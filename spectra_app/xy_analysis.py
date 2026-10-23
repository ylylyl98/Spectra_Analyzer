"""Source-valued, detached XY analysis; no Qt or DAT/Gate assumptions."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field, replace
from datetime import datetime, timezone
import hashlib
import json
import numpy as np
from scipy.signal import peak_prominences

from .fitting import FitResult, find_peak_indices, fit_multi_peak
from .analysis_document import AnalysisSeed
from .peak_candidates import (PeakCandidate, CandidateFilter, candidate_filter_preset,
                              collect_peak_candidates, filter_peak_candidates)
from .analysis_graph import fit_curve_sheet, marker_curve_sheet
from .comparison_display import prepare_comparison
from .workbook_state import resolve_curve, sheet_columns, Sheet, Workbook


@dataclass(frozen=True)
class AnalysisSettings:
    mode: str = 'Peaks'
    prominence: float = .05
    min_distance: int = 3
    max_peaks: int = 6
    model: str = 'Gaussian'
    width_min: float = 1e-9
    width_max: float = 0.
    detector: str = 'Original'
    sg_window: int = 11
    sg_polyorder: int = 3

    def validate(self):
        if self.mode not in ('Peaks', 'Dips', 'Both') or self.model not in ('Gaussian', 'Lorentzian', 'Voigt'):
            raise ValueError('Choose a supported peak mode and model.')
        if self.detector not in ('Original', 'D2'):
            raise ValueError('Choose Original or D2 candidate detection.')
        for value in (self.min_distance, self.max_peaks, self.sg_window, self.sg_polyorder):
            if type(value) is not int:
                raise ValueError('Detection sample settings must be integers.')
        if self.sg_window < 3 or self.sg_window % 2 != 1 or not 2 <= self.sg_polyorder < self.sg_window:
            raise ValueError('Use an odd smoothing window greater than the polynomial order.')
        if not np.isfinite([self.prominence, self.width_min, self.width_max]).all():
            raise ValueError('Analysis settings must be finite.')
        if not 0 <= self.prominence <= 1 or self.min_distance < 1 or self.max_peaks < 1:
            raise ValueError('Use prominence from 0 to 1 and positive sample distance / peak count.')
        if self.width_min < 1e-9 or self.width_max < 0 or 0 < self.width_max <= self.width_min:
            raise ValueError('The maximum width must exceed the minimum width, or be 0 for automatic.')


@dataclass(frozen=True)
class XYData:
    x: np.ndarray
    y: np.ndarray
    valid: np.ndarray
    gap_count: int


@dataclass(frozen=True)
class Peaks:
    indices: np.ndarray
    kinds: np.ndarray


@dataclass(frozen=True)
class AnalysisInput:
    key: tuple
    curve_id: str
    data: XYData
    settings: AnalysisSettings
    provenance: dict


def prepare_xy(x, y, interval=None):
    """Copy in acquisition order, check monotonicity, reverse descending copies.

    Missing rows remain gaps for detection. Fits use the finite pairs only.
    The monotonic check covers every valid source pair, even outside the crop.
    """
    x, y = np.array(x, dtype=float, copy=True), np.array(y, dtype=float, copy=True)
    if x.ndim != 1 or y.shape != x.shape:
        raise ValueError('The selected curve needs matching one-dimensional X/Y columns.')
    valid = np.isfinite(x) & np.isfinite(y)
    diff = np.diff(x[valid])
    if diff.size and not (np.all(diff > 0) or np.all(diff < 0)):
        raise ValueError('X must be strictly monotonic with no duplicate coordinates. Select one monotonic sweep; data are never sorted.')
    if diff.size and np.all(diff < 0):
        x, y, valid = x[::-1].copy(), y[::-1].copy(), valid[::-1].copy()
    if interval is not None:
        inside = np.flatnonzero(valid & (x >= interval[0]) & (x <= interval[1]))
        if inside.size:
            section = slice(inside[0], inside[-1] + 1)
            x, y, valid = x[section].copy(), y[section].copy(), valid[section].copy()
        else:
            valid[:] = False
    if valid.sum() < 5:
        raise ValueError('Select an X interval with at least five finite X/Y pairs.')
    return XYData(x, y, valid, int((~valid).sum()))


def capture_input(workspace, comparison, curve_id, settings):
    settings.validate()
    curve = comparison.curves[curve_id]
    x, y = resolve_curve(workspace, curve)
    display = prepare_comparison(workspace, comparison)
    if comparison.x_range_mode == 'common' and display.x_limits is None:
        raise ValueError('No common X range. Show overlapping curves or choose All X / Manual X.')
    interval = display.x_limits
    data = prepare_xy(x, y, interval)
    sheet = workspace.sheets[curve.sheet_id]
    columns = sheet_columns(workspace, sheet, curve.mode)
    digest = hashlib.sha256(np.asarray(x, dtype=float).tobytes() + np.asarray(y, dtype=float).tobytes()).hexdigest()
    key = (comparison.comparison_id, curve_id, curve.sheet_id, curve.x_column, curve.y_column,
           curve.mode, comparison.x_range_mode, interval, digest, settings)
    provenance = dict(graph_id=comparison.comparison_id, graph=comparison.name,
        curve_id=curve_id, curve=curve.label, sheet_id=sheet.sheet_id, sheet=sheet.name,
        source_path=str(sheet.source_path or ''), x_column=curve.x_column, y_column=curve.y_column,
        x_label=columns[curve.x_column], y_label=columns[curve.y_column], source_mode=curve.mode,
        interval_mode=comparison.x_range_mode, interval=interval,
        actual_interval=[float(data.x[data.valid][0]), float(data.x[data.valid][-1])],
        settings=asdict(settings), captured_at=datetime.now(timezone.utc).isoformat(),
        missing_rows=data.gap_count, source_hash=digest,
        fitting='Finite source pairs; constant baseline; unweighted least squares')
    return AnalysisInput(key, curve_id, data, settings, provenance)


def detect_peaks(data, settings):
    settings.validate()
    # One global prominence threshold and count cap, including across gaps.
    global_spread = float(np.ptp(data.y[data.valid]))
    edges = np.flatnonzero(np.diff(np.r_[False, data.valid, False]))
    candidates = []
    for start, stop in edges.reshape(-1, 2):
        x, y = data.x[start:stop], data.y[start:stop]
        spread = float(np.ptp(y))
        if len(y) < 3 or spread == 0:
            continue
        indices, kinds = find_peak_indices(x, y, mode=settings.mode,
            prominence_frac=settings.prominence * global_spread / spread,
            min_distance=settings.min_distance, max_peaks=settings.max_peaks, max_dips=settings.max_peaks)
        for kind in (True, False):
            selected = indices[kinds == kind]
            strengths = peak_prominences(y if kind else -y, selected)[0]
            candidates.extend((float(strength), int(index + start), kind)
                              for strength, index in zip(strengths, selected))
    chosen = sorted(sorted(candidates, reverse=True)[:settings.max_peaks], key=lambda item: item[1])
    return Peaks(np.array([item[1] for item in chosen], dtype=int),
                 np.array([item[2] for item in chosen], dtype=bool))


def fit_peaks(data, settings, peaks, *, check_cancelled=None):
    settings.validate()
    if not peaks.indices.size:
        raise ValueError('Find peaks before fitting.')
    if int(data.valid.sum()) <= 1 + 3 * peaks.indices.size:
        raise ValueError('Too few finite samples for this many peaks. Reduce maximum peaks or widen the X interval.')
    return fit_multi_peak(data.x[data.valid], data.y[data.valid], centers_hint=data.x[peaks.indices],
        model=settings.model, mode=settings.mode, linewidth_min=settings.width_min,
        linewidth_max=settings.width_max, kinds=peaks.kinds, check_cancelled=check_cancelled)


def input_fingerprint(captured):
    """Stable source/interval identity without mutable display or fit settings."""
    return hashlib.sha256(json.dumps(captured.key[:-1], allow_nan=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def collect_candidates(data, settings):
    settings.validate()
    return collect_peak_candidates(data.x, data.y, mode=settings.mode, detector=settings.detector,
        sg_window=settings.sg_window, sg_polyorder=settings.sg_polyorder)


def fit_seeds(data, settings, seeds, *, check_cancelled=None):
    """Fit continuous edited centers; candidate source Y is never an amplitude seed."""
    settings.validate()
    seeds = [seed for seed in seeds if seed.enabled]
    if not seeds:
        raise ValueError('Find peaks or add an enabled seed before fitting.')
    x, y = data.x[data.valid], data.y[data.valid]
    spread = float(np.ptp(x))
    width_max = settings.width_max or max(spread*2 + 1e-9, settings.width_min*2)
    width = float(np.clip(spread / max(8*len(seeds), 20), settings.width_min, width_max))
    baseline = float(np.median(y))
    amplitude_scale = float(np.max(np.abs(y-baseline))) or abs(baseline) or 1.
    amplitudes = [seed.amplitude if seed.amplitude is not None else
        (1 if seed.kind == 'peak' else -1)*max(abs(float(np.interp(seed.position, x, y))-baseline), amplitude_scale*1e-6)
        for seed in seeds]
    return fit_multi_peak(x, y, centers_hint=np.array([seed.position for seed in seeds]),
        model=settings.model, mode='Both', linewidth_min=settings.width_min, linewidth_max=settings.width_max,
        kinds=np.array([seed.kind == 'peak' for seed in seeds]), initial_amplitudes=np.array(amplitudes),
        initial_widths=np.array([seed.width if seed.width is not None else width for seed in seeds]),
        initial_gammas=(np.array([seed.gamma if seed.gamma is not None else width for seed in seeds])
                        if settings.model == 'Voigt' else None),
        fixed_centers=np.array([seed.fixed_center for seed in seeds]),
        center_bounds=(np.array([seed.center_min if seed.center_min is not None else x[0] for seed in seeds]),
                       np.array([seed.center_max if seed.center_max is not None else x[-1] for seed in seeds])),
        check_cancelled=check_cancelled)


@dataclass
class XYAnalysisProgress:
    """Comparison-owned JSON progress; runtime jobs and source arrays stay outside it."""
    captured: AnalysisInput
    candidate_pool: list[PeakCandidate] = field(default_factory=list)
    filters: CandidateFilter = field(default_factory=lambda: candidate_filter_preset('Balanced'))
    seeds: list[AnalysisSeed] = field(default_factory=list)
    fit_result: FitResult | None = None
    status: str = 'pending'
    message: str = ''

    def accepted_seeds(self):
        return [seed for seed in self.seeds if seed.enabled]

    def peaks(self):
        valid = np.flatnonzero(self.captured.data.valid)
        x = self.captured.data.x
        accepted = self.accepted_seeds()
        return Peaks(np.array([valid[np.argmin(abs(x[valid]-seed.position))] for seed in accepted], dtype=int),
                     np.array([seed.kind == 'peak' for seed in accepted], dtype=bool))

    def retire_fit(self):
        self.fit_result = None
        self.status = 'detected' if self.accepted_seeds() else 'no-seed'
        self.message = ''

    def set_seeds(self, seeds):
        seeds = list(seeds)
        if any(not isinstance(seed, AnalysisSeed) for seed in seeds):
            raise ValueError('Use validated analysis seeds.')
        previous = {seed.seed_id: seed for seed in self.seeds}
        exclusions = []
        for index, seed in enumerate(seeds):
            old = previous.get(seed.seed_id)
            if old is not None and old.source_index is not None and old.kind != seed.kind:
                # A candidate's detector kind is immutable. A kind edit adds a
                # manual seed and explicitly excludes the old candidate.
                seeds[index] = replace(seed, source_index=None, manual=True)
                exclusions.append(AnalysisSeed(old.position, old.kind, enabled=False, manual=True,
                                               source_index=old.source_index))
        seeds = sorted([*seeds, *exclusions], key=lambda seed: (seed.position, seed.kind))
        data = self.captured.data
        low, high = data.x[data.valid][[0, -1]]
        if len({seed.seed_id for seed in seeds}) != len(seeds):
            raise ValueError('Seed IDs must be unique.')
        if any(not low <= seed.position <= high or
               seed.source_index is not None and (seed.source_index >= len(data.x) or not data.valid[seed.source_index])
               for seed in seeds):
            raise ValueError('Seed positions and indices must be inside the bound input.')
        identities = [(seed.source_index, seed.kind) for seed in seeds if seed.source_index is not None]
        if len(set(identities)) != len(identities):
            raise ValueError('A candidate can have only one seed override.')
        if seeds != self.seeds:
            self.seeds = seeds
            self.retire_fit()

    def set_candidates(self, candidates):
        self.candidate_pool = list(candidates)
        self.retire_fit()
        self.apply_filter(self.filters)
        if not self.candidate_pool and not self.accepted_seeds():
            self.status = 'empty'

    def apply_filter(self, settings):
        accepted = {(candidate.index, candidate.kind) for candidate in filter_peak_candidates(self.candidate_pool, settings)}
        existing = {(seed.source_index, seed.kind): seed for seed in self.seeds if seed.source_index is not None}
        seeds = [seed for seed in self.seeds if seed.manual and seed.source_index is None]
        for candidate in self.candidate_pool:
            identity = candidate.index, candidate.kind
            previous = existing.get(identity)
            if previous is not None and previous.manual:
                seeds.append(previous)
            else:
                seeds.append(replace(previous, enabled=identity in accepted) if previous else
                    AnalysisSeed(candidate.position, candidate.kind, enabled=identity in accepted,
                                 source_index=candidate.index))
        self.filters = settings
        self.set_seeds(seeds)

    def to_payload(self):
        fit = self.fit_result
        record = None
        if fit is not None:
            record = {key: getattr(fit, key).tolist() for key in
                      ('x', 'y_fit', 'components', 'centers', 'amplitudes', 'widths')}
            record.update(model=fit.model, baseline=float(fit.baseline), rmse=float(fit.rmse),
                r_squared=float(fit.r_squared) if np.isfinite(fit.r_squared) else None,
                sigmas=None if fit.sigmas is None else fit.sigmas.tolist(),
                gammas=None if fit.gammas is None else fit.gammas.tolist())
        payload = dict(version=1, curve_id=self.captured.curve_id,
            input_fingerprint=input_fingerprint(self.captured), provenance=self.captured.provenance,
            settings=asdict(self.captured.settings), candidate_pool=[item.to_dict() for item in self.candidate_pool],
            filter=self.filters.to_dict(), seeds=[seed.to_dict() for seed in self.seeds], fit=record,
            status=self.status, message=self.message)
        # Tuples become ordinary JSON lists; NaN/Infinity can never escape into project state.
        return json.loads(json.dumps(payload, allow_nan=False))

    @classmethod
    def from_payload(cls, workspace, comparison, payload):
        try:
            if not isinstance(payload, dict) or type(payload.get('version')) is not int or payload['version'] != 1:
                raise ValueError('Unsupported XY analysis progress version.')
            json.dumps(payload, allow_nan=False)
            settings = AnalysisSettings(**payload['settings'])
            captured = capture_input(workspace, comparison, payload['curve_id'], settings)
            if input_fingerprint(captured) != payload['input_fingerprint']:
                raise ValueError('Source data or scientific X interval changed; find candidates again.')
            provenance = payload['provenance']
            if (not isinstance(provenance, dict) or set(provenance) != set(captured.provenance)
                or any(not isinstance(provenance[key], str) for key, value in captured.provenance.items() if isinstance(value, str))
                or any(provenance.get(key) != captured.provenance[key] for key in
                    ('graph_id', 'curve_id', 'sheet_id', 'x_column', 'y_column', 'source_mode', 'source_hash'))):
                raise ValueError('Saved XY source provenance does not match its input.')
            for key in ('interval_mode', 'interval', 'actual_interval', 'missing_rows', 'settings'):
                if json.dumps(provenance[key], sort_keys=True) != json.dumps(captured.provenance[key], sort_keys=True):
                    raise ValueError('Saved XY analysis provenance has inconsistent settings or interval.')
            captured = replace(captured, provenance=provenance)
            progress = cls(captured, filters=CandidateFilter.from_dict(payload['filter']))
            candidates = [PeakCandidate.from_dict(item) for item in payload['candidate_pool']]
            data = captured.data
            if len({(item.index, item.kind) for item in candidates}) != len(candidates):
                raise ValueError('Duplicate XY candidates.')
            for item in candidates:
                if (item.index >= len(data.x) or not data.valid[item.index] or item.position != data.x[item.index]
                        or item.amplitude != data.y[item.index] or item.detector != settings.detector):
                    raise ValueError('Saved candidates do not match the bound input.')
            progress.candidate_pool = candidates
            progress.set_seeds([AnalysisSeed.from_dict(item) for item in payload['seeds']])
            if payload['status'] not in ('pending', 'detected', 'empty', 'no-seed', 'fitted', 'failed', 'cancelled'):
                raise ValueError('Invalid XY analysis status.')
            if not isinstance(payload['message'], str):
                raise ValueError('Invalid XY analysis status message.')
            record = payload['fit']
            if record is not None:
                arrays = {key: np.asarray(record[key], dtype=float) for key in
                          ('x', 'y_fit', 'components', 'centers', 'amplitudes', 'widths')}
                count, samples = len(progress.accepted_seeds()), int(data.valid.sum())
                shapes = dict(x=(samples,), y_fit=(samples,), components=(count, samples),
                              centers=(count,), amplitudes=(count,), widths=(count,))
                if count == 0 or any(value.shape != shapes[key] or not np.isfinite(value).all()
                                     for key, value in arrays.items()):
                    raise ValueError('Saved fit arrays have invalid dimensions or values.')
                if not np.array_equal(arrays['x'], data.x[data.valid]) or np.any(arrays['widths'] <= 0):
                    raise ValueError('Saved fit does not align with its input.')
                if record['model'] != settings.model or not np.isfinite([record['baseline'], record['rmse']]).all():
                    raise ValueError('Invalid saved fit model or metrics.')
                if record['rmse'] < 0 or record['r_squared'] is not None and not np.isfinite(record['r_squared']):
                    raise ValueError('Invalid saved fit metrics.')
                optional = {key: None if record[key] is None else np.asarray(record[key], dtype=float)
                            for key in ('sigmas', 'gammas')}
                if record['model'] == 'Voigt' and any(value is None or value.shape != (count,) or
                    not np.isfinite(value).all() or np.any(value <= 0) for value in optional.values()):
                    raise ValueError('Saved Voigt fit requires positive sigma and gamma arrays.')
                if record['model'] != 'Voigt' and any(value is not None for value in optional.values()):
                    raise ValueError('Only Voigt fits may contain sigma and gamma arrays.')
                for seed, center, amplitude in zip(progress.accepted_seeds(), arrays['centers'], arrays['amplitudes']):
                    lower = max(data.x[data.valid][0], seed.center_min) if seed.center_min is not None else data.x[data.valid][0]
                    upper = min(data.x[data.valid][-1], seed.center_max) if seed.center_max is not None else data.x[data.valid][-1]
                    if (not lower <= center <= upper or seed.fixed_center and center != seed.position
                            or amplitude * (1 if seed.kind == 'peak' else -1) < 0):
                        raise ValueError('Saved fit violates its seed signs or center constraints.')
                if not np.allclose(arrays['y_fit'], record['baseline'] + arrays['components'].sum(axis=0), rtol=1e-9, atol=1e-12):
                    raise ValueError('Saved total fit and components are inconsistent.')
                progress.fit_result = FitResult(model=record['model'], **arrays, **optional,
                    baseline=float(record['baseline']), rmse=float(record['rmse']),
                    r_squared=np.nan if record['r_squared'] is None else float(record['r_squared']))
            if (payload['status'] == 'fitted') != (progress.fit_result is not None):
                raise ValueError('Saved XY fit status is inconsistent.')
            progress.status, progress.message = payload['status'], payload['message']
            return progress
        except (KeyError, TypeError, IndexError, OverflowError) as exc:
            raise ValueError('Invalid saved XY analysis progress.') from exc


def result_workbook(captured, peaks, fit=None, *, seeds=None):
    """Build detached snapshot records; the caller commits them atomically."""
    if not peaks.indices.size:
        raise ValueError('There are no peaks to save.')
    note = json.dumps(captured.provenance, ensure_ascii=False, indent=2)
    data = captured.data
    indices = peaks.indices
    positions = data.x[indices] if seeds is None else np.array([seed.position for seed in seeds])
    amplitudes = data.y[indices] if seeds is None else np.interp(positions, data.x[data.valid], data.y[data.valid])
    peak_sheet = Sheet(name='Detected peaks', kind='snapshot', role='snapshot', snapshot_note=note,
        data=np.column_stack([positions, amplitudes, np.full(len(indices), np.nan)]),
        column_names=[captured.provenance['x_label'], captured.provenance['y_label'], 'Kind'],
        text_columns={2: ['Peak' if kind else 'Dip' for kind in peaks.kinds]})
    sheets = [peak_sheet]
    if fit is not None:
        count = len(fit.centers)
        width = 'Sigma' if fit.model in ('Gaussian', 'Voigt') else 'HWHM'
        sheets.append(Sheet(name='Fit parameters', kind='snapshot', role='snapshot', snapshot_note=note,
            data=np.column_stack([fit.centers, fit.amplitudes, fit.widths, np.full(count, fit.baseline),
                                  np.full(count, fit.rmse), np.full(count, fit.r_squared), np.full(count, np.nan)]),
            column_names=['Center', 'Amplitude', width, 'Baseline', 'RMSE', 'R-squared', 'Model'],
            text_columns={6: [fit.model] * count}))
        if fit.model == 'Voigt':
            sheets[-1].data = np.column_stack([sheets[-1].data, fit.gammas])
            sheets[-1].column_names.append('Gamma')
        sheets.append(fit_curve_sheet(data.x, data.y, data.valid, fit,
            x_label=captured.provenance['x_label'], y_label=captured.provenance['y_label'], note=note))
    else:
        if seeds is None:
            sheets.append(marker_curve_sheet(data.x, data.y, peaks.indices, peaks.kinds,
                x_label=captured.provenance['x_label'], y_label=captured.provenance['y_label'], note=note))
        else:
            # Separate sparse rows preserve edited continuous centers without changing source rows.
            off_sample = positions != data.x[indices]
            values = np.full((len(data.x) + int(off_sample.sum()), 4), np.nan)
            values[:len(data.x), :2] = np.column_stack([data.x, data.y])
            extra_row = len(data.x)
            for offset, seed in enumerate(seeds):
                row = extra_row if off_sample[offset] else indices[offset]
                if off_sample[offset]:
                    values[row, 0] = positions[offset]
                    extra_row += 1
                values[row, 2 if seed.kind == 'peak' else 3] = amplitudes[offset]
            sheets.append(Sheet(name='Peak curves', kind='snapshot', role='snapshot', snapshot_note=note,
                data=values, column_names=[captured.provenance['x_label'], captured.provenance['y_label'], 'Peaks', 'Dips'],
                y_columns=[1, 2, 3]))
    book = Workbook(name=f"Analysis — {captured.provenance['curve'] or captured.provenance['graph']}",
                    sheet_ids=[sheet.sheet_id for sheet in sheets])
    return book, sheets
