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
_REGIONAL_NAMES = ("color_scale_mode", "color_region_boundaries")
_PROCESSING_DEFAULT = {
    "mode": "original", "smooth": True, "window_length": 21,
    "original_signal_title": "ΔR/R (a. u.)",
}


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
    if not isinstance(plot, dict):
        raise ValueError("Unexpected plot recipe fields")
    signal_auto = plot.get("signal_title_auto", False)
    if type(signal_auto) is not bool:
        raise ValueError("Invalid automatic signal title setting")
    plot = {key: value for key, value in plot.items() if key != "signal_title_auto"}
    legacy_keys = keys if version == 1 else (*keys, "title_settings")
    current_keys = (*legacy_keys, *_REGIONAL_NAMES)
    processing_keys = (*current_keys, "processing")
    if version == 1:
        if set(plot) == set(keys):
            plot = {**plot, "color_scale_mode": "global", "color_region_boundaries": []}
        else:
            exact_keys(plot, (*keys, *_REGIONAL_NAMES))
    elif set(plot) == set(legacy_keys):
        exact_keys(plot, legacy_keys)
        plot = {**plot, "color_scale_mode": "global", "color_region_boundaries": []}
    else:
        if set(plot) == set(current_keys):
            plot = {**plot, "processing": dict(_PROCESSING_DEFAULT)}
        else:
            exact_keys(plot, processing_keys)
    if version == 2:
        validate_title_settings(plot["title_settings"])
    if version in (1, 2):
        if plot["color_scale_mode"] not in ("global", "2", "3"):
            raise ValueError("Invalid saved color scale mode")
        boundaries = plot["color_region_boundaries"]
        if (not isinstance(boundaries, list) or len(boundaries) not in (0, 1, 2)
                or not all(number(value) for value in boundaries)
                or any(left >= right for left, right in zip(boundaries, boundaries[1:]))):
            raise ValueError("Invalid saved color region boundaries")
    processing = plot.get("processing", _PROCESSING_DEFAULT)
    if not isinstance(processing, dict):
        raise ValueError("Invalid saved processing settings")
    if set(processing) == {"mode", "smooth", "window_length"}:
        processing = {**processing,
                      "original_signal_title": _PROCESSING_DEFAULT["original_signal_title"]}
    exact_keys(processing, ("mode", "smooth", "window_length", "original_signal_title"))
    if processing["mode"] not in ("original", "energy_second_derivative"):
        raise ValueError("Invalid saved processing mode")
    if type(processing["smooth"]) is not bool:
        raise ValueError("Invalid saved smoothing setting")
    if not isinstance(processing["original_signal_title"], str):
        raise ValueError("Invalid saved original signal title")
    if (type(processing["window_length"]) is not int
            or processing["window_length"] < 5
            or processing["window_length"] % 2 == 0
            or processing["window_length"] > 1001):
        raise ValueError("Invalid saved smoothing window")
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
    plot = {**plot, "signal_title_auto": signal_auto}
    if version == 1:
        return {**plot, "title_settings": manual_settings(plot["annotations"]["plot_title"]),
                "processing": dict(processing)}
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
        plot = validate_plot_state(plot)
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
