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
from .plot_presentation import NormSpec, PlotPresentation
from .signal_titles import RAW_LABEL
from .svg_export import build_color_norm


_BOUND_NAMES = ("xmin", "xmax", "ymin", "ymax", "vmin", "vmax")
_ANNOTATION_NAMES = ("plot_title", "y_axis_name", "y_axis_unit", "signal_title")
_REGIONAL_NAMES = ("color_scale_mode", "color_region_boundaries")
_PROCESSING_DEFAULT = {
    "mode": "original", "smooth": True, "window_length": 21,
    "original_signal_title": RAW_LABEL,
}


def source_fingerprint(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_plot_state(plot: object, *, version: int = 3) -> dict:
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
    if version not in (1, 2, 3):
        raise ValueError("Incompatible recipe version")
    norm, presentation = NormSpec(), PlotPresentation(width_in=3.30, frame_width_in=None, frame_height_in=None)
    region_limits = []
    if version == 3:
        plot = dict(plot)
        norm = NormSpec.from_dict(plot.pop('norm', {}))
        presentation = PlotPresentation.from_dict(plot.pop('presentation', {}))
        region_limits = plot.pop('color_region_limits', [])
        version = 2
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
    if not isinstance(region_limits, list):
        raise ValueError('Invalid saved regional color limits')
    count = int(plot['color_scale_mode']) if plot['color_scale_mode'] in ('2', '3') else 0
    if region_limits and len(region_limits) != count:
        raise ValueError('Saved regional color limits do not match the region count')
    for pair in region_limits:
        if (not isinstance(pair, list) or len(pair) != 2 or not all(number(value) for value in pair)):
            raise ValueError('Invalid saved regional color limits')
        build_color_norm(norm, pair)
    build_color_norm(norm, plot['color_limits'])
    if version == 1:
        plot = {**plot, "title_settings": manual_settings(plot["annotations"]["plot_title"])}
    return {**plot, 'processing': dict(processing), 'norm': norm.to_dict(),
            'presentation': presentation.to_dict(), 'color_region_limits': region_limits}


class PlotCollection(dict):
    """Canonical collection with read-only legacy active-recipe key projection.

    JSON/dict iteration contains only collection fields; no saved active copy.
    """
    def __missing__(self, key):
        return active_plot_recipe(self)[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def active_plot_recipe(collection):
    if 'graphs' not in collection:
        return collection
    graph = next(record for record in collection['graphs']
                 if record['graph_id'] == collection['active_graph_id'])
    return {**graph['plot'], 'axis_override': collection['axis_override'],
            'processing': collection['processing']}


def validate_plot_collection(value, *, version=6):
    from .dataset_state import validate_graph_source
    if version in (1, 2, 3):
        recipe = validate_plot_state(value, version=version)
        axis, processing = recipe.pop('axis_override'), recipe.pop('processing')
        return PlotCollection(axis_override=axis, processing=processing,
                              active_graph_id='default', graphs=[{
                                  'graph_id': 'default', 'name': 'Graph 1', 'plot': recipe,
                                  'source_mode': 'processed', 'gate_indices': None, 'published': True}])
    if (version not in (4, 5, 6) or not isinstance(value, dict)
            or set(value) != {'axis_override', 'processing', 'graphs', 'active_graph_id'}
            or not isinstance(value['graphs'], list) or not value['graphs']):
        raise ValueError('Invalid graph collection.')
    ids, names, records = set(), set(), []
    for record in value['graphs']:
        fields = {'graph_id', 'name', 'plot'} | ({'source_mode', 'gate_indices'} if version >= 5 else set())
        if version >= 6:
            fields.add('published')
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError('Invalid graph record.')
        published = record.get('published', True)
        if type(published) is not bool:
            raise ValueError('Graph publication must be boolean.')
        source_mode, gate_indices = record.get('source_mode', 'processed'), record.get('gate_indices')
        validate_graph_source(source_mode, gate_indices)
        graph_id, name, plot = record['graph_id'], record['name'], record['plot']
        if (not isinstance(graph_id, str) or not graph_id.strip() or '\x00' in graph_id
                or graph_id in ids or not isinstance(name, str) or not name.strip()
                or name != name.strip() or '\x00' in name or name in names):
            raise ValueError('Graph IDs and names must be nonempty and unique.')
        if not isinstance(plot, dict) or 'processing' in plot or 'axis_override' in plot:
            raise ValueError('Shared processing and axis belong to the DAT.')
        normalized = validate_plot_state({**plot, 'processing': value['processing'],
                                         'axis_override': value['axis_override']})
        if set(plot) != set(normalized) - {'processing', 'axis_override'}:
            raise ValueError('Invalid or missing graph fields.')
        axis, processing = normalized.pop('axis_override'), normalized.pop('processing')
        records.append({'graph_id': graph_id, 'name': name, 'plot': normalized,
                        'published': published,
                        'source_mode': source_mode, 'gate_indices': None if gate_indices is None else list(gate_indices)})
        ids.add(graph_id)
        names.add(name)
    if not isinstance(value['active_graph_id'], str) or value['active_graph_id'] not in ids:
        raise ValueError('Active graph does not exist.')
    return PlotCollection(axis_override=axis, processing=processing, graphs=records,
                          active_graph_id=value['active_graph_id'])


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
            if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] not in (1, 2, 3, 4, 5, 6):
                raise ValueError("Incompatible recipe")
            if payload.get("fingerprint") != fingerprint:
                return None, "Saved plot settings ignored: DAT content has changed."
            return validate_plot_collection(payload.get("plot"), version=payload["version"]), ""
        except (ValueError, TypeError, OverflowError):
            return None, "Saved plot settings ignored: the recipe is invalid or incompatible."

    def save(self, path: Path, fingerprint: str, plot: dict) -> None:
        plot = validate_plot_collection(plot, version=6 if 'graphs' in plot else 3)
        # The sidecar's name associates the pair; the informational name is portable.
        payload = {"version": 6, "source_file": path.name,
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
