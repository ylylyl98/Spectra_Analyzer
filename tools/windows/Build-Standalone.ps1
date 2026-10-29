# Build a self-contained Windows x64 application; tests remain a separate lane.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$BuildPython,
    [string]$InnoCompiler,
    [ValidateSet('Release','Preview','Installer')][string]$Mode = 'Release',
    [string]$CacheDirectory,
    [string]$BuildDate = (Get-Date -Format 'yyyy-MM-dd'),
    [string]$DiagnosticScript
)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..')).TrimEnd('\')
$output = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\')
$cached = $Mode -ne 'Release'
$totalTimer = [Diagnostics.Stopwatch]::StartNew()
$stages = New-Object 'System.Collections.Generic.List[object]'
$cacheLock = $null

function Assert-SeparatePaths([string]$First, [string]$Second) {
    if ($First.Equals($Second, [StringComparison]::OrdinalIgnoreCase) -or
        $First.StartsWith($Second + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $Second.StartsWith($First + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Build paths must not overlap: $First and $Second"
    }
}
function Assert-NoReparsePath([string]$Path) {
    $current = $Path
    while ($current) {
        if ((Test-Path -LiteralPath $current) -and
            ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Build output/cache paths must not use reparse points: $current"
        }
        $current = Split-Path -Parent $current
    }
}
function Get-Identity($Value) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 6 -Compress))
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() }
    finally { $hasher.Dispose() }
}
function Get-EnvironmentSnapshot {
    $json = & $BuildPython (Join-Path $PSScriptRoot 'standalone_build_environment.py') $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect installed build dependencies.' }
    return $json | ConvertFrom-Json
}
function Get-SourceHashes {
    $qmlPrefix = (Join-Path $repository 'spectra_app\qml') + '\'
    $sources = @((Get-Item -LiteralPath (Join-Path $repository 'run_qt.py'))) +
        @(Get-ChildItem -LiteralPath (Join-Path $repository 'spectra_app') -Recurse -File |
            Where-Object { $_.Extension -in @('.py','.ico','.pal') -or
                $_.FullName.StartsWith($qmlPrefix, [StringComparison]::OrdinalIgnoreCase) }) +
        @(Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object {
            $_.Name -in @('Build-Standalone.ps1','standalone_build_environment.py','SpectraAnalyzer.spec',
                'SpectraAnalyzer.iss','requirements-standalone.txt','standalone-owner.ini','THIRD_PARTY_NOTICES.txt') }) +
        @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'licenses') -File)
    foreach ($source in ($sources | Sort-Object FullName)) {
        [ordered]@{path=$source.FullName.Substring($repository.Length + 1); sha256=(Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash}
    }
}
function Assert-StableSources {
    if ((Get-Identity $sourceHashes) -ne (Get-Identity @(Get-SourceHashes)) -or
        ($diagnostic -and $diagnostic.sha256 -ne (Get-FileHash -LiteralPath $diagnostic.path -Algorithm SHA256).Hash)) {
        throw 'Build sources changed during packaging; build again from stable source.'
    }
}
function Invoke-LoggedTool([string]$Executable, [string[]]$Arguments, [string]$Name) {
    # Literal quoting also supports Windows PowerShell 5.1 Start-Process.
    $quoted = foreach ($argument in $Arguments) {
        if ($argument.Contains('"')) { throw 'Build arguments cannot contain quotation marks.' }
        '"' + $argument.TrimEnd('\') + '"'
    }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    Write-Host "Starting $Name..."
    $process = Start-Process -FilePath $Executable -ArgumentList $quoted -WorkingDirectory $repository `
        -WindowStyle Hidden -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $output "$Name.stdout.log") `
        -RedirectStandardError (Join-Path $output "$Name.stderr.log")
    $stages.Add([ordered]@{name=$Name; status='completed'; seconds=[math]::Round($timer.Elapsed.TotalSeconds,3)})
    if ($process.ExitCode -ne 0) { throw "$Name failed ($($process.ExitCode)); see $output\$Name.stderr.log" }
    Write-Host ("Finished {0} in {1:N1}s." -f $Name,$timer.Elapsed.TotalSeconds)
}

if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) { throw "Missing build tool: $BuildPython" }
$BuildPython = [IO.Path]::GetFullPath($BuildPython)
if ($Mode -ne 'Preview') {
    if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
        throw 'InnoCompiler is required for Release and Installer modes.'
    }
    $InnoCompiler = [IO.Path]::GetFullPath($InnoCompiler)
}
if ($cached -and -not $CacheDirectory) { throw 'CacheDirectory is required for Preview and Installer modes.' }
if (-not $cached -and $CacheDirectory) { throw 'Release is always clean; use Preview or Installer with CacheDirectory.' }
if ($BuildDate -notmatch '^\d{4}-\d{2}-\d{2}$') { throw 'BuildDate must use yyyy-MM-dd.' }
[datetime]::ParseExact($BuildDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture) | Out-Null
Assert-SeparatePaths $output $repository
Assert-NoReparsePath $output
if ((Test-Path -LiteralPath $output) -and
    ((-not (Test-Path -LiteralPath $output -PathType Container)) -or
     (Get-ChildItem -LiteralPath $output -Force | Select-Object -First 1))) {
    throw "Output directory must be new or empty: $output"
}
if ($cached) {
    $cache = [IO.Path]::GetFullPath($CacheDirectory).TrimEnd('\')
    Assert-SeparatePaths $cache $repository
    Assert-SeparatePaths $cache $output
    Assert-NoReparsePath $cache
}

$environmentNames = @('PATH','PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','PYINSTALLER_CONFIG_DIR','SPECTRA_DIAGNOSTICS_SCRIPT')
$savedEnvironment = @{}
foreach ($name in $environmentNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    # Prevent native DLL discovery through unrelated tools on the caller's PATH.
    $env:PYTHONPATH = $null
    $env:PYTHONHOME = $null
    $env:VIRTUAL_ENV = $null
    $env:PYINSTALLER_CONFIG_DIR = $null
    $env:SPECTRA_DIAGNOSTICS_SCRIPT = $null
    $windowsSearchPaths = @((Join-Path $env:WINDIR 'System32'), $env:WINDIR)
    $env:PATH = $windowsSearchPaths -join ';'
    $versionSource = Join-Path $repository 'spectra_app\app_version.py'
    # Keep Python source out of native arguments for Windows PowerShell 5.1.
    $version = @'
import ast, sys
with open(sys.argv[1], encoding="utf-8-sig") as source:
    tree = ast.parse(source.read())
print(next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "APP_VERSION" for t in n.targets)))
'@ | & $BuildPython - $versionSource
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw 'APP_VERSION must be a numeric major.minor.patch string.' }
    $runtimeJson = @'
