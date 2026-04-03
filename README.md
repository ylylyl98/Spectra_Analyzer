# PySide6 Spectra Analysis App

Standalone desktop app for `.dat` spectra maps with:
- root-folder scan and process-folder selection,
- DAT file selection/load,
- heatmap + linked gate spectrum (click heatmap to update gate),
- peak finding on selected spectrum,
- manual per-peak editing by clicking on the spectrum (left click add, right click remove),
- fit range control (`xmin/xmax`) so detect/fit can be limited to selected energy window,
- optional Savitzky-Golay smoothing for detect/fit input,
- multi-peak fitting with Lorentzian or Gaussian models,
- fit parameter CSV export (`center`, `width`, `amplitude`, plus gate/model/baseline),
- current spectrum CSV export (raw and analysis columns),
- batch auto-fit across all gates in one DAT with CSV export.

## Install
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run
```bash
python run_qt.py
```

## DAT format expected
- Header row with gate values is supported, e.g.:
  - `Photon energy(eV)    -1.0    -0.5    0.0 ...`
- Data rows:
  - first column: energy
  - remaining columns: intensity vs gate

The app also handles common whitespace/comma/tab separated numeric rows.
