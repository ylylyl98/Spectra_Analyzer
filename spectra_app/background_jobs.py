"""Cancellable pure work with generation-checked delivery on the GUI thread."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .dat_io import load_dat_cube, list_dat_files
from .plot_state import PlotStateStore, source_fingerprint


class CancelledError(Exception):
    pass


class JobContext:
    def __init__(self, cancelled: Event, progress: Callable[[str], None]):
        self._cancelled = cancelled
        self._progress = progress

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise CancelledError()

    def report(self, message: str) -> None:
        self.check_cancelled()
        self._progress(message)


class _Job(QThread):
    result = Signal(object)
    error = Signal(object)
    progress = Signal(str)

    def __init__(self, work, parent):
        super().__init__(parent)
        self.work = work
        self.cancelled = Event()

    def run(self):
        context = JobContext(self.cancelled, self.progress.emit)
        try:
            context.check_cancelled()
            result = self.work(context)
            context.check_cancelled()
            self.result.emit(result)
        except CancelledError:
            pass
        except Exception as exc:
            self.error.emit(exc)


class BackgroundJobs(QObject):
    """Own threads until finished; only the newest request may invoke callbacks."""
    idle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs = {}
        self._current = None

    @property
    def busy(self):
        return bool(self._jobs)

    def start(self, work, on_result, *, on_error=None, on_progress=None):
        self.cancel()
        job = _Job(work, self)
        self._jobs[job] = (on_result, on_error, on_progress)
        self._current = job
        job.result.connect(self._result)
        job.error.connect(self._error)
        job.progress.connect(self._progress)
        job.finished.connect(self._finished)
        job.start()

    def cancel(self):
        self._current = None
        for job in self._jobs:
            job.cancelled.set()

    def _deliver(self, index, value):
        job = self.sender()
        if job is self._current:
            callback = self._jobs[job][index]
            if callback is not None:
                try:
                    callback(value)
                except Exception as exc:
                    on_error = self._jobs[job][1]
                    if index == 1 or on_error is None:
                        import traceback
                        traceback.print_exc()
                    else:
                        on_error(exc)

    @Slot(object)
    def _result(self, result):
        self._deliver(0, result)

    @Slot(object)
    def _error(self, error):
        self._deliver(1, error)

    @Slot(str)
    def _progress(self, message):
        self._deliver(2, message)

    @Slot()
    def _finished(self):
        job = self.sender()
        self._jobs.pop(job, None)
        if job is self._current:
            self._current = None
        job.deleteLater()
        if not self._jobs:
            self.idle.emit()


@dataclass(frozen=True)
class LoadRequest:
    path: Path
    override: str | None
    colormaps: tuple[str, ...] = ()
    recall: bool = True


def prepare_dat(request: LoadRequest, context: JobContext):
    context.report(f"Reading {request.path.name}…")
    fingerprint = source_fingerprint(request.path)
    context.check_cancelled()
    saved_plot, notice = (PlotStateStore().load(request.path, fingerprint)
                          if request.recall else (None, ""))
    recipes = ([record['plot'] for record in saved_plot['graphs']] if saved_plot and 'graphs' in saved_plot
               else [saved_plot] if saved_plot else [])
    if any(recipe['colormap'] not in request.colormaps for recipe in recipes):
        saved_plot = None
        notice = "Saved plot settings ignored: the colormap is unavailable."
    override = saved_plot["axis_override"] if saved_plot else request.override
    cube = load_dat_cube(request.path, x_axis_override=override)
    context.check_cancelled()
    if source_fingerprint(request.path) != fingerprint:
        raise ValueError("DAT content changed while loading. Please load it again.")
    context.check_cancelled()
    return cube, fingerprint, saved_plot, notice


@dataclass(frozen=True)
class DatImportError:
    path: Path
    reason: str


@dataclass(frozen=True)
class DatBatchPreparation:
    prepared: tuple[tuple[Path, tuple], ...]
    errors: tuple[DatImportError, ...]


def prepare_dat_batch(requests: tuple[LoadRequest, ...], context: JobContext) -> DatBatchPreparation:
    """Prepare one ordered batch; expected file failures do not abort other files."""
    prepared, errors = [], []
    for index, request in enumerate(requests, 1):
        context.report(f'Importing DAT {index}/{len(requests)}: {request.path.name}…')
        try:
            result = prepare_dat(request, context)
        except (OSError, ValueError) as exc:
            errors.append(DatImportError(request.path, str(exc)))
        else:
            prepared.append((request.path, result))
        context.check_cancelled()
    return DatBatchPreparation(tuple(prepared), tuple(errors))


def discover_folders(root: Path, context: JobContext):
    """Walk incrementally so cancellation does not await a full recursive scan."""
    import os
    folders = []
    for directory, _, filenames in os.walk(root):
        context.check_cancelled()
        if any(Path(name).suffix.lower() == ".dat" for name in filenames):
            folders.append(Path(directory))
            context.report(f"Found {len(folders)} DAT folders…")
    return sorted(folders, key=lambda path: (path != root, path))


def discover_files(folder: Path, context: JobContext):
    context.report("Reading folder…")
    files = list_dat_files(folder)
    context.check_cancelled()
    return files
