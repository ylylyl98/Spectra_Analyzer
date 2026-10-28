# Standalone Windows distribution

`Build-Standalone.ps1` creates a Windows x64 standalone application. Its default
`Release` mode also creates an installer EXE and a portable ZIP. These contain
CPython, Qt, NumPy, SciPy, Matplotlib, Pillow, and the application.
The receiving PC needs Windows 10/11 x64; it does not need Python, this checkout,
or a network connection to install or launch the app.

The existing `Install-Launcher.ps1` remains a shortcut for source development.
Its small launcher still needs the original checkout and `.venv` and must not be
used as a standalone distribution.

## Build

Use CPython **3.14.0 x64**, matching the validated dependency lock, and the
official Inno Setup 6 compiler. Obtain Inno Setup from
<https://jrsoftware.org/isdl.php> and verify its Authenticode signature. The
2026-10-23 build uses Inno Setup 6.7.3 (publisher Pyrsys B.V.; SHA-256
`9C73C3BAE7ED48D44112A0F48E66742C00090BDB5BEF71D9D3C056C66E97B732`).
Keep build tools outside the checkout and outside the application test `.venv`.

```powershell
& 'C:\Python314\python.exe' -m venv 'C:\BuildTools\spectra-python'
.\tools\windows\Build-Standalone.ps1 `
  -BuildPython 'C:\BuildTools\spectra-python\Scripts\python.exe' `
  -InnoCompiler 'C:\BuildTools\Inno Setup 6\ISCC.exe' `
  -OutputDirectory 'C:\BuildOutput\spectra-2026-10-23' `
  -BuildDate '2026-10-23'
```

The output folder must be new or empty on every run. Keep output, cache, source,
and build-tool directories separate; output/cache paths through junctions or
symbolic links are rejected. Dependency installation is confined to `BuildPython`.
The script records build logs, installed versions, input hashes, artifact hashes,
mode, cache identity/reuse, and elapsed stage times in `build-manifest.json`.
It does not run tests, install the resulting application, or publish a release.

For everyday app changes, use `Preview` with a stable cache directory. This builds
only `bundle\SpectraAnalyzer\SpectraAnalyzer.exe` and its adjacent runtime;
Inno Setup is not required and no installer or ZIP is compressed.

```powershell
.\tools\windows\Build-Standalone.ps1 `
  -Mode Preview `
  -BuildPython 'C:\BuildTools\spectra-python\Scripts\python.exe' `
  -CacheDirectory "$env:LOCALAPPDATA\SpectraAnalyzerBuildCache" `
  -OutputDirectory 'C:\BuildOutput\spectra-preview-001'
```

To deliver only `SpectraAnalyzer-0.1.3-Setup.exe`, select `-Mode Installer`, provide
`-InnoCompiler`, and keep the same cache directory. This freezes once, compresses
one installer, and skips the portable ZIP. `Preview` and `Installer` share cache
entries. A preceding Preview build is optional. Each still creates a fresh bundle
in its fresh output directory; neither copies a previously frozen app as its result.

Cached modes omit PyInstaller's `--clean` and retain its work and native-library
cache outside output. Their identity includes the checkout path, build Python and
runtime, installed distribution metadata, dependency lock, spec, and diagnostic
path/content. Ordinary app source changes retain that identity so PyInstaller can
detect and rebuild changed inputs. The manifest hashes the actual source and
rejects source changes during a build. `cache.workReused` means an existing
PyInstaller analysis was supplied; PyInstaller's log shows which individual
stages it actually reused or rebuilt. It does not mean a previously built EXE was
accepted unchanged.

After a successful dependency install and `pip check`, cached modes retain a
validation stamp. Each run first checks every exact locked version and fingerprints
all installed distribution names, versions, locations, and metadata (including
RECORD). Only an unchanged validated environment skips redundant pip install/check.
Version or metadata drift reruns both; this is metadata validation, not a hash of
every installed package file. Release always installs/checks dependencies and uses
clean PyInstaller work. Cache directories require the builder's ownership marker,
and simultaneous reuse fails immediately through an exclusive file lock. To start
fresh without deleting anything, choose a new cache directory.

Use three verification lanes:

1. During daily work, run focused tests for the changed behavior. Reuse their
   passing evidence while the relevant source, tests, and dependencies stay unchanged.
2. Build a Preview and launch the frozen app to check the changed interaction.
   A frozen launch check proves the packaged boundary that source tests cannot.
3. For a formal release candidate, run full tests and startup smoke once, then the
   default clean Release build, frozen smoke, and isolated installer validation once.
   Rerun only checks invalidated by subsequent changes or failures.

There is no automatic test-result cache and failed checks are never treated as
passing. Build modes change packaging cost; they do not certify release readiness.

An optional `-DiagnosticScript` builds a separate console verification executable
using the production app's identical dependency archive. This executable stays
under `bundle\diagnostics` and is excluded from both distributable artifacts.

## Install and use

Send `SpectraAnalyzer-VERSION-Setup.exe` to the receiving PC. Double-click
it and follow the wizard. It installs for the current user under
`%LOCALAPPDATA%\Programs\SpectraAnalyzerStandalone`, creates a Start menu shortcut,
and offers a desktop shortcut. Shortcuts use the app's icon and stable taskbar
identity. Projects can be opened inside the app or passed as a single `.spectra`
or `.spextra` command-line filename. The installer does not change file associations.

For portable use, extract the entire ZIP, then launch `SpectraAnalyzer.exe` inside
its `SpectraAnalyzer` folder. Keep `_internal-VERSION` beside the EXE. Copying only the
EXE will not work.

The installer uses a separate directory and shortcut names from the development
launcher. A nonempty installation directory must contain this installer's
ownership marker. Reinstall in the same folder for an upgrade; Windows' Restart
Manager detects open app files. Uninstall through Windows Installed Apps. Only
installer-owned files and shortcuts are removed; projects, other user-created
files, and application preferences in HKCU are preserved. Old application files
from a prior build may remain until the installation is uninstalled because
upgrades deliberately do not recursively delete the installation directory.
Each application version has its own runtime folder, so an upgrade cannot load
stale dependency files left by an earlier version.

The application and installer read their version from `spectra_app/app_version.py`;
the date is recorded in the build manifest. The installer filename and adjacent
`.sha256` file match the in-app update download contract. Artifacts are unsigned. A successful
build alone is not evidence of launch or installer correctness; retain the
frozen smoke and isolated install/upgrade/uninstall results with the artifacts.
`Test-StandaloneInstaller.ps1 -Installer PATH -OutputDirectory NEW_DIRECTORY`
checks an isolated installation, shortcut properties, same-version upgrade,
unowned-directory rejection, and uninstall while retaining user-created data.
It refuses to replace an existing standalone installation or shortcut.

Source and public update downloads use the existing repository
<https://github.com/ylylyl98/Spectra_Analyzer> and its
[Releases](https://github.com/ylylyl98/Spectra_Analyzer/releases). The updater
reads the same repository identifier from `app_version.py`. Publishing a release
is separate from the local build. Upstream dependency metadata, license texts,
and `THIRD_PARTY_NOTICES.txt` are retained in the unpacked runtime directory.
