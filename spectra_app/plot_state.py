"""Small, versioned per-source plot recipes; data and rendering style stay separate."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from .dat_io import AXIS_OVERRIDE_ITEMS
from .title_templates import manual_settings, validate_title_settings


_BOUND_NAMES = ("xmin", "xmax", "ymin", "ymax", "vmin", "vmax")
_ANNOTATION_NAMES = ("plot_title", "y_axis_name", "y_axis_unit", "signal_title")


def source_fingerprint(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_plot_state(plot: object, *, version: int = 2) -> dict:
    """Reject the whole recipe before any UI mutation, including non-finite JSON."""
    def exact_keys(value, keys):
        if not isinstance(value, dict) or set(value) != set(keys):
            raise ValueError("Unexpected plot recipe fields")

    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    keys = ("axis_override", "colormap", "annotations", "fix_annotations",
            "bounds", "locks", "x_limits", "y_limits", "color_limits", "gate")
    exact_keys(plot, keys if version == 1 else (*keys, "title_settings"))
    if version == 2:
        validate_title_settings(plot["title_settings"])
    if plot["axis_override"] not in [item[0] for item in AXIS_OVERRIDE_ITEMS]:
        raise ValueError("Unknown saved axis interpretation")
    if not isinstance(plot["colormap"], str):
        raise ValueError("Invalid saved colormap")
    exact_keys(plot["annotations"], _ANNOTATION_NAMES)
    if not all(isinstance(value, str) for value in plot["annotations"].values()):
        raise ValueError("Invalid saved annotation")
    exact_keys(plot["bounds"], _BOUND_NAMES)
    exact_keys(plot["locks"], _BOUND_NAMES)
    if not all(number(value) and abs(value) <= 1e12 for value in plot["bounds"].values()):
        raise ValueError("Invalid saved bound")
    if not all(type(value) is bool for value in plot["locks"].values()):
        raise ValueError("Invalid saved lock")
    if type(plot["fix_annotations"]) is not bool or not number(plot["gate"]):
        raise ValueError("Invalid saved plot setting")
    for name in ("x_limits", "y_limits", "color_limits"):
        pair = plot[name]
        if (not isinstance(pair, list) or len(pair) != 2
                or not all(number(value) for value in pair) or pair[0] == pair[1]
                or (name == "color_limits" and pair[0] > pair[1])):
            raise ValueError("Invalid saved plot limits")
    if version == 1:
        return {**plot, "title_settings": manual_settings(plot["annotations"]["plot_title"])}
    return plot


class PlotStateStore:
    def path_for(self, path: Path) -> Path:
        return path.with_name(path.name + ".plot.json")

    def load(self, path: Path, fingerprint: str) -> tuple[dict | None, str]:
        try:
            with self.path_for(path).open(encoding="utf-8") as stream:
                payload = json.load(stream)
        except FileNotFoundError:
            return None, ""
        except (OSError, ValueError, UnicodeError):
            return None, "Saved plot settings ignored: the recipe could not be read."
        try:
            if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] not in (1, 2):
                raise ValueError("Incompatible recipe")
            if payload.get("fingerprint") != fingerprint:
                return None, "Saved plot settings ignored: DAT content has changed."
            return validate_plot_state(payload.get("plot"), version=payload["version"]), ""
        except (ValueError, TypeError, OverflowError):
            return None, "Saved plot settings ignored: the recipe is invalid or incompatible."

    def save(self, path: Path, fingerprint: str, plot: dict) -> None:
        validate_plot_state(plot)
        # The sidecar's name associates the pair; the informational name is portable.
        payload = {"version": 2, "source_file": path.name,
                   "fingerprint": fingerprint, "plot": plot}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path_for(path))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
