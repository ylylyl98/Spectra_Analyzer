# Build and install the current checkout's per-user, pinnable GUI entry point.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$python = Join-Path $repository '.venv\Scripts\python.exe'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$icon = Join-Path $repository 'spectra_app\resources\spectra_analyzer.ico'
$install = Join-Path $env:LOCALAPPDATA 'Programs\SpectraAnalyzer'
$executable = Join-Path $install 'SpectraAnalyzer.exe'
$build = Join-Path $repository 'build\windows-launcher'
$shortcuts = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Spectra Analyzer.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Spectra Analyzer.lnk')
)
foreach ($required in @($python, $compiler, $icon, (Join-Path $repository 'run_qt.py'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing required file: $required" }
}
$identitySource = Get-Content -LiteralPath (Join-Path $repository 'spectra_app\app_identity.py') -Raw
$appId = [regex]::Match($identitySource, "(?m)^APP_ID = '([^']+)'\r?$").Groups[1].Value
if (-not $appId) { throw 'Cannot read the stable application identity.' }
Add-Type -Path (Join-Path $PSScriptRoot 'ShellProperties.cs')
$shell = New-Object -ComObject WScript.Shell
# Refuse to replace a same-named shortcut or installation owned by another app.
foreach ($linkPath in $shortcuts) {
    if (Test-Path -LiteralPath $linkPath) {
        $old = $shell.CreateShortcut($linkPath)
        if ($old.TargetPath -ne $executable -or [SpectraShellProperties]::ReadAppId($linkPath) -ne $appId) {
            throw "A different shortcut already exists: $linkPath"
        }
    }
}
$manifestPath = Join-Path $install 'launcher-install.json'
if (Test-Path -LiteralPath $install) {
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "Unrecognized installation directory: $install" }
    $prior = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($prior.appId -ne $appId -or $prior.executable -ne $executable) { throw "Unrecognized installation manifest: $manifestPath" }
}
New-Item -ItemType Directory -Path $build -Force | Out-Null
$escapedRepo = $repository.Replace('"', '""')
$settings = "internal static class BuildSettings { internal const string Repository = @`"$escapedRepo`"; internal const string AppId = `"$appId`"; }"
Set-Content -LiteralPath (Join-Path $build 'BuildSettings.cs') -Value $settings -Encoding UTF8
$builtExe = Join-Path $build 'SpectraAnalyzer.exe'
& $compiler /nologo /target:winexe /optimize+ /platform:anycpu /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$builtExe" (Join-Path $PSScriptRoot 'Launcher.cs') (Join-Path $build 'BuildSettings.cs')
if ($LASTEXITCODE -ne 0) { throw "Launcher compilation failed ($LASTEXITCODE)." }
New-Item -ItemType Directory -Path $install -Force | Out-Null
Copy-Item -LiteralPath $builtExe -Destination $executable -Force
Copy-Item -LiteralPath $icon -Destination (Join-Path $install 'spectra_analyzer.ico') -Force
$verifiedLinks = foreach ($linkPath in $shortcuts) {
    $link = $shell.CreateShortcut($linkPath)
    $link.TargetPath = $executable
    $link.Arguments = ''
    $link.WorkingDirectory = $repository
    $link.IconLocation = "$executable,0"
    $link.Description = 'Spectra Analyzer'
    $link.Save()
    $readId = [SpectraShellProperties]::WriteAppId($linkPath, $appId)
    $read = $shell.CreateShortcut($linkPath)
    if ($readId -ne $appId -or $read.TargetPath -ne $executable -or $read.IconLocation -ne "$executable,0" -or $read.WorkingDirectory -ne $repository -or $read.Arguments -ne '') {
        throw "Shortcut readback verification failed: $linkPath"
    }
    [ordered]@{ path = $linkPath; target = $read.TargetPath; arguments = $read.Arguments;
        workingDirectory = $read.WorkingDirectory; icon = $read.IconLocation; appId = $readId }
}
$manifest = [ordered]@{
    appId = $appId; executable = $executable; repository = $repository;
    python = (Join-Path $repository '.venv\Scripts\pythonw.exe');
    icon = (Join-Path $install 'spectra_analyzer.ico');
    iconSha256 = (Get-FileHash -LiteralPath $icon -Algorithm SHA256).Hash;
    executableSha256 = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash;
    installedAt = (Get-Date).ToString('o'); shortcuts = @($verifiedLinks)
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$manifest | ConvertTo-Json -Depth 5
