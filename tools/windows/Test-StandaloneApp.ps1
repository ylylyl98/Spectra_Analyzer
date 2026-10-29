# Process-level probe for the actual frozen EXE, outside its source checkout.
# Never pass a source-backed launcher here. This intentionally opens no project.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class SpectraOwnedWindowProbe {
    private delegate bool EnumWindowsCallback(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsCallback callback, IntPtr data);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int length);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder text, int length);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll", SetLastError = true)] private static extern IntPtr SendMessageTimeout(IntPtr window, uint message, UIntPtr wParam, IntPtr lParam, uint flags, uint timeout, out UIntPtr result);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool PostMessage(IntPtr window, uint message, UIntPtr wParam, IntPtr lParam);
    public static bool IsOwnedBy(IntPtr window, int processId) {
        uint owner;
        return GetWindowThreadProcessId(window, out owner) != 0 && owner == processId;
    }
    public static IntPtr FindMainWindow(int processId, out string title, out string className) {
        IntPtr found = IntPtr.Zero;
        string selectedTitle = "", selectedClass = "";
        EnumWindows((window, data) => {
            if (!IsOwnedBy(window, processId)) return true;
            var text = new StringBuilder(1024);
            var type = new StringBuilder(256);
            GetWindowText(window, text, text.Capacity);
            GetClassName(window, type, type.Capacity);
            string candidateTitle = text.ToString(), candidateClass = type.ToString();
            if (!candidateTitle.StartsWith("Spectra Analyzer", StringComparison.Ordinal) ||
                !candidateClass.StartsWith("Qt6", StringComparison.Ordinal) || !candidateClass.Contains("QWindow")) return true;
            found = window; selectedTitle = candidateTitle; selectedClass = candidateClass;
            return false;
        }, IntPtr.Zero);
        title = selectedTitle; className = selectedClass;
        return found;
    }
    public static bool IsResponsive(IntPtr window, int processId) {
        if (!IsOwnedBy(window, processId)) return false;
        UIntPtr result;
        return SendMessageTimeout(window, 0, UIntPtr.Zero, IntPtr.Zero, 2, 2000, out result) != IntPtr.Zero;
    }
    public static bool RequestClose(IntPtr window, int processId) {
        return IsOwnedBy(window, processId) && PostMessage(window, 0x10, UIntPtr.Zero, IntPtr.Zero);
    }
}
'@
$exePath = [IO.Path]::GetFullPath($Executable)
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) { throw "Missing EXE: $exePath" }
if ([IO.Path]::GetPathRoot($outputPath).TrimEnd('\') -eq $outputPath.TrimEnd('\')) { throw 'An evidence directory is required, not a drive root.' }
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
# The installer driver already redirects its logs into this directory.
$unexpected = @(Get-ChildItem -LiteralPath $outputPath -Force | Where-Object { $_.Name -notin @('driver.stdout.log','driver.stderr.log') })
if ($unexpected.Count) { throw "Output directory must be new or empty: $outputPath" }
$scratchPath = Join-Path $outputPath 'unrelated working directory'
New-Item -ItemType Directory -Path $scratchPath -Force | Out-Null

function Get-TextHash([string]$Text) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-', '') }
    finally { $algorithm.Dispose() }
}
function Get-PreferencesHash {
    $keyPath = 'HKCU:\Software\SpectraAnalyzer\SpectraAnalyzer'
    if (-not (Test-Path -LiteralPath $keyPath)) { return 'ABSENT' }
    $records = foreach ($key in @((Get-Item -LiteralPath $keyPath)) + @(Get-ChildItem -LiteralPath $keyPath -Recurse)) {
        foreach ($name in @($key.GetValueNames() | Sort-Object)) {
            [ordered]@{path=$key.Name;name=$name;kind=[string]$key.GetValueKind($name);value=$key.GetValue($name)}
        }
    }
    return Get-TextHash (ConvertTo-Json -InputObject @($records) -Depth 12 -Compress)
}
function Get-ExistingInstallHashes {
    $files = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\SpectraAnalyzer\SpectraAnalyzer.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\SpectraAnalyzer\launcher-install.json'),
        (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Spectra Analyzer.lnk'),
        (Join-Path ([Environment]::GetFolderPath('Programs')) 'Spectra Analyzer.lnk')
    )
    $hashes = [ordered]@{}
    foreach ($filePath in $files) {
        $hashes[$filePath] = if (Test-Path -LiteralPath $filePath -PathType Leaf) { (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash } else { 'ABSENT' }
    }
    return ConvertTo-Json -InputObject $hashes -Compress
}

$report = [ordered]@{status='initializing';executable=$exePath;sha256=(Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash;workingDirectory=$scratchPath;errors=@();forcedCleanup=$false}
$beforePreferences = Get-PreferencesHash
$beforeInstall = Get-ExistingInstallHashes
$environmentNames = @('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'QT_QPA_PLATFORM', 'PATH')
$savedEnvironment = @{}
foreach ($environmentName in $environmentNames) { $savedEnvironment[$environmentName] = [Environment]::GetEnvironmentVariable($environmentName, 'Process') }
$ownedProcess = $null
try {
    # This release probe is for a clean hosted runner. Never overwrite a real
    # user's settings: explicit QSettings IDs cannot be redirected by Qt flags.
    if ($beforePreferences -ne 'ABSENT') { throw 'Production smoke requires an unused SpectraAnalyzer preferences key.' }
    $env:PYTHONPATH = $null
    $env:PYTHONHOME = $null
    $env:VIRTUAL_ENV = $null
    $env:QT_QPA_PLATFORM = 'windows'
    $env:PATH = "$env:WINDIR\System32;$env:WINDIR"
    $ownedProcess = Start-Process -FilePath $exePath -WorkingDirectory $scratchPath -WindowStyle Hidden -PassThru
    $report.pid = $ownedProcess.Id
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $ownedWindow = [IntPtr]::Zero
    $ownedTitle = ''
    $ownedClass = ''
    while ([DateTime]::UtcNow -lt $deadline) {
        $ownedProcess.Refresh()
        if ($ownedProcess.HasExited) { throw "Production EXE exited during startup with code $($ownedProcess.ExitCode)." }
        $ownedWindow = [SpectraOwnedWindowProbe]::FindMainWindow($ownedProcess.Id, [ref]$ownedTitle, [ref]$ownedClass)
        if ($ownedWindow -ne [IntPtr]::Zero) { break }
        Start-Sleep -Milliseconds 200
    }
    $ownedProcess.Refresh()
    if ($ownedWindow -eq [IntPtr]::Zero) { throw 'No owned Qt main application window was created in 30 seconds.' }
    $report.nativeWindowHandle = $ownedWindow.ToInt64()
    $report.title = $ownedTitle
    $report.windowClass = $ownedClass
    $report.windowVisible = [SpectraOwnedWindowProbe]::IsWindowVisible($ownedWindow)
    if ($report.title -notlike 'Spectra Analyzer*') { throw "Unexpected owned window title: $($report.title)" }
    Start-Sleep -Milliseconds 1000
    $ownedProcess.Refresh()
    $report.responding = [SpectraOwnedWindowProbe]::IsResponsive($ownedWindow, $ownedProcess.Id)
    if (-not $report.responding) { throw 'Production window is not responding.' }
    if (-not [SpectraOwnedWindowProbe]::RequestClose($ownedWindow, $ownedProcess.Id)) { throw 'Production window did not accept a close request.' }
    if (-not $ownedProcess.WaitForExit(15000)) { throw 'Production EXE did not exit after its own main window was closed.' }
    $report.exitCode = $ownedProcess.ExitCode
    if ($ownedProcess.ExitCode -ne 0) { throw "Production EXE exit code $($ownedProcess.ExitCode)." }
    $report.cleanShutdown = $true
    $report.status = 'passed'
}
catch {
    $report.status = 'failed'
    $report.errors += $_.Exception.Message
}
finally {
    foreach ($environmentName in $environmentNames) { [Environment]::SetEnvironmentVariable($environmentName, $savedEnvironment[$environmentName], 'Process') }
    if ($null -ne $ownedProcess) {
        $ownedProcess.Refresh()
        if (-not $ownedProcess.HasExited) {
            # Only the precise process started above is eligible for cleanup.
            Stop-Process -Id $ownedProcess.Id -ErrorAction SilentlyContinue
            $report.forcedCleanup = $true
        }
    }
    # Only a key absent before this owned process started may be removed.
    if ($null -ne $ownedProcess -and $beforePreferences -eq 'ABSENT' -and
        (Test-Path -LiteralPath 'HKCU:\Software\SpectraAnalyzer\SpectraAnalyzer')) {
        try { Remove-Item -LiteralPath 'HKCU:\Software\SpectraAnalyzer\SpectraAnalyzer' -Recurse -Force }
        catch {
            $report.status = 'failed'
            $report.errors += 'Could not restore absent preferences: ' + $_.Exception.Message
        }
    }
    $report.preferencesUnchanged = (Get-PreferencesHash) -eq $beforePreferences
    $report.scope = 'Hidden native Windows startup, WM_NULL responsiveness, and normal WM_CLOSE; no visual or high-DPI claim.'
    $report.existingInstallationUnchanged = (Get-ExistingInstallHashes) -eq $beforeInstall
    if (-not $report.preferencesUnchanged -or -not $report.existingInstallationUnchanged) {
        $report.status = 'failed'
        $report.errors += 'Preferences or existing local installation changed.'
    }
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $outputPath 'production-exe-smoke.json') -Encoding UTF8
}
$report | ConvertTo-Json -Depth 6
if ($report.status -ne 'passed') { exit 1 }
exit 0
