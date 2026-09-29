from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

import numpy as np

from .signal_titles import infer_signal_kind


AXIS_TYPE_EV = "ev"
AXIS_TYPE_MEV = "mev"
AXIS_TYPE_NM = "nm"
AXIS_TYPE_CM1 = "cm^-1"
AXIS_TYPE_PIXEL = "pixel"
AXIS_TYPE_UNKNOWN = "unknown"

AXIS_OVERRIDE_ITEMS: tuple[tuple[str | None, str], ...] = (
    (None, "Auto Detect"),
    (AXIS_TYPE_EV, "Photon Energy (eV)"),
    (AXIS_TYPE_MEV, "Photon Energy (meV)"),
    (AXIS_TYPE_NM, "Wavelength (nm)"),
    (AXIS_TYPE_CM1, "Wavenumber (cm^-1)"),
    (AXIS_TYPE_PIXEL, "Pixel / Channel / Index"),
    (AXIS_TYPE_UNKNOWN, "Unknown / Keep Raw"),
)

_AXIS_LABELS = {
    AXIS_TYPE_EV: "Photon Energy (eV)",
    AXIS_TYPE_MEV: "Photon Energy (meV)",
    AXIS_TYPE_NM: "Wavelength (nm)",
    AXIS_TYPE_CM1: "Wavenumber (cm^-1)",
    AXIS_TYPE_PIXEL: "Pixel / Channel / Index",
    AXIS_TYPE_UNKNOWN: "X Axis",
}

_SEP_RE = re.compile(r"[\t, ]+")
_TOKEN_SEP_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class AxisInterpretation:
    axis_type: str
    axis_label: str
    source: str
    note: str
    ambiguous: bool
    photon_energy: np.ndarray | None
    photon_energy_note: str


@dataclass(frozen=True)
class DataCube:
    energy: np.ndarray
    gate: np.ndarray
    z: np.ndarray  # shape: (n_gate, n_energy)
    gate_label: str
    title: str
    source_path: str
    x_original: np.ndarray
    x_original_type: str
    x_original_label: str
    x_detection_source: str
    x_detection_note: str
    x_is_ambiguous: bool
    x_override: str | None
    photon_energy: np.ndarray | None
    photon_energy_note: str
    energy_label: str
    signal_kind: str = "unknown"

    @property
    def has_photon_energy(self) -> bool:
        return self.photon_energy is not None


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _tokens(line: str) -> list[str]:
    return [tok for tok in _SEP_RE.split(line.strip()) if tok]


def list_dat_folders(root: str | Path) -> list[Path]:
    base = Path(root)
    if not base.exists() or not base.is_dir():
        return []
    out: list[Path] = []
    for d in [base, *sorted([p for p in base.rglob("*") if p.is_dir()])]:
        try:
            if any(f.is_file() and f.suffix.lower() == ".dat" for f in d.iterdir()):
                out.append(d)
        except OSError:
            continue
    return out


def list_dat_files(folder: str | Path) -> list[str]:
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        return []
    try:
        return sorted([f.name for f in p.glob("*.dat") if f.is_file()])
    except OSError:
        return []


def _parse_header_gates(header_tokens: Iterable[str]) -> np.ndarray:
    vals: list[float] = []
    for t in list(header_tokens)[1:]:
        if _is_number(t):
            vals.append(float(t))
    return np.asarray(vals, dtype=float)


def _is_monotonic(values: np.ndarray) -> bool:
    if values.size < 2:
        return False
    diffs = np.diff(values)
    nz = diffs[np.abs(diffs) > 1e-12]
    if nz.size == 0:
        return False
    return bool(np.all(nz > 0.0) or np.all(nz < 0.0))


def _has_outlier_leading_step(values: np.ndarray, factor: float = 20.0) -> bool:
    if values.size < 4:
        return False
    tail_diffs = np.abs(np.diff(values[1:]))
    tail_diffs = tail_diffs[tail_diffs > 1e-12]
    if tail_diffs.size == 0:
        return False
    return bool(abs(values[1] - values[0]) > factor * float(np.median(tail_diffs)))


def _normalize_text(text: str) -> str:
    return _TOKEN_SEP_RE.sub(" ", text.strip().lower())


def _metadata_axis_type(text: str) -> str | None:
    norm = _normalize_text(text)
    if not norm:
        return None
    if "wavelength" in norm or " lambda " in f" {norm} " or re.search(r"\bnm\b", norm):
        return AXIS_TYPE_NM
    if "photon energy" in norm or re.search(r"\bev\b", norm):
        return AXIS_TYPE_EV
    if re.search(r"\bmev\b", norm) or "milli electron volt" in norm:
        return AXIS_TYPE_MEV
    if "wavenumber" in norm or "raman shift" in norm or "cm 1" in norm or "cm -1" in norm:
        return AXIS_TYPE_CM1
    if "pixel" in norm or "channel" in norm or "index" in norm or "ccd" in norm:
        return AXIS_TYPE_PIXEL
    return None


