# Isolated per-user install, same-version upgrade, and uninstall verification.
# This deliberately leaves its evidence and a retained user-data sentinel.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$ApplicationSmokeScript
)
$ErrorActionPreference = 'Stop'
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
if ($ApplicationSmokeScript) { $ApplicationSmokeScript = (Resolve-Path -LiteralPath $ApplicationSmokeScript).Path }
$output = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\')
if ([IO.Path]::GetPathRoot($output).TrimEnd('\') -eq $output) { throw 'An evidence directory is required, not a drive root.' }
if ((Test-Path -LiteralPath $output) -and (Get-ChildItem -LiteralPath $output -Force | Select-Object -First 1)) {
    throw "Output directory must be new or empty: $output"
}
$registryPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SpectraAnalyzer.Desktop.Standalone_is1'
if (Test-Path -LiteralPath $registryPath) { throw 'An existing standalone installation must not be replaced by an isolated test.' }
$shortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Spectra Analyzer (Standalone).lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Spectra Analyzer (Standalone).lnk')
)
foreach ($path in $shortcutPaths) {
    if (Test-Path -LiteralPath $path) { throw "An existing shortcut must not be replaced by this test: $path" }
}
New-Item -ItemType Directory -Path $output -Force | Out-Null
$install = [IO.Path]::GetFullPath((Join-Path $output 'installed app'))
$unowned = [IO.Path]::GetFullPath((Join-Path $output 'unowned directory'))
foreach ($target in @($install,$unowned)) {
    if (-not $target.StartsWith($output + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Test target escapes evidence directory: $target" }
}
$executable = Join-Path $install 'SpectraAnalyzer.exe'
$report = [ordered]@{status='running';installer=$installerPath;installerSha256=(Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash;installDirectory=$install;checks=[ordered]@{};errors=@()}

function Get-PreferencesFingerprint {
    $key = 'HKCU:\Software\SpectraAnalyzer\SpectraAnalyzer'
    if (-not (Test-Path -LiteralPath $key)) { return 'ABSENT' }
    $records = foreach ($item in @((Get-Item -LiteralPath $key)) + @(Get-ChildItem -LiteralPath $key -Recurse)) {
        foreach ($name in @($item.GetValueNames() | Sort-Object)) {
            [ordered]@{path=$item.Name;name=$name;kind=[string]$item.GetValueKind($name);value=$item.GetValue($name)}
        }
    }
    ConvertTo-Json -InputObject @($records) -Depth 12 -Compress
}

function Invoke-Setup([string[]]$Arguments, [string]$Name) {
    $quoted = foreach ($argument in $Arguments) {
        if ($argument.Contains('"')) { throw 'Installer arguments cannot contain quotes.' }
        '"' + $argument + '"'
    }
    $process = Start-Process -FilePath $installerPath -ArgumentList $quoted -WindowStyle Hidden -PassThru -Wait
    $report.checks[$Name + 'ExitCode'] = $process.ExitCode
    return $process.ExitCode
}

function Assert-Condition([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$beforePreferences = Get-PreferencesFingerprint
$existingFiles = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\SpectraAnalyzer\SpectraAnalyzer.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\SpectraAnalyzer\launcher-install.json'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Spectra Analyzer.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Spectra Analyzer.lnk')
)
$beforeExisting = @{}
foreach ($file in $existingFiles) {
    $beforeExisting[$file] = if (Test-Path -LiteralPath $file -PathType Leaf) { (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash } else { 'ABSENT' }
}
try {
    New-Item -ItemType Directory -Path $unowned -Force | Out-Null
    $unownedFile = Join-Path $unowned 'keep.txt'
    Set-Content -LiteralPath $unownedFile -Value 'User-owned directory must remain unchanged.' -Encoding UTF8
    $unownedHash = (Get-FileHash -LiteralPath $unownedFile -Algorithm SHA256).Hash
    $rejected = Invoke-Setup @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOICONS',('/DIR=' + $unowned),('/LOG=' + (Join-Path $output 'rejected-target.log'))) 'rejectedTarget'
    Assert-Condition ($rejected -ne 0) 'Installer accepted an unowned nonempty directory.'
    Assert-Condition (((Get-FileHash -LiteralPath $unownedFile -Algorithm SHA256).Hash -eq $unownedHash) -and @(Get-ChildItem -LiteralPath $unowned -Force).Count -eq 1) 'Unowned target was modified.'
    Assert-Condition (-not (Test-Path -LiteralPath $registryPath)) 'Rejected install created application registration.'
    $report.checks.unownedDirectoryRejected = $true

    $setupArguments = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/CLOSEAPPLICATIONS','/TASKS=desktopicon',('/DIR=' + $install))
    $installed = Invoke-Setup ($setupArguments + @('/LOG=' + (Join-Path $output 'install.log'))) 'install'
    Assert-Condition ($installed -eq 0) 'Installer failed.'
    Assert-Condition (Test-Path -LiteralPath $executable -PathType Leaf) 'Installed EXE is missing.'
    Assert-Condition (Test-Path -LiteralPath (Join-Path $install 'standalone-owner.ini') -PathType Leaf) 'Installation ownership marker is missing.'
    Assert-Condition (Test-Path -LiteralPath $registryPath) 'Per-user uninstall registration is missing.'
    $registration = Get-ItemProperty -LiteralPath $registryPath
    Assert-Condition ($registration.InstallLocation.TrimEnd('\') -eq $install) 'Uninstall registration points elsewhere.'
    $report.checks.installedExeSha256 = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash

    Add-Type -Path (Join-Path $PSScriptRoot 'ShellProperties.cs')
    $shell = New-Object -ComObject WScript.Shell
    $shortcutDetails = foreach ($path in $shortcutPaths) {
        Assert-Condition (Test-Path -LiteralPath $path -PathType Leaf) "Shortcut is missing: $path"
        $shortcut = $shell.CreateShortcut($path)
        $appId = [SpectraShellProperties]::ReadAppId($path)
        Assert-Condition ($shortcut.TargetPath -eq $executable -and $shortcut.WorkingDirectory -eq $install -and $shortcut.Arguments -eq '' -and $shortcut.IconLocation -eq "$executable,0" -and $appId -eq 'SpectraAnalyzer.Desktop') "Shortcut properties are incorrect: $path"
        [ordered]@{path=$path;target=$shortcut.TargetPath;workingDirectory=$shortcut.WorkingDirectory;icon=$shortcut.IconLocation;appId=$appId}
    }
    $report.checks.shortcuts = @($shortcutDetails)

    if ($ApplicationSmokeScript) {
        $smokeOutput = Join-Path $output 'installed-exe-smoke'
        New-Item -ItemType Directory -Path $smokeOutput -Force | Out-Null
        $smokeArguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $ApplicationSmokeScript + '"'),
            '-Executable',('"' + $executable + '"'),'-OutputDirectory',('"' + $smokeOutput + '"'))
        $smokeProcess = Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList $smokeArguments `
            -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput (Join-Path $smokeOutput 'driver.stdout.log') `
            -RedirectStandardError (Join-Path $smokeOutput 'driver.stderr.log')
        Assert-Condition ($smokeProcess.ExitCode -eq 0) 'Installed application startup probe failed.'
        $smokeReport = Get-Content -LiteralPath (Join-Path $smokeOutput 'production-exe-smoke.json') -Raw | ConvertFrom-Json
        Assert-Condition ($smokeReport.status -eq 'passed') 'Installed application startup did not pass.'
        $report.checks.installedApplicationStartup = $smokeReport
    }

    $sentinel = Join-Path $install 'user project.spectra'
    Set-Content -LiteralPath $sentinel -Value 'User-created project sentinel; installer must preserve this file.' -Encoding UTF8
    $sentinelHash = (Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash
    $upgraded = Invoke-Setup ($setupArguments + @('/LOG=' + (Join-Path $output 'upgrade.log'))) 'upgrade'
    Assert-Condition ($upgraded -eq 0) 'Same-version upgrade failed.'
    Assert-Condition ((Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash -eq $sentinelHash) 'Upgrade changed user data.'
    Assert-Condition ((Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash -eq $report.checks.installedExeSha256) 'Upgrade installed a different EXE.'
    $report.checks.upgradePreservedUserData = $true

    $uninstaller = Join-Path $install 'unins000.exe'
    Assert-Condition (Test-Path -LiteralPath $uninstaller -PathType Leaf) 'Owned uninstaller is missing.'
    $uninstall = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('"/LOG=' + (Join-Path $output 'uninstall.log') + '"')) -WindowStyle Hidden -PassThru -Wait
    $report.checks.uninstallExitCode = $uninstall.ExitCode
    Assert-Condition ($uninstall.ExitCode -eq 0) 'Uninstaller failed.'
    Assert-Condition (-not (Test-Path -LiteralPath $executable)) 'Uninstall left the installed application EXE.'
    Assert-Condition (-not (Test-Path -LiteralPath $registryPath)) 'Uninstall left its registration.'
    foreach ($path in $shortcutPaths) { Assert-Condition (-not (Test-Path -LiteralPath $path)) "Uninstall left a shortcut: $path" }
    Assert-Condition ((Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash -eq $sentinelHash) 'Uninstall removed or changed user data.'
    $remaining = @(Get-ChildItem -LiteralPath $install -Force)
    Assert-Condition ($remaining.Count -eq 1 -and $remaining[0].FullName -eq $sentinel) 'Unexpected installed files remain after uninstall.'
    $report.checks.uninstallPreservedUserData = $true
    $report.status = 'passed'
} catch {
    $report.status = 'failed'
    $report.errors += $_.Exception.Message
} finally {
    $report.checks.preferencesUnchanged = (Get-PreferencesFingerprint) -eq $beforePreferences
    $report.checks.developmentInstallationUnchanged = $true
    foreach ($file in $existingFiles) {
        $after = if (Test-Path -LiteralPath $file -PathType Leaf) { (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash } else { 'ABSENT' }
        if ($after -ne $beforeExisting[$file]) { $report.checks.developmentInstallationUnchanged = $false }
    }
    if (-not $report.checks.preferencesUnchanged -or -not $report.checks.developmentInstallationUnchanged) {
        $report.status = 'failed'
        $report.errors += 'Existing preferences or development installation changed.'
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $output 'installer-validation.json') -Encoding UTF8
}
$report | ConvertTo-Json -Depth 8
if ($report.status -ne 'passed') { exit 1 }
