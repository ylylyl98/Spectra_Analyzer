"""Child-only startup gate and isolated QSettings profile for suite processes."""
import os
from pathlib import Path
import time


if os.environ.get('SPECTRA_ACCEPTANCE_START_GATE'):
    _gate = Path(os.environ['SPECTRA_ACCEPTANCE_START_GATE'])
    _deadline = time.monotonic() + 30
    while not _gate.exists():
        if time.monotonic() > _deadline:
            raise RuntimeError('Supervisor did not assign an owned Job before imports')
        time.sleep(.02)

if os.environ.get('SPECTRA_FINAL_REQUIRE_GUARD') == '1':
    from PySide6.QtCore import QSettings
    _root = Path(os.environ['SPECTRA_SUITE_SETTINGS'])
    _root.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(_root / 'user'))
    QSettings.setPath(QSettings.IniFormat, QSettings.SystemScope, str(_root / 'system'))
    _OriginalSettings = QSettings

    class SuiteGuardSettings(_OriginalSettings):
        def __init__(self, *args, **kwargs):
            if len(args) >= 2 and args[0] == 'SpectraAnalyzer' and args[1] == 'SpectraAnalyzer':
                super().__init__(str(_root / 'SpectraAnalyzer.ini'), _OriginalSettings.IniFormat)
            else:
                super().__init__(*args, **kwargs)

    import PySide6.QtCore as _qtcore
    _qtcore.QSettings = SuiteGuardSettings
