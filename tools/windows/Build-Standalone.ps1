# Build a self-contained Windows x64 application and a per-user installer.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$BuildPython,
    [Parameter(Mandatory=$true)][string]$InnoCompiler,
    [string]$BuildDate = (Get-Date -Format 'yyyy-MM-dd'),
    [string]$DiagnosticScript
)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$output = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\')
foreach ($required in @($BuildPython, $InnoCompiler)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing build tool: $required" }
}
if ($BuildDate -notmatch '^\d{4}-\d{2}-\d{2}$') { throw 'BuildDate must use yyyy-MM-dd.' }
[datetime]::ParseExact($BuildDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture) | Out-Null
$environmentNames = @('PATH','PYTHONPATH','PYTHONHOME','VIRTUAL_ENV')
$savedEnvironment = @{}
foreach ($name in $environmentNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    # Native dependency discovery must not resolve DLLs from unrelated tools on
    # the caller's PATH (for example an incompatible Poppler ICU library).
    $env:PYTHONPATH = $null
    $env:PYTHONHOME = $null
    $env:VIRTUAL_ENV = $null
    $windowsSearchPaths = @((Join-Path $env:WINDIR 'System32'), $env:WINDIR)
    $env:PATH = $windowsSearchPaths -join ';'
$versionSource = Join-Path $repository 'spectra_app\app_version.py'
$version = & $BuildPython -c 'import ast,sys; tree=ast.parse(open(sys.argv[1], encoding="utf-8").read()); print(next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="APP_VERSION" for t in n.targets)))' $versionSource
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw 'APP_VERSION must be a numeric major.minor.patch string.' }
if (Test-Path -LiteralPath $output) {
    if (Get-ChildItem -LiteralPath $output -Force | Select-Object -First 1) {
        throw "Output directory must be new or empty: $output"
    }
}
New-Item -ItemType Directory -Path $output -Force | Out-Null