import json, platform, struct, sys
print(json.dumps(dict(version=platform.python_version(), bits=struct.calcsize("P") * 8, prefix=sys.prefix, base_prefix=sys.base_prefix)))
'@ | & $BuildPython -
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the build Python environment.' }
    $runtime = $runtimeJson | ConvertFrom-Json
    if ($runtime.version -ne '3.14.0' -or $runtime.bits -ne 64) {
        throw 'This build lock requires CPython 3.14.0 x64 in an isolated environment.'
    }
    if ($runtime.prefix -eq $runtime.base_prefix) {
        throw 'BuildPython must belong to an isolated virtual environment outside the source checkout.'
    }
    Assert-SeparatePaths ([IO.Path]::GetFullPath($runtime.prefix).TrimEnd('\')) $repository
    $toolDirectories = @($runtime.prefix, $runtime.base_prefix, (Split-Path -Parent $BuildPython))
    if ($Mode -ne 'Preview') { $toolDirectories += Split-Path -Parent $InnoCompiler }
    foreach ($toolDirectory in $toolDirectories) {
        $toolDirectory = [IO.Path]::GetFullPath($toolDirectory).TrimEnd('\')
        Assert-SeparatePaths $repository $toolDirectory
        Assert-SeparatePaths $output $toolDirectory
        if ($cached) { Assert-SeparatePaths $cache $toolDirectory }
    }
    $buildSearchPaths = @((Split-Path $BuildPython -Parent), $runtime.base_prefix,
        (Join-Path $runtime.base_prefix 'DLLs')) + $windowsSearchPaths
    $env:PATH = $buildSearchPaths -join ';'
    New-Item -ItemType Directory -Path $output -Force | Out-Null
    $requirements = Join-Path $PSScriptRoot 'requirements-standalone.txt'
    $spec = Join-Path $PSScriptRoot 'SpectraAnalyzer.spec'
    $runtimeIdentity = [ordered]@{python=$BuildPython.ToLowerInvariant(); sha256=(Get-FileHash -LiteralPath $BuildPython -Algorithm SHA256).Hash; runtime=$runtime;
        lockSha256=(Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash;
        inspectorSha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'standalone_build_environment.py') -Algorithm SHA256).Hash}
    $dependencyStampReused = $false
    if ($cached) {
        $owner = Join-Path $cache 'standalone-build-cache.txt'
        Assert-NoReparsePath $owner
        if (Test-Path -LiteralPath $cache) {
            if (-not (Test-Path -LiteralPath $cache -PathType Container) -or
                ((Get-ChildItem -LiteralPath $cache -Force | Select-Object -First 1) -and
                 ((-not (Test-Path -LiteralPath $owner -PathType Leaf)) -or
                  (Get-Content -Raw -LiteralPath $owner).Trim() -ne 'SpectraAnalyzer standalone build cache v1'))) {
                throw "Cache directory must be empty or owned by this builder: $cache"
            }
        }
        New-Item -ItemType Directory -Path $cache -Force | Out-Null
        $lockPath = Join-Path $cache 'build.lock'
        Assert-NoReparsePath $lockPath
        try { $cacheLock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
        catch { throw "Build cache is already in use: $cache" }
        'SpectraAnalyzer standalone build cache v1' | Set-Content -LiteralPath $owner -Encoding ASCII
        $stampPath = Join-Path $cache ('dependencies-' + (Get-Identity $runtimeIdentity) + '.json')
        Assert-NoReparsePath $stampPath
        $snapshot = Get-EnvironmentSnapshot
        if ($snapshot.lockedVersionsMatch -and (Test-Path -LiteralPath $stampPath -PathType Leaf)) {
            try { $stamp = Get-Content -Raw -LiteralPath $stampPath | ConvertFrom-Json }
            catch { $stamp = $null }
            $dependencyStampReused = $stamp -and $stamp.fingerprint -eq $snapshot.fingerprint
        }
    }
    if ($dependencyStampReused) {
        Write-Host 'Reusing dependency validation: locked versions and installed metadata still match.'
        $stages.Add([ordered]@{name='dependencies';status='reused';seconds=0})
        $stages.Add([ordered]@{name='dependency-check';status='reused';seconds=0})
    } else {
        Invoke-LoggedTool $BuildPython @('-m','pip','install','--disable-pip-version-check','-r',$requirements) 'dependencies'
        Invoke-LoggedTool $BuildPython @('-m','pip','check') 'dependency-check'
        $snapshot = Get-EnvironmentSnapshot
        if (-not $snapshot.lockedVersionsMatch) { throw 'Installed build dependencies do not match the exact lock.' }
        if ($cached) { $snapshot | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $stampPath -Encoding UTF8 }
    }
    $diagnostic = $null
    if ($DiagnosticScript) {
        $env:SPECTRA_DIAGNOSTICS_SCRIPT = (Resolve-Path -LiteralPath $DiagnosticScript).Path
        $diagnostic = [ordered]@{path=$env:SPECTRA_DIAGNOSTICS_SCRIPT;sha256=(Get-FileHash -LiteralPath $env:SPECTRA_DIAGNOSTICS_SCRIPT -Algorithm SHA256).Hash}
    }
    $sourceHashes = @(Get-SourceHashes)
    $workReused = $false
    $cacheIdentity = $null
    if ($cached) {
        $cacheIdentity = Get-Identity ([ordered]@{schema=1;repository=$repository.ToLowerInvariant();runtime=$runtimeIdentity;
            environment=$snapshot.fingerprint;specSha256=(Get-FileHash -LiteralPath $spec -Algorithm SHA256).Hash;diagnostic=$diagnostic;
            qmlResources=@($sourceHashes | Where-Object { $_.path.StartsWith('spectra_app\qml\', [StringComparison]::OrdinalIgnoreCase) })})
        $cacheEntry = Join-Path $cache $cacheIdentity
        $work = Join-Path $cacheEntry 'work'
        $env:PYINSTALLER_CONFIG_DIR = Join-Path $cacheEntry 'config'
        Assert-NoReparsePath $work
        Assert-NoReparsePath $env:PYINSTALLER_CONFIG_DIR
        $workReused = Test-Path -LiteralPath (Join-Path $work 'SpectraAnalyzer\Analysis-00.toc') -PathType Leaf
        Write-Host "PyInstaller cache $cacheIdentity (existing analysis: $workReused)."
    } else {
        $work = Join-Path $output 'work'
        $env:PYINSTALLER_CONFIG_DIR = Join-Path $output 'pyinstaller-config'
    }
    $arguments = @('-m','PyInstaller','--noconfirm')
    if (-not $cached) { $arguments += '--clean' }
    $arguments += @('--distpath',(Join-Path $output 'bundle'),'--workpath',$work,$spec)
    Invoke-LoggedTool $BuildPython $arguments 'pyinstaller'
    Assert-StableSources
    $bundle = Join-Path $output 'bundle\SpectraAnalyzer'
    $executable = Join-Path $bundle 'SpectraAnalyzer.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'Frozen app executable is missing.' }
    $artifacts = @()
    if ($Mode -eq 'Preview') { $artifacts += $executable }
    $installer = $null
    $inno = $null
    if ($Mode -ne 'Preview') {
        Invoke-LoggedTool $InnoCompiler @('/Qp',('/DBundleDir=' + $bundle),('/DArtifactDir=' + $output),
            ('/DBuildDate=' + $BuildDate),('/DBuildVersion=' + $version), (Join-Path $PSScriptRoot 'SpectraAnalyzer.iss')) 'installer'
        $installer = Join-Path $output "SpectraAnalyzer-$version-Setup.exe"
        $artifacts += $installer
        $inno = [ordered]@{version=(Get-Item -LiteralPath $InnoCompiler).VersionInfo.FileVersion;sha256=(Get-FileHash -LiteralPath $InnoCompiler -Algorithm SHA256).Hash}
    }
    if ($Mode -eq 'Release') {
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $portable = Join-Path $output "SpectraAnalyzer-$version-win64.zip"
        Compress-Archive -LiteralPath $bundle -DestinationPath $portable -CompressionLevel Optimal
        $stages.Add([ordered]@{name='portable-zip';status='completed';seconds=[math]::Round($timer.Elapsed.TotalSeconds,3)})
        $artifacts += $portable
    }
    Assert-StableSources
    $files = foreach ($artifact in $artifacts) {
        $file = Get-Item -LiteralPath $artifact
        [ordered]@{name=$file.FullName.Substring($output.Length + 1);bytes=$file.Length;sha256=(Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash}
    }
    $stages.Add([ordered]@{name='total';status='completed';seconds=[math]::Round($totalTimer.Elapsed.TotalSeconds,3)})
    [ordered]@{mode=$Mode;buildDate=$BuildDate;installerVersion=$version;python=$runtime.version;architecture='x64';
        nativeDependencySearchPaths=@($buildSearchPaths);pythonEnvironmentIsolated=$true;innoCompiler=$inno;
        cache=[ordered]@{enabled=$cached;identity=$cacheIdentity;directory=$(if ($cached) {$cacheEntry} else {$null});workReused=$workReused;dependencyStampReused=[bool]$dependencyStampReused};
        stages=@($stages.ToArray());diagnostic=$diagnostic;createdAt=(Get-Date).ToString('o');dependencies=@($snapshot.dependencies);artifacts=@($files);sources=$sourceHashes} |
        ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $output 'build-manifest.json') -Encoding UTF8
    $files | ForEach-Object { '{0} *{1}' -f $_.sha256,$_.name } |
        Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ASCII
    if ($installer) {
        ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + [IO.Path]::GetFileName($installer)) |
            Set-Content -LiteralPath ($installer + '.sha256') -Encoding ASCII
    }
    $files | ConvertTo-Json
} finally {
    if ($cacheLock) { $cacheLock.Dispose() }
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name,$savedEnvironment[$name], 'Process') }
}
