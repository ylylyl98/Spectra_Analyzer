"""Bundled custom Matplotlib colormaps."""

from functools import lru_cache
from pathlib import Path

import numpy as np
from matplotlib.colors import ListedColormap


CUSTOM_COLORMAP_NAMES = ("RdBu_r_p0p45", "RdBu_r_p0p6")
COMMON_COLORMAPS = (("jet", "jet"), ("RdBu_r", "RdBu_r"), ("RdBu_r_0.6", "RdBu_r_p0p6"))


def canonical_colormap(name: str) -> str:
    return 'RdBu_r_p0p6' if name == 'RdBu_r_0.6' else name
_PALETTE_FILES = {
    name: Path(__file__).with_name("palettes") / f"{name}.pal"
    for name in CUSTOM_COLORMAP_NAMES
}


def _read_jasc_palette(path: Path) -> np.ndarray:
    lines = path.read_text(encoding="ascii").splitlines()
    if len(lines) < 3 or lines[:2] != ["JASC-PAL", "0100"]:
        raise ValueError(f"Invalid JASC-PAL header in {path}")
    try:
        expected_count = int(lines[2])
    except ValueError as exc:
        raise ValueError(f"Invalid JASC-PAL color count in {path}") from exc

    rows: list[list[int]] = []
    for line in lines[3:]:
        fields = line.split()
        if len(fields) != 3:
            raise ValueError(f"Invalid JASC-PAL RGB row in {path}")
        try:
            row = [int(value) for value in fields]
        except ValueError as exc:
            raise ValueError(f"Invalid JASC-PAL RGB row in {path}") from exc
        if any(value < 0 or value > 255 for value in row):
            raise ValueError(f"Out-of-range JASC-PAL RGB row in {path}")
        rows.append(row)

    colors = np.asarray(rows, dtype=float)
    if colors.shape != (expected_count, 3):
        raise ValueError(f"JASC-PAL color count mismatch in {path}")
    return colors / 255.0


@lru_cache(maxsize=None)
def resolve_colormap(name: str) -> str | ListedColormap:
    """Return a bundled ListedColormap or the original Matplotlib name."""
    name = canonical_colormap(name)
    path = _PALETTE_FILES.get(name)
    if path is None:
        return name
    return ListedColormap(_read_jasc_palette(path), name=name)
