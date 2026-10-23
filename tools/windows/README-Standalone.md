# Standalone Windows distribution

`Build-Standalone.ps1` creates a Windows x64 installer EXE and a portable ZIP.
Both contain CPython, Qt, NumPy, SciPy, Matplotlib, Pillow, and the application.
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

The output folder must be new or empty. Dependency installation is confined to
`BuildPython`. The script records build logs, locked environment versions, input
file hashes, artifact hashes, and a manifest. It does not run tests or install
the resulting application. Run the full release tests and source startup smoke
first; then validate the frozen output and installer before distribution.

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
