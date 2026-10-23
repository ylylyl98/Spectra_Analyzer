"""Windows x64 onedir bundle; data keeps its source-relative package path."""
from pathlib import Path
import ast
import os
import sys

from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).resolve().parents[1]
icon = root / "spectra_app" / "resources" / "spectra_analyzer.ico"
version_tree = ast.parse((root / "spectra_app" / "app_version.py").read_text(encoding="utf-8"))
version = next(ast.literal_eval(node.value) for node in version_tree.body
               if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "APP_VERSION"
                       for target in node.targets))
data = [(str(icon), "spectra_app/resources"),
        (str(root / "spectra_app/palettes/RdBu_r_p0p45.pal"), "spectra_app/palettes"),
        (str(root / "spectra_app/palettes/RdBu_r_p0p6.pal"), "spectra_app/palettes"),
        (str(root / "tools/windows/THIRD_PARTY_NOTICES.txt"), "."),
        (str(root / "tools/windows/licenses/LGPL-3.0.txt"), "licenses"),
        (str(root / "tools/windows/licenses/GPL-3.0.txt"), "licenses"),
        (str(Path(sys.base_prefix) / "LICENSE.txt"), "licenses/python")]
runtime_distributions = ["contourpy", "cycler", "fonttools", "kiwisolver",
                         "matplotlib", "numpy", "packaging", "pillow",
                         "pyparsing", "PySide6", "PySide6_Addons",
                         "PySide6_Essentials", "python-dateutil", "scipy",
                         "shiboken6", "six"]
for distribution in runtime_distributions:
    data.extend(copy_metadata(distribution))
analysis = Analysis(
    [str(root / "run_qt.py")],
    pathex=[str(root)],
    binaries=[],
    datas=data,
    hiddenimports=["run_qt"],
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["QtAgg", "Agg", "svg", "pdf"]}},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)


def executable(name, scripts, console=False):
    return EXE(
        pyz,
        scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=console,
        disable_windowed_traceback=False,
        icon=str(icon),
        contents_directory=f"_internal-{version}",
    )


app = executable("SpectraAnalyzer", analysis.scripts)
COLLECT(app, analysis.binaries, analysis.datas, strip=False, upx=False,
        name="SpectraAnalyzer")

# Optional verification-only executable uses the exact same PYZ/dependency set.
# It lives in a separate folder and is never packaged in the installer or ZIP.
diagnostic = os.environ.get("SPECTRA_DIAGNOSTICS_SCRIPT")
if diagnostic:
    diagnostic = Path(diagnostic).resolve(strict=True)
    if analysis.scripts[-1][0] != "run_qt":
        raise RuntimeError("Unexpected PyInstaller entry point order")
    scripts = list(analysis.scripts[:-1])
    scripts.append(("frozen_smoke", str(diagnostic), "PYSOURCE"))
    probe = executable("SpectraAnalyzerDiagnostics", scripts, console=True)
    COLLECT(probe, analysis.binaries, analysis.datas, strip=False, upx=False,
            name="diagnostics")
