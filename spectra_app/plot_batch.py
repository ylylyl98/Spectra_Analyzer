"""One detached graph per step, for a GUI-thread QTimer serial export loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from .plot_presentation import HeatmapSnapshot, PlotPresentation
from .svg_export import render_hybrid_svg, write_svg, write_svg_png


@dataclass(frozen=True)
class BatchExportJob:
    snapshot: HeatmapSnapshot
    presentation: PlotPresentation
    basename: Path
    metadata: Mapping[str, str] = field(default_factory=dict)
    target_label: str = ''

    def __post_init__(self):
        if not isinstance(self.snapshot, HeatmapSnapshot) or not isinstance(self.presentation, PlotPresentation):
            raise ValueError('Batch jobs require detached snapshot and presentation records.')
        if not isinstance(self.basename, (str, Path)) or not str(self.basename).strip():
            raise ValueError('Batch basename must be a nonempty path without an export suffix.')
        path = Path(self.basename)
        if path.name in ('', '.', '..'):
            raise ValueError('Batch basename must name a file.')
        if not isinstance(self.metadata, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str) for key, value in self.metadata.items()):
            raise ValueError('Batch metadata keys and values must be strings.')
        if not isinstance(self.target_label, str):
            raise ValueError('Batch target labels must be text.')
        object.__setattr__(self, 'basename', path)
        object.__setattr__(self, 'metadata', dict(self.metadata))
        object.__setattr__(self, 'presentation', PlotPresentation.from_dict(self.presentation.to_dict()))


@dataclass(frozen=True)
class FormatExportResult:
    format: str
    path: Path
    success: bool
    error: str = ''
    error_stage: str = ''


@dataclass(frozen=True)
class BatchFileResult:
    dataset_id: str
    basename: Path
    outputs: tuple[FormatExportResult, ...] = ()
    error: str = ''
    error_stage: str = ''
    canceled: bool = False
    graph_id: str = 'default'
    target_label: str = ''

    @property
    def success(self) -> bool:
        return bool(self.outputs) and not self.canceled and not self.error and all(item.success for item in self.outputs)


@dataclass(frozen=True)
class BatchSummary:
    total: int
    results: tuple[BatchFileResult, ...]

    @property
    def completed(self) -> int:
        return sum(not result.canceled for result in self.results)

    @property
    def failed(self) -> int:
        return sum(not result.success and not result.canceled for result in self.results)

    @property
    def canceled(self) -> int:
        return sum(result.canceled for result in self.results)

    @property
    def success(self) -> bool:
        return len(self.results) == self.total and self.failed == 0 and self.canceled == 0


def _formats(formats):
    if (not isinstance(formats, (tuple, list)) or not formats
            or any(item not in ('svg', 'png') for item in formats)
            or len(set(formats)) != len(formats)):
        raise ValueError('Batch formats must be a nonempty distinct sequence of svg and/or png.')
    return tuple(formats)


def next_export_paths(basename: str | Path, formats=('svg',)) -> tuple[Path, ...]:
    """Check every requested extension together; writers still create exclusively."""
    formats = _formats(formats)
    base = Path(basename)
    index = 1
    while True:
        paths = tuple(base.parent / f'{base.name}_export{index:02d}.{kind}' for kind in formats)
        if not any(path.exists() for path in paths):
            return paths
        index += 1


class SerialPlotBatch:
    """No threads, activation, settings writes or sidecars. Call step on the GUI thread.

    A basename is a complete destination stem (including its directory). Capture
    jobs before constructing this runner. Cancel is observed between graphs;
    each graph's requested SVG/PNG pair finishes together.
    """
    def __init__(self, jobs: Sequence[BatchExportJob], *, formats=('svg',),
                 renderer=render_hybrid_svg, svg_writer=write_svg, png_writer=write_svg_png,
                 prepare_page=None):
        self.formats = _formats(formats)
        self._jobs = tuple(jobs)
        if any(not isinstance(job, BatchExportJob) for job in self._jobs):
            raise ValueError('Serial batch requires a declared sequence of BatchExportJob records.')
        self._renderer, self._svg_writer, self._png_writer = renderer, svg_writer, png_writer
        self._prepare_page = prepare_page
        self._next = 0
        self._results = []
        self._stepping = False
        self._cancel_requested = False

    @property
    def done(self) -> bool:
        return self._next == len(self._jobs)

    @property
    def summary(self) -> BatchSummary:
        return BatchSummary(len(self._jobs), tuple(self._results))

    def cancel(self) -> None:
        self._cancel_requested = True
        if not self._stepping:
            self._cancel_remaining()

    def _cancel_remaining(self):
        for job in self._jobs[self._next:]:
            self._results.append(BatchFileResult(job.snapshot.dataset_id, job.basename, canceled=True,
                graph_id=job.snapshot.graph_id, target_label=job.target_label))
        self._next = len(self._jobs)

    def step(self) -> BatchFileResult | None:
        if self._stepping:
            raise RuntimeError('Batch export steps must run serially.')
        if self.done:
            return None
        self._stepping = True
        job = self._jobs[self._next]
        self._next += 1
        try:
            if self._prepare_page is None:
                page = self._renderer(job.snapshot, job.presentation)
            else:
                # Keep sizing and its metadata on the detached job that owns
                # these bytes; the GUI callback may also update a current owner.
                job, page = self._prepare_page(job)
                index = self._next - 1
                self._jobs = self._jobs[:index] + (job,) + self._jobs[index+1:]
        except Exception as exc:
            result = BatchFileResult(job.snapshot.dataset_id, job.basename, error=str(exc), error_stage='render',
                graph_id=job.snapshot.graph_id, target_label=job.target_label)
        else:
            try:
                paths = next_export_paths(job.basename, self.formats)
            except Exception as exc:
                result = BatchFileResult(job.snapshot.dataset_id, job.basename, error=str(exc), error_stage='write',
                    graph_id=job.snapshot.graph_id, target_label=job.target_label)
            else:
                outputs = []
                for kind, path in zip(self.formats, paths):
                    try:
                        if kind == 'svg':
                            self._svg_writer(page, path)
                        else:
                            self._png_writer(page, path, metadata=job.metadata)
                    except Exception as exc:
                        outputs.append(FormatExportResult(kind, path, False, str(exc), 'write'))
                    else:
                        outputs.append(FormatExportResult(kind, path, True))
                errors = '; '.join(f'{item.format}: {item.error}' for item in outputs if not item.success)
                result = BatchFileResult(job.snapshot.dataset_id, job.basename, tuple(outputs),
                                         error=errors, error_stage='write' if errors else '',
                                         graph_id=job.snapshot.graph_id, target_label=job.target_label)
        self._results.append(result)
        self._stepping = False
        if self._cancel_requested:
            self._cancel_remaining()
        return result


__all__ = ['BatchExportJob', 'FormatExportResult', 'BatchFileResult', 'BatchSummary',
           'next_export_paths', 'SerialPlotBatch']
