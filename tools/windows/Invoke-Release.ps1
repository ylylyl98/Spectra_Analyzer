# GitHub-hosted release orchestration. Never run against a user's installation.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceCommit,
    [Parameter(Mandatory=$true)][ValidatePattern('^\d+$')][string]$FullSuiteRunId
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows') { throw 'This entry point requires a GitHub-hosted Windows release job.' }
if ((git rev-parse HEAD) -cne $SourceCommit) { throw 'Checkout differs from gated source commit.' }
function Assert-Exit([string]$Stage) { if ($LASTEXITCODE -ne 0) { throw "$Stage failed ($LASTEXITCODE)." } }
function Assert-Smoke([string]$Path) {
    $report = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    if ($report.status -ne 'passed') { throw "Smoke report did not pass: $Path" }
}
$nativeModules = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\Modules'
$env:PSModulePath = $nativeModules
Import-Module Microsoft.PowerShell.Utility,Microsoft.PowerShell.Archive -Force
$evidence = Join-Path $env:RUNNER_TEMP 'release-evidence'
$build = Join-Path $env:RUNNER_TEMP 'release-build'
New-Item -ItemType Directory -Path $evidence | Out-Null
Start-Transcript -Path (Join-Path $evidence 'release.log') | Out-Null
try {
    gh run download $FullSuiteRunId --repo $env:GH_REPO --name windows-full-suite-verdict --dir "$evidence/full-suite"
    Assert-Exit 'Download exact-commit verdict'
    $verdict = Get-Content -Raw "$evidence/full-suite/full-suite.json" | ConvertFrom-Json
    if ($verdict.passed -ne $true -or $verdict.complete_suite -ne $true -or $verdict.commit -cne $SourceCommit) { throw 'Incomplete or mismatched full-suite verdict.' }
    [ordered]@{sourceCommit=$SourceCommit;fullSuiteRunId=$FullSuiteRunId;fullSuiteRunUrl="https://github.com/$env:GH_REPO/actions/runs/$FullSuiteRunId"} | ConvertTo-Json | Set-Content "$evidence/release-gate.json" -Encoding UTF8
    # Keep Python source out of native arguments for Windows PowerShell 5.1.
    $version = @'
import ast
with open("spectra_app/app_version.py", encoding="utf-8-sig") as source:
    tree = ast.parse(source.read())
print(next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == "APP_VERSION" for x in n.targets)))
'@ | python -
    Assert-Exit 'Read release version'
    if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid release version.' }
    $tag = "v$version"
    # An API listing succeeds even when no matching release/tag exists; errors fail closed.
    $releases = gh api --paginate --slurp "repos/$env:GH_REPO/releases?per_page=100" | ConvertFrom-Json
    Assert-Exit 'Read existing releases'
    if (@($releases | ForEach-Object { $_ } | Where-Object { $_.tag_name -eq $tag }).Count) { throw "Release $tag already exists; inspect it manually, never overwrite." }
    $previous = gh api "repos/$env:GH_REPO/releases/latest" | ConvertFrom-Json
    Assert-Exit 'Read previous stable release'
    if ($previous.tag_name -notmatch '^v?\d+\.\d+\.\d+$') { throw 'Previous stable release has an invalid version.' }
    $refs = gh api "repos/$env:GH_REPO/git/matching-refs/tags/$tag" | ConvertFrom-Json
    Assert-Exit 'Read existing tags'
    if (@($refs | Where-Object { $_.ref -ceq "refs/tags/$tag" }).Count) { throw "Tag $tag already exists; never move an existing tag." }
    $privateTemp = Join-Path $env:RUNNER_TEMP 'release-temp'
    New-Item -ItemType Directory -Path $privateTemp | Out-Null
    $canonicalTemp = python -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=True))' $privateTemp
    Assert-Exit 'Resolve private temporary directory'
    if ([IO.Path]::GetPathRoot($canonicalTemp) -ne [IO.Path]::GetPathRoot($env:GITHUB_WORKSPACE)) { throw 'Temporary paths must use the checkout drive.' }
    $env:TMP = $canonicalTemp
    $env:TEMP = $canonicalTemp
    python -m venv "$env:RUNNER_TEMP/release-python"
    Assert-Exit 'Create isolated build Python'
    $python = "$env:RUNNER_TEMP/release-python/Scripts/python.exe"
    $innoInstaller = Join-Path $env:RUNNER_TEMP 'innosetup-6.7.3.exe'
    Invoke-WebRequest 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $innoInstaller -UseBasicParsing
    $innoHash = (Get-FileHash -LiteralPath $innoInstaller -Algorithm SHA256).Hash
    if ($innoHash -cne '9C73C3BAE7ED48D44112A0F48E66742C00090BDB5BEF71D9D3C056C66E97B732') { throw 'Inno Setup installer checksum mismatch.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $innoInstaller
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Pyrsys B.V.') { throw 'Inno Setup signature/publisher verification failed.' }
    $inno = Join-Path $env:RUNNER_TEMP 'release-inno'
    $process = Start-Process -FilePath $innoInstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER',('"/DIR=' + $inno + '"')) -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw 'Inno Setup installation failed.' }
    $compiler = Join-Path $inno 'ISCC.exe'
    if ((Get-Item $compiler).VersionInfo.ProductVersion -notmatch '^6\.7\.3') { throw 'Unexpected Inno compiler version.' }
    [ordered]@{sha256=$innoHash;signature=[string]$signature.Status;publisher=$signature.SignerCertificate.Subject;compilerVersion=(Get-Item $compiler).VersionInfo.ProductVersion} | ConvertTo-Json | Set-Content "$evidence/inno.json" -Encoding UTF8
    & $python -m pip install -r tools/windows/requirements-standalone.txt
    Assert-Exit 'Install pinned release dependencies'
    & $python -m pip check
    Assert-Exit 'Check release dependencies'
    & $python -B tools/windows/release_smoke.py --output "$evidence/source-smoke"
    Assert-Exit 'Source startup/render/export smoke'
    Assert-Smoke "$evidence/source-smoke/release-smoke.json"
    & "$PSScriptRoot/Build-Standalone.ps1" -Mode Release -BuildPython $python -InnoCompiler $compiler -OutputDirectory $build -DiagnosticScript "$PSScriptRoot/release_smoke.py"
    Assert-Exit 'Clean Release build'
    & "$build/bundle/diagnostics/SpectraAnalyzerDiagnostics.exe" --output "$evidence/frozen-smoke"
    Assert-Exit 'Frozen diagnostic smoke'
    Assert-Smoke "$evidence/frozen-smoke/release-smoke.json"
    & "$PSScriptRoot/Test-StandaloneApp.ps1" -Executable "$build/bundle/SpectraAnalyzer/SpectraAnalyzer.exe" -OutputDirectory "$evidence/production-smoke"
    Assert-Exit 'Production executable smoke'
    Assert-Smoke "$evidence/production-smoke/production-exe-smoke.json"
    $installer = "$build/SpectraAnalyzer-$version-Setup.exe"
    & "$PSScriptRoot/Test-StandaloneInstaller.ps1" -Installer $installer -OutputDirectory "$evidence/installer" -ApplicationSmokeScript "$PSScriptRoot/Test-StandaloneApp.ps1"
    Assert-Exit 'Isolated installer lifecycle'
    Assert-Smoke "$evidence/installer/installer-validation.json"
    $manifest = Get-Content -Raw "$build/build-manifest.json" | ConvertFrom-Json
    if ($manifest.mode -ne 'Release' -or $manifest.installerVersion -ne $version -or $manifest.cache.enabled -ne $false -or $manifest.python -ne '3.14.0' -or $manifest.architecture -ne 'x64') { throw 'Unexpected build manifest.' }
    $expected = @("SpectraAnalyzer-$version-Setup.exe", "SpectraAnalyzer-$version-win64.zip")
    if ($manifest.artifacts.Count -ne 2) { throw 'Unexpected distributable count.' }
    foreach ($name in $expected) {
        $entry = @($manifest.artifacts | Where-Object { $_.name -ceq $name })
        $file = Get-Item -LiteralPath (Join-Path $build $name)
        if ($entry.Count -ne 1 -or $file.Length -le 0 -or $file.Length -ne $entry[0].bytes -or (Get-FileHash $file.FullName -Algorithm SHA256).Hash -cne $entry[0].sha256) { throw "Artifact validation failed: $name" }
    }
    $installerHash = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    if ((Get-Content -Raw ($installer + '.sha256')).Trim() -cne ($installerHash + '  ' + [IO.Path]::GetFileName($installer))) { throw 'Updater checksum sidecar mismatch.' }
    $sums = @(Get-Content "$build/SHA256SUMS.txt")
    foreach ($entry in $manifest.artifacts) { if ($sums -cnotcontains ('{0} *{1}' -f $entry.sha256,$entry.name)) { throw 'SHA256SUMS differs from manifest.' } }
    $assets = @($installer, ($installer + '.sha256'), "$build/SpectraAnalyzer-$version-win64.zip", "$build/SHA256SUMS.txt", "$build/build-manifest.json", "$evidence/release-gate.json")
    # Draft creation can defer tag creation until publication. Create the ref
    # first; GitHub rejects an existing ref, including a race after preflight.
    gh api --method POST "repos/$env:GH_REPO/git/refs" -f "ref=refs/tags/$tag" -f "sha=$SourceCommit" | Out-Null
    Assert-Exit 'Create exact source tag (never overwrite)'
    $tagCommit = gh api "repos/$env:GH_REPO/git/ref/tags/$tag" --jq .object.sha
    Assert-Exit 'Verify source tag before draft'
    if ($tagCommit -cne $SourceCommit) { throw 'Tag differs from gated commit; do not create a release.' }
    gh release create $tag --repo $env:GH_REPO --verify-tag --target $SourceCommit --draft --title "Spectra Analyzer $version" --notes-file "docs/releases/$tag.md" @assets
    Assert-Exit 'Create draft release with all assets'
    $draft = gh release view $tag --repo $env:GH_REPO --json isDraft,assets,targetCommitish | ConvertFrom-Json
    Assert-Exit 'Verify draft release'
    if (-not $draft.isDraft -or $draft.targetCommitish -cne $SourceCommit -or $draft.assets.Count -ne $assets.Count) { throw 'Draft release verification failed; leave it unpublished.' }
    foreach ($asset in $assets) {
        $file = Get-Item $asset
        if (@($draft.assets | Where-Object { $_.name -ceq $file.Name -and $_.size -eq $file.Length }).Count -ne 1) { throw 'Draft release assets differ; leave unpublished.' }
    }
    $tagCommit = gh api "repos/$env:GH_REPO/git/ref/tags/$tag" --jq .object.sha
    Assert-Exit 'Verify source tag before publication'
    if ($tagCommit -cne $SourceCommit) { throw 'Tag differs from gated commit; leave the release unpublished.' }
    gh release edit $tag --repo $env:GH_REPO --draft=false --prerelease=false --latest
    Assert-Exit 'Publish stable latest release'
    $publishedCommit = gh api "repos/$env:GH_REPO/commits/$tag" --jq .sha
    Assert-Exit 'Verify published tag commit'
    if ($publishedCommit -cne $SourceCommit) { throw 'Published tag differs from gated commit; release is already published. Investigate without moving the tag.' }
    # Publication has happened. A failure here leaves the published release intact.
    & $python -B "$PSScriptRoot/verify_published_update.py" --repository $env:GH_REPO --previous-version $previous.tag_name --version $version --sha256 $installerHash --output "$evidence/public-update"
    Assert-Exit 'Anonymous public updater discovery and verified download (release is already published)'
} finally {
    Stop-Transcript | Out-Null
}