def _heuristic_axis_type(values: np.ndarray) -> tuple[str, str, bool]:
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size < 3:
        return AXIS_TYPE_UNKNOWN, "Not enough finite x-axis values for numeric heuristics.", True

    abs_vals = np.abs(vals)
    vmin = float(np.nanmin(vals))
    vmax = float(np.nanmax(vals))
    median = float(np.nanmedian(vals))
    diffs = np.diff(vals)
    nz_diffs = np.abs(diffs[np.abs(diffs) > 1e-12])
    median_step = float(np.nanmedian(nz_diffs)) if nz_diffs.size else 0.0
    rounded = np.round(vals)
    integer_like = bool(np.all(np.abs(vals - rounded) < 1e-6))

    if integer_like and median_step <= 2.0 and abs(vmax - vmin) <= max(vals.size * 2.5, 16.0):
        return AXIS_TYPE_PIXEL, "Numeric heuristics: integer-like axis with index-style spacing.", False

    if np.all(vals > 0.0) and 200.0 <= median <= 2000.0 and vmax <= 5000.0:
        return AXIS_TYPE_NM, "Numeric heuristics: positive axis in a wavelength-like nm range.", False

    if float(np.nanmax(abs_vals)) <= 20.0:
        return AXIS_TYPE_EV, "Numeric heuristics: compact x-axis range consistent with photon energy in eV.", False

    if float(np.nanmax(abs_vals)) <= 10000.0 and median_step < 100.0:
        return AXIS_TYPE_MEV, "Numeric heuristics: x-axis magnitude is consistent with meV, but this is ambiguous.", True

    if np.all(vals >= 0.0) and vmax <= 50000.0:
        return AXIS_TYPE_CM1, "Numeric heuristics: positive spectral axis may be wavenumber, but this is ambiguous.", True

    return AXIS_TYPE_UNKNOWN, "Could not confidently infer x-axis type from metadata or numeric heuristics.", True


def _convert_to_photon_energy(axis_type: str, x_values: np.ndarray) -> tuple[np.ndarray | None, str]:
    x = np.asarray(x_values, dtype=float)
    if axis_type == AXIS_TYPE_EV:
        return np.asarray(x, dtype=float), "Detected photon energy in eV; no conversion applied."
    if axis_type == AXIS_TYPE_MEV:
        return np.asarray(x / 1000.0, dtype=float), "Converted photon energy from meV to eV."
    if axis_type == AXIS_TYPE_NM:
        if np.any(~np.isfinite(x)) or np.any(x <= 0.0):
            return None, "Detected wavelength in nm, but conversion failed because some values are non-positive."
        return np.asarray(1240.0 / x, dtype=float), "Converted wavelength to photon energy with 1240 / wavelength_nm."
    if axis_type == AXIS_TYPE_CM1:
        return None, "Detected wavenumber axis; not converted because photon energy mapping is not reliable here."
    if axis_type == AXIS_TYPE_PIXEL:
        return None, "Detected pixel/index axis; not converted because no reliable calibration is available."
    return None, "Photon energy unavailable because x-axis type is unknown."


def interpret_x_axis(
    x_values: np.ndarray,
    *,
    x_label_hint: str = "",
    metadata_lines: Iterable[str] = (),
    override: str | None = None,
) -> AxisInterpretation:
    if override is not None:
        axis_type = override
        source = "manual override"
        note = f"Manual override applied: {_AXIS_LABELS.get(axis_type, 'X Axis')}."
        ambiguous = False
    else:
        axis_type = _metadata_axis_type(x_label_hint) if x_label_hint else None
        if axis_type is not None:
            source = "header label"
            note = f"Detected from x-axis header '{x_label_hint}'."
            ambiguous = False
        else:
            meta_type = _metadata_axis_type(" ".join(str(v) for v in metadata_lines))
            if meta_type is not None:
                axis_type = meta_type
                source = "metadata"
                note = "Detected from DAT metadata text."
                ambiguous = False
            else:
                axis_type, note, ambiguous = _heuristic_axis_type(x_values)
                source = "numeric heuristics"

    photon_energy, photon_note = _convert_to_photon_energy(axis_type, x_values)
    return AxisInterpretation(
        axis_type=axis_type,
        axis_label=_AXIS_LABELS.get(axis_type, "X Axis"),
        source=source,
        note=note,
        ambiguous=ambiguous,
        photon_energy=photon_energy,
        photon_energy_note=photon_note,
    )