function Invoke-LoggedTool([string]$Executable, [string[]]$Arguments, [string]$Name) {
    # All build arguments are literal paths/options. Reject quote injection and
    # quote each argument for Start-Process (also compatible with PowerShell 5).
    $quoted = foreach ($argument in $Arguments) {
        if ($argument.Contains('"')) { throw 'Build arguments cannot contain quotation marks.' }
        '"' + $argument.TrimEnd('\') + '"'
    }
    $process = Start-Process -FilePath $Executable -ArgumentList $quoted -WorkingDirectory $repository `
        -WindowStyle Hidden -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $output "$Name.stdout.log") `
        -RedirectStandardError (Join-Path $output "$Name.stderr.log")
    if ($process.ExitCode -ne 0) { throw "$Name failed ($($process.ExitCode)); see $output\$Name.stderr.log" }
}

$runtimeJson = & $BuildPython -c 'import json,platform,struct,sys; print(json.dumps(dict(version=platform.python_version(),bits=struct.calcsize("P")*8,prefix=sys.prefix,base_prefix=sys.base_prefix)))'
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the build Python environment.' }
$runtime = $runtimeJson | ConvertFrom-Json
if ($runtime.version -ne '3.14.0' -or $runtime.bits -ne 64) {
    throw 'This build lock requires CPython 3.14.0 x64 in an isolated environment.'
}
if ($runtime.prefix -eq $runtime.base_prefix -or
    $runtime.prefix.StartsWith($repository + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'BuildPython must belong to an isolated virtual environment outside the source checkout.'
}
$buildSearchPaths = @((Split-Path ([IO.Path]::GetFullPath($BuildPython)) -Parent),
    $runtime.base_prefix, (Join-Path $runtime.base_prefix 'DLLs')) + $windowsSearchPaths
$env:PATH = $buildSearchPaths -join ';'
Invoke-LoggedTool $BuildPython @('-m','pip','install','--disable-pip-version-check','-r',(Join-Path $PSScriptRoot 'requirements-standalone.txt')) 'dependencies'
Invoke-LoggedTool $BuildPython @('-m','pip','check') 'dependency-check'

$previousDiagnostic = $env:SPECTRA_DIAGNOSTICS_SCRIPT
try {
    if ($DiagnosticScript) {
        $env:SPECTRA_DIAGNOSTICS_SCRIPT = (Resolve-Path -LiteralPath $DiagnosticScript).Path
    } else { Remove-Item Env:SPECTRA_DIAGNOSTICS_SCRIPT -ErrorAction SilentlyContinue }
    Invoke-LoggedTool $BuildPython @('-m','PyInstaller','--noconfirm','--clean',
        '--distpath',(Join-Path $output 'bundle'),'--workpath',(Join-Path $output 'work'),
        (Join-Path $PSScriptRoot 'SpectraAnalyzer.spec')) 'pyinstaller'
} finally {
    if ($null -ne $previousDiagnostic) { $env:SPECTRA_DIAGNOSTICS_SCRIPT = $previousDiagnostic }
    else { Remove-Item Env:SPECTRA_DIAGNOSTICS_SCRIPT -ErrorAction SilentlyContinue }
}
$bundle = Join-Path $output 'bundle\SpectraAnalyzer'
if (-not (Test-Path -LiteralPath (Join-Path $bundle 'SpectraAnalyzer.exe') -PathType Leaf)) { throw 'Frozen app executable is missing.' }
Invoke-LoggedTool $InnoCompiler @('/Qp',('/DBundleDir=' + $bundle),('/DArtifactDir=' + $output),
    ('/DBuildDate=' + $BuildDate),('/DBuildVersion=' + $version),
    (Join-Path $PSScriptRoot 'SpectraAnalyzer.iss')) 'installer'

$installer = Join-Path $output "SpectraAnalyzer-$version-Setup.exe"
$portable = Join-Path $output "SpectraAnalyzer-$version-win64.zip"
Compress-Archive -LiteralPath $bundle -DestinationPath $portable -CompressionLevel Optimal
$files = foreach ($artifact in @($installer,$portable)) {
    $file = Get-Item -LiteralPath $artifact
    [ordered]@{name=$file.Name; bytes=$file.Length; sha256=(Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash}
}
$sources = @((Get-Item -LiteralPath (Join-Path $repository 'run_qt.py'))) +
    @(Get-ChildItem -LiteralPath (Join-Path $repository 'spectra_app') -Recurse -File |
        Where-Object { $_.Extension -in @('.py','.ico','.pal') }) +
    @(Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object { $_.Name -in @('Build-Standalone.ps1','SpectraAnalyzer.spec','SpectraAnalyzer.iss','requirements-standalone.txt','standalone-owner.ini','THIRD_PARTY_NOTICES.txt') }) +
    @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'licenses') -File)
$sourceHashes = foreach ($source in ($sources | Sort-Object FullName)) {
    [ordered]@{path=$source.FullName.Substring($repository.Length + 1); sha256=(Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash}
}
$dependencies = & $BuildPython -m pip freeze --all
if ($LASTEXITCODE -ne 0) { throw 'Could not record build environment.' }
[ordered]@{buildDate=$BuildDate; installerVersion=$version; python=$runtime.version; architecture='x64';
    nativeDependencySearchPaths=@($buildSearchPaths); pythonEnvironmentIsolated=$true;
    innoCompiler=[ordered]@{version=(Get-Item -LiteralPath $InnoCompiler).VersionInfo.FileVersion;sha256=(Get-FileHash -LiteralPath $InnoCompiler -Algorithm SHA256).Hash};
    createdAt=(Get-Date).ToString('o'); dependencies=@($dependencies); artifacts=@($files); sources=@($sourceHashes)} |
    ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output 'build-manifest.json') -Encoding UTF8
$files | ForEach-Object { '{0} *{1}' -f $_.sha256,$_.name } |
    Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ASCII
((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + [IO.Path]::GetFileName($installer)) |
    Set-Content -LiteralPath ($installer + '.sha256') -Encoding ASCII
$files | ConvertTo-Json
} finally {
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name,$savedEnvironment[$name], 'Process') }
}
