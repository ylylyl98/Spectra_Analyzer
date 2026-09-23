# PySide6 Spectra Analysis App

Standalone desktop app for `.dat` spectra maps with:
- root-folder scan and process-folder selection,
- DAT file selection/load,
- heatmap + linked gate spectrum (click heatmap or use the plot navigation bar to update the single current gate),
- peak finding on selected spectrum,
- manual per-peak editing by clicking on the spectrum (left click add, right click remove),
- independent detection and fit ranges (`xmin/xmax`), with an explicit **Use Current X Range** copy action,
- optional Savitzky-Golay smoothing for detect/fit input,
- multi-peak fitting with Lorentzian or Gaussian models,
- explicit **Detect Then Fit Current Gate** preparation for Find Peaks seeds (edited seeds are preserved),
- separate **Export Current Gate CSV** and **Export All Results CSV** actions; all-results output preserves batch rows and failures,
- current spectrum CSV export with raw and analysis columns,
- batch auto-fit across all gates in one DAT with CSV export.
- **Export PNG** saves a detached heatmap with a plotting area exactly 4 inches wide by 3 inches high on a compact, fixed 5.15 × 4.14-inch white canvas at 300 DPI. All plot text is 12 pt, including preview titles, axis labels/ticks, scientific offsets, spectrum legends, fit statistics, and peak annotations. The horizontal colorbar sits 0.04 inch above the upper-right corner, with its `ΔR/R (a. u.)` label to the left. The editable title (blank by default) is aligned above the plot's upper-left corner; the Y-axis name/unit are also editable. Clicking the button saves directly beside the loaded DAT, without a save dialog. Filenames retain the original source name and append `_export01`, `_export02`, etc., selecting the next unused number without overwriting existing files. The saved path is shown in the status bar. The full source name and heatmap settings are embedded in the PNG metadata. Export preserves the active colormap, color limits, and viewport regardless of preview size.
- **Title presets** offers **Manual**, **Current group**, and your saved presets. Manual accepts any literal title, including blank. Current group uses `E={voltage}V {field}T {valley}` with `85=K;175=Kp`; these angle labels belong only to that preset. For example, `$YZD320$~$pa$~$8T3.6KRef0.2sx10$~$680nmc$~$R1_85deg$~$0.95TG-BG=18$_rep01_001_avg13_DR_R_External_d2E_Regrid.dat` produces `E=18V 8T K` (voltage is the right-hand value `18`, not the coefficient `0.95`). Edit the format to choose fields/order, use **Insert field**, change the simple angle map, then enter a name and **Save preset**. Allowed fields are `{voltage}`, `{field}`, `{angle}`, `{valley}`, `{temperature}`, and `{sample}`; units are literal text, and `{{` / `}}` insert braces. Only clear, unambiguous filename values are used; missing fields clear the generated title and show a message. No expressions, attribute/index lookups, or format codes are supported.
- Selecting or editing a template sets the default for new files and survives restart. Editing **Plot title** makes a per-DAT override, including an intentional blank; **Apply template** regenerates that DAT's title. Previously saved DAT files recall their own complete template snapshot and override without replacing the default for new files. Named presets are stored locally; copying DAT + JSON also carries custom templates to another computer without installing the preset there.
- **Fix annotations** (on by default) retains Y-axis name/unit and Z signal title as defaults for new files. For backward compatibility, it also retains manual titles in **Manual** mode. In template mode, each new DAT gets a fresh generated title; manual overrides never become global title defaults. Previously opened DAT files restore their own saved annotations first. The editable Z signal list offers `ΔR/R (a. u.)`, `d²(ΔR/R)/dE² (a. u.)`, and `PL (a. u.)`; selecting a label does not transform the data.
- **Recall plots** stores a readable JSON sidecar beside each DAT: `sample.dat.plot.json`. Plot ranges, color limits, colormap, axis interpretation, annotations, selected Gate, and Fix controls are saved when switching/reloading files, closing the app, or exporting PNG. Reopening the DAT restores this state while using the app's current fonts, dimensions, and layout. Copy the DAT and its JSON together to retain the setup on another computer. The DAT is unchanged; content changes or invalid JSON cause the saved settings to be skipped with a message. Version 2 also stores title mode, template snapshot, and manual override; version 1 recipes remain readable with all prior settings and blank titles preserved. Peak/fit results and rendering-style values are not stored.

Gate navigation is committed from the plot bar: arrow/index stepping is immediate, while typed gate values are applied on Enter or focus loss. The visible X range controls detection and can be copied deliberately into the independent fit range; later zooming does not change fit bounds. CSV seed selection never falls back to peak detection when the selected CSV has no seeds.

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

## Export a figure

Load a DAT file, adjust the displayed heatmap as needed, then use **Export PNG** beside the plot toolbar. The image is saved immediately beside that DAT as `original_source_export01.png` (or the next unused number), and the current plot settings are saved to `original_source.dat.plot.json`. The PNG contains only the heatmap and reference colorbar, without the interactive spectrum or editing overlays.

## DAT format expected
- Header row with gate values is supported, e.g.:
  - `Photon energy(eV)    -1.0    -0.5    0.0 ...`
- Data rows:
  - first column: energy
  - remaining columns: intensity vs gate

The app also handles common whitespace/comma/tab separated numeric rows.
