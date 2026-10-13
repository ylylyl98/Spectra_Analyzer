# Comparison display

Approved scope: normalize, vertically offset and compare existing XY curves over one configured X interval. This extends the scrollable XY inspector; the graph toolbar and Add data action remain available. Cross-window axis synchronization is a separate feature.

## Scientific contract

Sources remain the original Sheet column views or live DAT raw/applied cube views. A Qt-free display helper creates temporary plotting arrays only. It never changes a source array, sorts, interpolates, smooths, resamples or fits a baseline. All previews, SVG clipboard output and PNG/SVG exports call the same renderer.

Each Comparison stores `normalization` (`none`, `maxabs`, `minmax`), finite signed `stack_step`, `x_range_mode` (`all`, `common`, `manual`) and an optional ordered finite `x_range` pair. Each Curve stores finite signed `y_offset`. Defaults are `none`, zero, `all`, no manual range and zero extra offset.

All X uses every finite original X/Y pair. Common overlap intersects the finite paired X extents of visible usable curves; hidden and entirely nonfinite curves do not restrict it. Manual range requires finite minimum < maximum. Empty/no-positive-width common intervals produce an empty view with feedback, never fallback to all X. Curves without samples inside the interval are omitted and named. Original row order, duplicates, unequal grids and gaps remain intact: excluded rows become NaN.

Normalize each curve using its selected original sample pairs, then offset:

`display Y = normalized Y + curve list ordinal × stack_step + y_offset`

The ordinal includes hidden entries. Maximum-absolute normalization divides by max(abs(Y)); all-zero data stays zero. Min–max maps to 0–1; constant or single-sample data becomes zero with a notice. Opposite-sign extrema are scaled before subtraction to avoid finite overflow. Offset overflows become omitted display values with a notice. Y autoscale sees only the selected display samples. Temporary zoom/pan does not change normalization references; Home/Rescale returns to the configured view. Manual mode seeds from the configured sample extents, independently of temporary zoom.

## Editing and persistence

Native combo boxes and line edits accept scientific notation without rounding stored values on display. Invalid or reversed drafts are retained and block finish/save/document switching/closing through the existing preflight contract. Science refresh renders committed settings while keeping drafts. Changing normalization preserves offsets. Reset comparison display restores the neutral settings and every curve offset while preserving labels, source references, styles, visibility and unrelated drafts. The control status names omitted/constant/overflow curves and reports the effective range and normalization. Export Y labels describe normalization/offsets while saved base axis labels remain unchanged; neutral export labels stay unchanged.

Schema 5 stores only metadata plus the existing scientific arrays. These fields participate in runtime dirty stamps and canonical digests. Versions 1–3 retain their migration paths; v4 Comparison and Curve objects are validated against their original exact field sets and receive neutral defaults. Version 5 validates exact fields, enums, finite numeric values excluding bool, and ordered range bounds. Existing archive resource limits, detached snapshots and no-pickle reads remain in force. Older apps may reject v5; display settings are not silently discarded.

## Verification boundary

Focused tests observe plotted arrays/limits, independent grids, out-of-window peaks, overflow/constant/zero/single-sample/nonfinite/disjoint cases, source bytes, real UI drafts/reset, live DAT updates, append order, close/reopen, source-free saves, schema migration/rejection, dirty stamps and shared export rendering. Affected workbook/project/XY/batch suites provide regression coverage. Offscreen screenshots use the existing Fusion/Segoe UI/application QSS at normal and compact widths; they do not claim native high-DPI validation.
