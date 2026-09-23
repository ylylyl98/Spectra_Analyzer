# Workflow Simplification Implementation Plan

> For agentic workers: Execute sequentially with the executing-plans skill. The user selected Luna implementation; use one `gpt-5.6-luna` worker with `high` effort. Parent reviews the actual diff and focused test evidence.

**Goal:** Make Gate selection, range selection, fitting preparation, and CSV export explicit and consistent.

**Architecture:** Extend the existing PySide6 MainWindow and reuse its Gate navigation, peak detection, fitting, and CSV row helpers. Preserve the existing viewport/color-limit fix and scientific algorithms. Use one implementation worker because the changes share the same window and state.

**Tech Stack:** Python, PySide6, NumPy, Matplotlib, SciPy, unittest; use `.venv/Scripts/python.exe`.

**Spec:** User-approved four recommendations in this task: one immediate Gate entry; one-click adoption of visible X as fit range with a range summary; explicit detect-then-fit while preserving edited peaks; separate current-Gate/all-results CSV exports. The user's “用Luna impleent” authorizes this scope.

## Constraints

- Preserve existing uncommitted cmap implementation and its nine tests.
- Main production ownership: `spectra_app/main_window.py`. New tests: `tests/test_workflow.py`. Update `README.md` with user-facing workflow behavior.
- Keep existing advanced controls, CSV schema, scientific algorithms, manual peak edits, and CSV seed source semantics.
- Do not add dependencies, change the shell/theme, commit, package, or refactor unrelated modules.
- Current-Gate detect-then-fit is explicit; batch fitting remains explicit and must not silently redetect all gates or overwrite prepared seeds.
- Fix protects bounds from automatic resets; a user-invoked “Use Current X Range” copies both endpoints as a deliberate edit without changing Fix states.

## 1. One Gate navigation path

- [x] Add a real Qt regression with irregular Gate coordinates. Select a Gate through the visible navigation input; assert `current_gate`, displayed spectrum, and the Gate used for fitting/detection agree immediately. No Apply step.
- [x] Run the regression before changing behavior and record the failure.
- [x] Remove the duplicate sidebar Gate picker and Apply action. Use the existing plot navigation and `_set_current_gate_from_value/index` path. Update all references to the removed controls rather than leaving an unsynchronized hidden widget. Numeric text commits on Enter/focus loss; arrow navigation remains immediate.
- [x] Verify heatmap clicks, index navigation and value input stay synchronized, and prior cmap tests pass.

## 2. Explicit range selection

- [x] Add a regression: set visible X, invoke the copy action, assert both fit bounds match; subsequently zoom and assert fit bounds remain unchanged. Include reversed endpoints and Fix-state preservation.
- [x] Add a “Use Current X Range” action near Fit Range. Copy the ordered current X bounds with signals blocked, then refresh once.
- [x] Show a concise, refreshed summary identifying detection's visible-X range and the independent fit range. Refresh on load, navigation/zoom, axis override, and explicit range changes without introducing redraw recursion.
- [x] Keep existing Full Range/Fix semantics and verify the focused regression.

## 3. Clear fit preparation

- [x] Add tests for a synthetic peaked spectrum with no seeds, existing manually edited seeds, CSV seeds, and a CSV source with no loaded seeds.
- [x] Refresh a seed-readiness message and current-Gate action label based on active seed source and seeds in fit range.
- [x] For the Find Peaks source with no existing Gate seeds, provide the explicit “Detect Then Fit Current Gate” action and reuse detection before fitting. If no peaks are found or detection fails, stop with a clear message. State which detection range is used.
- [x] When existing seeds are present, fit directly without redetection. If seeds exist only outside fit range, explain that mismatch without replacing them. CSV selection must not silently fall back to detection or another seed source.
- [x] Preserve separate Find Peaks and Batch Fit operations. Refresh preparation state after Gate changes, range edits, detection/manual edits, CSV loading/source changes, and data reload.

## 4. Explicit export scope

- [x] Add tests writing real temporary CSVs, intercepting only the file chooser. Verify current export contains only the selected Gate even when batch results exist; all-results export contains all available rows including recorded failures. Cover multiple individually fitted Gates too.
- [x] Replace the ambiguous export action with visibly distinct “Export Current Gate CSV” and “Export All Results CSV” actions. Reuse `_fit_result_to_export_row` and `_export_fieldnames`; preserve schema and failures.
- [x] Validate or disable unavailable actions with useful state text. Clearing results or loading a new file must reset export availability.
- [x] Update README and record final file list/test output.

## Focused validation and parent review

### Verified prerequisite: fit initialization bounds

Parent integrated validation found that the existing `fitting.py` initial width (`xrange / 20` for one peak) can fall outside GUI-specified linewidth bounds after cropping. Example: X=1.5..4, original grid spacing=0.02, minimum width=10 points=0.2, initial width=0.125. Detect Then Fit fails with “Initial guess is outside of provided bounds”.

- [x] Extend worker ownership to `spectra_app/fitting.py` and a focused regression for this prerequisite. Keep the user's configured bounds and models intact; place the initial width within the valid effective interval. Verify lower- and upper-bound cases, plus the default-GUI cropped workflow.

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m unittest tests.test_workflow tests.test_view_color_limits -v
git diff --check
```

- [x] Parent inspects the actual production diff and tests, checking all four outcomes and no unrelated changes.
- [x] Run an integrated synthetic-DAT smoke path: load, select Gate, crop, copy range, detect/fit, export selected/all, and verify preserved cmap behavior.
- [x] Report implementation, workers, checks, and remaining limitations to the user.

## Validation evidence

- Parent baseline reproduction: sidebar Gate set to 1 left the plot title at `Spectrum @ 0`, while the analysis target was 1.
- Parent integrated reproduction initially failed with an out-of-bounds initial linewidth after cropping; the same DAT workflow passed after the initialization fix.
- Final focused run: 20 tests passed (11 workflow and 9 viewport/color-limit tests). `git diff --check` passed.
- Real synthetic-DAT workflow: load, crop, copy range, navigate irregular Gates, detect and fit two Gates with default GUI bounds, export one current row versus two all-result rows, reload and verify CSV seed/result reset. Cmap remained consistent.
- Initial-width upper-bound check passed separately; fitted width stayed within 0.005..0.02.
- Independent Luna high review found stale zoom readiness and missing CSV batch-source validation; both were corrected, covered by regressions, and confirmed closed by the reviewer.
- Export availability refresh with 5,000 cached results fell from approximately 363 ms to 0.044 ms in the local check after removing export serialization from the redraw path. This is a focused local measurement, not a general performance guarantee.
- Limitations: synthetic data and offscreen Qt validation; no real experimental dataset manual acceptance or installer/package validation performed.
