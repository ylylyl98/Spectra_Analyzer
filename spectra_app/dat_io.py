from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class DataCube:
    energy: np.ndarray
    gate: np.ndarray
    z: np.ndarray  # shape: (n_gate, n_energy)
    gate_label: str
    title: str


_SEP_RE = re.compile(r"[\t, ]+")


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


def load_dat_cube(path: str | Path) -> DataCube:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")

    with p.open("r", encoding="utf-8", errors="ignore") as fh:
        raw_lines = [line.rstrip("\n\r") for line in fh if line.strip()]
    if not raw_lines:
        raise ValueError(f"Empty DAT file: {p.name}")

    first_tokens = _tokens(raw_lines[0])
    has_header = len(first_tokens) > 2 and (not _is_number(first_tokens[0]))
    header_gates = _parse_header_gates(first_tokens) if has_header else np.asarray([], dtype=float)

    rows: list[list[float]] = []
    data_lines = raw_lines[1:] if has_header else raw_lines
    for line in data_lines:
        tok = _tokens(line)
        if len(tok) < 2:
            continue
        if not all(_is_number(t) for t in tok):
            continue
        rows.append([float(t) for t in tok])

    if not rows:
        raise ValueError(f"No numeric matrix rows found in {p.name}")

    arr = np.asarray(rows, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError(f"Invalid matrix shape in {p.name}: {arr.shape}")

    energy = arr[:, 0]
    vals = arr[:, 1:]  # (n_energy, n_gate)

    if header_gates.size and header_gates.size == vals.shape[1]:
        gate = header_gates
        gate_label = first_tokens[0]
    else:
        gate = np.arange(vals.shape[1], dtype=float)
        gate_label = "Gate Index"

    z = vals.T  # (n_gate, n_energy)

    if not np.all(np.isfinite(energy)):
        raise ValueError(f"Energy axis contains invalid values in {p.name}")
    if not np.all(np.isfinite(gate)):
        raise ValueError(f"Gate axis contains invalid values in {p.name}")
    if not np.any(np.isfinite(z)):
        raise ValueError(f"Intensity matrix has no finite values in {p.name}")

    order_e = np.argsort(energy)
    energy = energy[order_e]
    z = z[:, order_e]

    order_g = np.argsort(gate)
    gate = gate[order_g]
    z = z[order_g, :]

    return DataCube(
        energy=np.asarray(energy, dtype=float),
        gate=np.asarray(gate, dtype=float),
        z=np.asarray(z, dtype=float),
        gate_label=str(gate_label),
        title=p.stem,
    )
