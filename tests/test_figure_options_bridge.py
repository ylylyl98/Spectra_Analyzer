"""Complete live Figure options domain proof using real Matplotlib artists."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import ListedColormap, to_rgba
from matplotlib.dates import DateConverter, date2num
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from spectra_app.figure_options import FigureOptionsBridge

APP = QApplication.instance() or QApplication([])


class Navigation(QObject):
    stateChanged = Signal()

    def __init__(self, figure):
        super().__init__()
        self.ownerToken = 'source|navigation:1'
        self.enabled = True
        self.toolbar = SimpleNamespace(canvas=figure.canvas, push_current=Mock())

    def allowed(self, owner=None):
        return self.enabled and (owner is None or owner == self.ownerToken)


class FigureOptionsBridgeTests(unittest.TestCase):
    def setUp(self):
        self.figure = Figure()
        FigureCanvasAgg(self.figure)
        self.axes = self.figure.subplots()
        self.line, = self.axes.plot([1, 2], [2, 4], label='curve', alpha=.5,
                                    marker='o', markerfacecolor='red')
        self.hidden, = self.axes.plot([1, 2], [3, 5], label='_nolegend_')
        self.custom = ListedColormap(['red', 'blue'], name='custom-map')
        self.image = self.axes.imshow(np.arange(4).reshape(2, 2), cmap=self.custom)
        self.collection = self.axes.scatter([1, 2], [1, 2], c=[2, 6])
        self.axes.scatter([1], [1], color='red')  # No array: ineligible.
        self.navigation = Navigation(self.figure)
        self.bridge = FigureOptionsBridge(self.navigation, self.axes)
        self.figure.canvas.draw = Mock()

    def tearDown(self):
        self.bridge.shutdown()

    def test_complete_stable_schema_and_numeric_rgba(self):
        self.assertEqual([a['name'] for a in self.bridge.axes], ['x', 'y'])
        self.assertEqual(len(self.bridge.curves), 1)
        self.assertEqual(len(self.bridge.mappables), 2)
        self.assertTrue(self.bridge.mappables[0]['isImage'])
        self.assertFalse(self.bridge.mappables[1]['isImage'])
        self.assertNotIn('mappable.1.interpolation', self.bridge.drafts)
        self.assertEqual(self.bridge.colorPreview('curve.0.color'), list(to_rgba('#1f77b480')))
        schema = self.bridge.curves
        self.bridge.setDraft('curve.0.label', 'new')
        self.assertEqual(self.bridge.curves, schema)
        self.figure.canvas.draw.assert_not_called()

    def test_all_fields_apply_actual_artists_and_navigation_once(self):
        changes = {'axes.title': 'Edited', 'axes.legend': True,
                   'axis.x.min': '5', 'axis.x.max': '1', 'axis.x.label': 'X label',
                   'axis.x.scale': 'linear', 'axis.y.label': 'Y label',
                   'curve.0.label': 'edited curve', 'curve.0.linestyle': '--',
                   'curve.0.drawstyle': 'steps-post', 'curve.0.width': '2.5',
                   'curve.0.color': '#12345678', 'curve.0.marker': 's',
                   'curve.0.size': '9', 'curve.0.facecolor': '#01020304',
                   'curve.0.edgecolor': '#aabbccdd', 'mappable.0.label': 'image',
                   'mappable.0.min': '12', 'mappable.0.max': '-3',
                   'mappable.0.interpolation': 'nearest', 'mappable.0.stage': 'rgba',
                   'mappable.1.label': 'collection', 'mappable.1.cmap': 'plasma',
                   'mappable.1.min': '8', 'mappable.1.max': '2'}
        self.axes.legend(handles=[self.line, self.collection], labels=['curve', 'scatter'],
                         ncols=2).set_draggable(True)
        for key, value in changes.items():
            self.bridge.setDraft(key, value)
        self.assertTrue(self.bridge.apply(), self.bridge.validationError)
        self.assertEqual(self.axes.get_title(), 'Edited')
        self.assertEqual(self.axes.get_xlim(), (5, 1))
        self.assertEqual(self.axes.get_xlabel(), 'X label')
        self.assertEqual(self.axes.get_ylabel(), 'Y label')
        self.assertEqual(self.line.get_label(), 'edited curve')
        self.assertEqual(self.line.get_linestyle(), '--')
        self.assertEqual(self.line.get_drawstyle(), 'steps-post')
        self.assertEqual(self.line.get_linewidth(), 2.5)
        self.assertIsNone(self.line.get_alpha())
        self.assertEqual(self.line.get_color(), to_rgba('#12345678'))
        self.assertEqual(self.line.get_marker(), 's')
        self.assertEqual(self.line.get_markersize(), 9)
        self.assertEqual(to_rgba(self.line.get_markerfacecolor()), to_rgba('#01020304'))
        self.assertEqual(to_rgba(self.line.get_markeredgecolor()), to_rgba('#aabbccdd'))
        self.assertIs(self.image.get_cmap(), self.custom)
        self.assertEqual(self.image.get_clim(), (-3, 12))
        self.assertEqual(self.image.get_interpolation_stage(), 'rgba')
        self.assertEqual(self.collection.get_cmap().name, 'plasma')
        self.assertEqual(self.collection.get_clim(), (2, 8))
        self.assertEqual(self.axes.legend_._ncols, 2)
        self.assertIsNotNone(self.axes.legend_._draggable)
        self.navigation.toolbar.push_current.assert_called_once()
        self.figure.canvas.draw.assert_called_once()
        self.assertTrue(self.bridge.apply())
        self.navigation.toolbar.push_current.assert_called_once()

    def test_invalid_drafts_never_partially_apply_other_valid_changes(self):
        for key, invalid in [('axis.x.min', 'nan'), ('axis.y.max', 'inf'),
                             ('curve.0.width', '-1'), ('curve.0.size', 'nan'),
                             ('curve.0.color', '#badhex'), ('curve.0.facecolor', 'bad'),
                             ('curve.0.edgecolor', 'bad'), ('mappable.0.min', 'nan'),
                             ('mappable.1.max', 'bad'), ('axis.x.scale', 'bogus'),
                             ('mappable.0.interpolation', 'bogus'),
                             ('mappable.0.stage', 'bogus'), ('mappable.0.cmap', 'bogus')]:
            with self.subTest(key=key):
                previous = self.bridge.drafts[key]
                self.bridge.setDraft('axes.title', 'must stay draft')
                self.assertFalse(self.bridge.setDraft(key, invalid))
                self.assertFalse(self.bridge.apply())
                self.assertEqual(self.axes.get_title(), '')
                self.assertIn(key, self.bridge.errors)
                self.bridge.setDraft(key, previous)
        self.figure.canvas.draw.assert_not_called()

    def test_log_domains_and_marker_none_native_semantics(self):
        self.bridge.setDraft('axis.x.scale', 'log')
        self.bridge.setDraft('axis.x.min', '0')
        self.assertFalse(self.bridge.apply())
        self.bridge.setDraft('axis.x.min', '1')
        self.bridge.setDraft('axis.x.max', '10')
        self.bridge.setDraft('curve.0.marker', 'none')
        self.bridge.setDraft('curve.0.size', '25')
        self.assertTrue(self.bridge.apply(), self.bridge.validationError)
        self.assertEqual(self.axes.get_xscale(), 'log')
        self.assertEqual(self.line.get_marker(), 'o')
        self.assertEqual(self.line.get_markersize(), 6)

    def test_date_converter_units_and_inverted_limits(self):
        self.bridge.shutdown()
        converter = DateConverter()
        self.axes.xaxis._set_converter(converter)
        self.axes.xaxis.set_units(timezone.utc)
        self.axes.set_xlim(datetime(2026, 1, 2, tzinfo=timezone.utc),
                           datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.bridge = FigureOptionsBridge(self.navigation, self.axes)
        self.assertTrue(self.bridge.axes[0]['isDate'])
        self.bridge.setDraft('axis.x.min', '2026-02-03T12:30:00+00:00')
        self.bridge.setDraft('axis.x.max', '2026-02-01T00:00:00+00:00')
        self.assertTrue(self.bridge.apply(), self.bridge.validationError)
        self.assertIs(self.axes.xaxis.get_converter(), converter)
        self.assertIs(self.axes.xaxis.get_units(), timezone.utc)
        self.assertEqual(self.axes.get_xlim(), tuple(date2num([
            datetime(2026, 2, 3, 12, 30, tzinfo=timezone.utc),
            datetime(2026, 2, 1, tzinfo=timezone.utc)])))

    def test_cancel_keeps_previous_apply_and_discards_pending(self):
        self.bridge.setDraft('axes.title', 'applied')
        self.assertTrue(self.bridge.apply())
        self.bridge.setDraft('axes.title', 'pending')
        self.bridge.shutdown()
        self.assertFalse(self.bridge.apply())
        self.assertEqual(self.axes.get_title(), 'applied')

    def test_naive_date_input_resolves_timezone_string_and_preserves_units(self):
        self.bridge.shutdown()
        converter = DateConverter()
        self.axes.xaxis._set_converter(converter)
        self.axes.xaxis.set_units('America/New_York')
        self.axes.set_xlim(datetime(2026, 1, 1), datetime(2026, 1, 4))
        self.bridge = FigureOptionsBridge(self.navigation, self.axes)
        self.bridge.setDraft('axis.x.min', '2026-01-02T12:30:00')
        self.bridge.setDraft('axis.x.max', '2026-01-03T12:30:00')
        self.assertTrue(self.bridge.apply(), self.bridge.validationError)
        self.assertIs(self.axes.xaxis.get_converter(), converter)
        self.assertEqual(self.axes.xaxis.get_units(), 'America/New_York')
        self.assertEqual(self.axes.get_xlim(), tuple(date2num([
            datetime(2026, 1, 2, 17, 30, tzinfo=timezone.utc),
            datetime(2026, 1, 3, 17, 30, tzinfo=timezone.utc)])))

    def test_current_custom_marker_and_registry_named_colormap_are_preserved(self):
        self.bridge.shutdown()
        marker = (5, 1, 23)
        self.line.set_marker(marker)
        custom = ListedColormap(['red', 'green'], name='viridis')
        self.image.set_cmap(custom)
        self.bridge = FigureOptionsBridge(self.navigation, self.axes)
        self.assertTrue(self.bridge.apply(), self.bridge.validationError)
        self.assertEqual(self.line.get_marker(), marker)
        self.assertIs(self.image.get_cmap(), custom)

    def test_all_axis_map_entries_include_three_dimensional_z(self):
        self.bridge.shutdown()
        figure = Figure()
        FigureCanvasAgg(figure)
        axes = figure.add_subplot(projection='3d')
        navigation = Navigation(figure)
        self.bridge = FigureOptionsBridge(navigation, axes)
        self.assertEqual([a['name'] for a in self.bridge.axes], ['x', 'y', 'z'])
        self.bridge.setDraft('axis.z.min', '8')
        self.bridge.setDraft('axis.z.max', '2')
        self.bridge.setDraft('axis.z.label', 'depth')
        self.assertTrue(self.bridge.apply(), self.bridge.validationError)
        self.assertEqual(axes.get_zlim(), (8, 2))
        self.assertEqual(axes.get_zlabel(), 'depth')

    def test_real_limit_and_clim_callbacks_receive_live_apply(self):
        limits, clim = [], []
        self.axes.callbacks.connect('xlim_changed', lambda ax: limits.append(ax.get_xlim()))
        self.collection.callbacks.connect('changed', lambda m: clim.append(m.get_clim()))
        self.bridge.setDraft('axis.x.min', '4')
        self.bridge.setDraft('axis.x.max', '9')
        self.bridge.setDraft('mappable.1.min', '3')
        self.bridge.setDraft('mappable.1.max', '8')
        self.assertTrue(self.bridge.apply())
        self.assertIn((4, 9), limits)
        self.assertIn((3, 8), clim)

    def test_owner_targets_and_observed_roundtrips_retire_permanently(self):
        for mutation in ('owner', 'inactive', 'figure', 'axes', 'line', 'image', 'collection'):
            with self.subTest(mutation=mutation):
                self.bridge.shutdown()
                self.setUp()
                retired = Mock()
                self.bridge.retired.connect(retired)
                if mutation == 'owner':
                    self.navigation.ownerToken = 'other'
                elif mutation == 'inactive':
                    self.navigation.enabled = False
                elif mutation == 'figure':
                    self.figure.canvas.figure = Figure()
                elif mutation == 'axes':
                    self.figure.delaxes(self.axes)
                else:
                    getattr(self, mutation).remove()
                self.navigation.stateChanged.emit()
                self.navigation.ownerToken = 'source|navigation:1'
                self.navigation.enabled = True
                self.assertFalse(self.bridge.setDraft('axes.title', 'stale'))
                self.assertFalse(self.bridge.apply())
                self.assertEqual(self.axes.get_title(), '')
                retired.assert_called_once()


if __name__ == '__main__':
    unittest.main()
