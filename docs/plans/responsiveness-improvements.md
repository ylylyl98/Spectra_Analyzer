# Responsiveness improvements

Scope authorized by the user: improve the measured input/rendering stalls and move blocking load, discovery, and analysis operations off the UI thread. Preserve numeric results, plot/export appearance, saved settings, and Fix behavior.

## Execution

- [x] Remove redundant regional layouts; commit numeric edits without per-digit rebuilds; update peak drag markers in place. Verified focused rendering/persistence tests and synthetic timing (boundary 685→284 ms, xmin 440→31 ms).
- [x] Add a minimal cancellable background-job boundary; move file loading, axis interpretation, and folder discovery to it. Focused tests cover event-loop responsiveness, thread affinity, cancellation, stale results, source changes, recipes, and safe close.
- [x] Move single/batch fitting and batch peak detection to background jobs using input snapshots. Verified worker/GUI thread separation, progress, atomic cancellation, gate navigation, stale results/errors, and preserved numerical workflow.
- [x] Review actual changes; run focused interaction, persistence, rendering, and job lifecycle checks; repeat representative performance measurements. Parent combined verification: 87 tests and 6 subtests passed. Independent review identified and verified the fix for edits made while a new file is loading; final analysis review found no further material issue.

## Practical limits

- Cancellation is cooperative: an active solver call finishes before its thread stops; the UI remains responsive and its result is discarded.
- Full regional rebuilds still take approximately 250–280 ms for the measured 300×1000 fixture; numeric typing no longer triggers rebuilds per digit.
- Axis reinterpretation still reparses and checks the source, now in the background. No parsing cache or installer build was introduced.

No packaging or unrelated refactoring. Existing baseline legacy-recipe and environment-specific export test failures must be distinguished from regressions.

Final focused recheck after normalizing default peak/dip kinds in the stale-input guard: 23 analysis/workflow tests passed. This avoids discarding a running fit merely because an unchanged seed map was synchronized for export.