def load_dat_cube(path: str | Path, *, x_axis_override: str | None = None) -> DataCube:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")

    with p.open("r", encoding="utf-8", errors="ignore") as fh:
        raw_lines = [line.rstrip("\n\r") for line in fh if line.strip()]
    if not raw_lines:
        raise ValueError(f"Empty DAT file: {p.name}")

    metadata_lines: list[str] = []
    numeric_lines: list[str] = []
    header_tokens: list[str] | None = None

    for line in raw_lines:
        tok = _tokens(line)
        if len(tok) < 2:
            metadata_lines.append(line)
            continue
        all_numeric = all(_is_number(t) for t in tok)
        if all_numeric:
            numeric_lines.append(line)
            continue
        numeric_tail = sum(1 for t in tok[1:] if _is_number(t))
        if header_tokens is None and (not _is_number(tok[0])) and numeric_tail >= max(1, len(tok) - 2):
            header_tokens = tok
            continue
        metadata_lines.append(line)

    if not numeric_lines:
        raise ValueError(f"No numeric matrix rows found in {p.name}")

    rows = [[float(t) for t in _tokens(line)] for line in numeric_lines]
    arr = np.asarray(rows, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError(f"Invalid matrix shape in {p.name}: {arr.shape}")

    header_gates = _parse_header_gates(header_tokens) if header_tokens is not None else np.asarray([], dtype=float)
    x_label_hint = header_tokens[0] if header_tokens is not None else ""

    # Some exports prefix the numeric gate header with a zero-padded title
    # and an all-zero separator. Require the complete layout before removing
    # either row so ordinary zero-valued measurements remain intact.
    padded_title_layout = (
        header_tokens is not None
        and len(header_tokens) == arr.shape[1]
        and header_gates.size == arr.shape[1] - 1
        and np.all(header_gates == 0.0)
        and arr.shape[0] >= 4
        and arr.shape[1] >= 3
        and np.all(arr[0] == 0.0)
        and arr[1, 0] == 0.0
        and np.all(np.isfinite(arr[1:, 0]))
        and np.all(np.isfinite(arr[1, 1:]))
        and _is_monotonic(arr[1, 1:])
        and _is_monotonic(arr[2:, 0])
    )
    if padded_title_layout:
        metadata_lines.append(" ".join(header_tokens))
        header_gates = np.asarray([], dtype=float)
        x_label_hint = ""
        arr = arr[1:]

    numeric_header_layout = padded_title_layout
    if not numeric_header_layout and header_tokens is None and arr.shape[0] >= 3 and arr.shape[1] >= 3:
        numeric_header_layout = (
            _is_monotonic(arr[0, 1:])
            and _is_monotonic(arr[1:, 0])
            and _has_outlier_leading_step(arr[:, 0])
        )

    if numeric_header_layout:
        x_original = arr[1:, 0]
        vals = arr[1:, 1:]
        gate = arr[0, 1:]
        gate_label = "Gate"
    else:
        x_original = arr[:, 0]
        vals = arr[:, 1:]
        gate = np.arange(vals.shape[1], dtype=float)
        gate_label = "Gate Index"

    if header_gates.size and header_gates.size == vals.shape[1]:
        gate = header_gates
        gate_label = "Gate"

    if not np.all(np.isfinite(x_original)):
        raise ValueError(f"X-axis contains invalid values in {p.name}")
    if not np.all(np.isfinite(gate)):
        raise ValueError(f"Gate axis contains invalid values in {p.name}")

    x_info = interpret_x_axis(
        x_original,
        x_label_hint=x_label_hint,
        metadata_lines=metadata_lines,
        override=x_axis_override,
    )
    energy = x_info.photon_energy if x_info.photon_energy is not None else np.asarray(x_original, dtype=float)
    energy_label = "Photon Energy (eV)" if x_info.photon_energy is not None else x_info.axis_label

    z = vals.T
    if not np.any(np.isfinite(z)):
        raise ValueError(f"Intensity matrix has no finite values in {p.name}")

    order_x = np.argsort(energy)
    energy = np.asarray(energy[order_x], dtype=float)
    x_original = np.asarray(x_original[order_x], dtype=float)
    if x_info.photon_energy is not None:
        photon_energy = np.asarray(x_info.photon_energy[order_x], dtype=float)
    else:
        photon_energy = None
    z = np.asarray(z[:, order_x], dtype=float)

    order_g = np.argsort(gate)
    gate = np.asarray(gate[order_g], dtype=float)
    z = np.asarray(z[order_g, :], dtype=float)

    return DataCube(
        energy=energy,
        gate=gate,
        z=z,
        gate_label=str(gate_label),
        title=p.stem,
        source_path=str(p),
        x_original=x_original,
        x_original_type=x_info.axis_type,
        x_original_label=x_info.axis_label,
        x_detection_source=x_info.source,
        x_detection_note=x_info.note,
        x_is_ambiguous=bool(x_info.ambiguous),
        x_override=x_axis_override,
        photon_energy=photon_energy,
        photon_energy_note=x_info.photon_energy_note,
        energy_label=energy_label,
        signal_kind=infer_signal_kind(str(p), metadata_lines),
    )
