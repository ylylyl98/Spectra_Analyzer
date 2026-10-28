"""Real figure writes, raw draft validation, and captured subplot ownership."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from matplotlib.figure import Figure
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from spectra_app.subplot_editor import SubplotEditorBridge


APP = QApplication.instance() or QApplication([])


class Navigation(QObject):
    def __init__(self, figure):
        super().__init__()
        self.ownerToken = 'dataset|graph|navigation:1'
        self.enabled = True
        self.toolbar = SimpleNamespace(canvas=figure.canvas)

    def allowed(self, owner=None):
        return self.enabled and (owner is None or owner == self.ownerToken)


class SubplotEditorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.figure = Figure()
        self.axes = self.figure.subplots(2, 2)
        self.figure.canvas.draw_idle = Mock()
        self.navigation = Navigation(self.figure)
        self.bridge = SubplotEditorBridge(self.navigation)

    def test_live_numeric_commits_and_raw_invalid_drafts(self):
        bounds = self.axes[0, 0].get_position().bounds
        self.assertTrue(self.bridge.setDraft('left', '0.200'))
        self.assertEqual(self.figure.subplotpars.left, .125)
        self.figure.canvas.draw_idle.assert_not_called()
        self.assertTrue(self.bridge.commitDrafts())
        self.assertEqual(self.figure.subplotpars.left, .2)
        self.assertNotEqual(self.axes[0, 0].get_position().bounds, bounds)
        self.figure.canvas.draw_idle.assert_called_once()
        self.assertTrue(self.bridge.commitDrafts())
        self.figure.canvas.draw_idle.assert_called_once()
        for text in ('', '-', 'nan', 'inf', '-0.1', '1.1', 'abc'):
            with self.subTest(text=text):
                self.assertFalse(self.bridge.setDraft('left', text))
                self.assertFalse(self.bridge.commitDrafts())
                self.assertEqual(self.bridge.drafts['left'], text)
                self.assertTrue(self.bridge.errors['left'])
                self.assertEqual(self.figure.subplotpars.left, .2)
        self.assertTrue(self.bridge.setDraft('left', '0.205'))
        self.assertFalse(self.bridge.validationError)
        self.assertTrue(self.bridge.setDraft('left', '.2'))
        self.assertTrue(self.bridge.commitDrafts())
        self.assertEqual(self.bridge.drafts['left'], '0.200')
        self.assertFalse(self.bridge.setDraft('left', '-'))
        self.assertFalse(self.bridge.commitDrafts())
        self.assertEqual(self.bridge.drafts['left'], '-')

    def test_correlated_constraints_and_steps(self):
        self.assertFalse(self.bridge.setDraft('bottom', '0.880'))
        self.assertEqual(self.figure.subplotpars.bottom, .11)
        self.assertIn('bottom', self.bridge.errors)
        self.assertTrue(self.bridge.setDraft('bottom', '0.879'))
        self.assertTrue(self.bridge.commitDrafts())
        self.assertFalse(self.bridge.step('bottom', 1))
        self.assertEqual(self.figure.subplotpars.bottom, .879)
        self.assertTrue(self.bridge.reset())
        self.assertTrue(self.bridge.step('left', 1))
        self.assertEqual(self.bridge.drafts['left'], '0.130')
        self.assertAlmostEqual(self.figure.subplotpars.left, .13)
        self.assertTrue(self.bridge.step('left', -1))
        self.assertEqual(self.bridge.drafts['left'], '0.125')
        self.assertFalse(self.bridge.setDraft('right', '0.125'))
        self.assertTrue(self.bridge.setDraft('right', '0.126'))
        self.assertTrue(self.bridge.commitDrafts())
        self.assertEqual(self.figure.subplotpars.right, .126)

    def test_each_field_rejects_nonfinite_and_out_of_range_drafts(self):
        for key in self.bridge.KEYS:
            for text in ('nan', 'inf', '-0.001', '1.001'):
                with self.subTest(key=key, text=text):
                    before = getattr(self.figure.subplotpars, key)
                    self.assertFalse(self.bridge.setDraft(key, text))
                    self.assertFalse(self.bridge.commitDrafts())
                    self.assertEqual(self.bridge.drafts[key], text)
                    self.assertEqual(getattr(self.figure.subplotpars, key), before)
                    self.bridge.reset()

    def test_reset_tight_layout_and_export_actual_values(self):
        defaults = {key: getattr(self.figure.subplotpars, key) for key in self.bridge.KEYS}
        self.bridge.setDraft('left', '.200')
        self.bridge.commitDrafts()
        self.assertEqual(self.figure.subplotpars.left, .2)
        self.bridge.reset()
        self.assertEqual({key: getattr(self.figure.subplotpars, key) for key in defaults}, defaults)
        self.axes[0, 0].set_ylabel('Long axis label')
        self.assertTrue(self.bridge.tightLayout())
        for key in defaults:
            self.assertEqual(self.bridge.drafts[key], f'{getattr(self.figure.subplotpars, key):.3f}')
        self.assertTrue(self.bridge.exportValues())
        self.assertEqual(self.bridge.exportText, ',\n'.join(
            f'{key}={getattr(self.figure.subplotpars, key):.3}' for key in self.bridge.KEYS))
        self.bridge.shutdown()
        fresh = SubplotEditorBridge(self.navigation)
        opening_left = self.figure.subplotpars.left
        fresh.setDraft('left', '.250')
        fresh.commitDrafts()
        self.assertEqual(self.figure.subplotpars.left, .25)
        fresh.reset()
        self.assertEqual(self.figure.subplotpars.left, opening_left)

    def test_ordinary_add_axes_notice_preserves_physical_geometry(self):
        figure = Figure()
        axes = figure.add_axes([.2, .3, .6, .5])
        bridge = SubplotEditorBridge(Navigation(figure))
        original = axes.get_position().bounds
        self.assertIn('Properties', bridge.notice)
        bridge.setDraft('left', '.300')
        bridge.commitDrafts()
        self.assertEqual(axes.get_position().bounds, original)

    def test_unchanged_focus_commit_preserves_tight_layout_full_precision(self):
        self.axes[0, 0].set_ylabel('Long axis label')
        self.assertTrue(self.bridge.tightLayout())
        actual = {key: getattr(self.figure.subplotpars, key) for key in self.bridge.KEYS}
        self.assertTrue(any(value != float(f'{value:.3f}') for value in actual.values()))
        self.figure.canvas.draw_idle.reset_mock()
        self.assertTrue(self.bridge.commitDrafts())
        self.assertEqual({key: getattr(self.figure.subplotpars, key) for key in actual}, actual)
        self.figure.canvas.draw_idle.assert_not_called()
        self.assertTrue(self.bridge.setDraft('left', self.bridge.drafts['left']))
        self.assertTrue(self.bridge.commitDrafts())
        self.assertEqual({key: getattr(self.figure.subplotpars, key) for key in actual}, actual)
        self.figure.canvas.draw_idle.assert_not_called()

    def test_stale_owner_activity_figure_axes_and_shutdown_cannot_write(self):
        for mutation in ('owner', 'inactive', 'figure', 'axes', 'shutdown'):
            with self.subTest(mutation=mutation):
                self.setUp()
                retired = Mock()
                self.bridge.retired.connect(retired)
                if mutation == 'owner':
                    self.navigation.ownerToken = 'other|navigation:2'
                elif mutation == 'inactive':
                    self.navigation.enabled = False
                elif mutation == 'figure':
                    self.navigation.toolbar.canvas.figure = Figure()
                elif mutation == 'axes':
                    self.figure.delaxes(self.axes[0, 0])
                else:
                    self.bridge.shutdown()
                before = self.figure.subplotpars.left
                self.assertFalse(self.bridge.setDraft('left', '.250'))
                self.assertFalse(self.bridge.commitDrafts())
                self.assertFalse(self.bridge.reset())
                self.assertFalse(self.bridge.tightLayout())
                self.assertFalse(self.bridge.exportValues())
                self.assertEqual(self.figure.subplotpars.left, before)
                self.navigation.ownerToken = 'dataset|graph|navigation:1'
                self.navigation.enabled = True
                self.assertFalse(self.bridge.setDraft('left', '.300'))
                self.assertEqual(retired.call_count, 0 if mutation == 'shutdown' else 1)


if __name__ == '__main__':
    unittest.main()
