"""Typed XY prompt drafts, normalization and finite decimal boundaries."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from spectra_app.common_prompts import CommonPromptBridge, prompt_float
from spectra_app.curve_prompts import CurveColorPromptBridge, prompt_color


APP = QApplication.instance() or QApplication([])


class CurvePromptBridgeTests(unittest.TestCase):
    def test_color_normalizes_names_hex_and_alpha_and_blocks_invalid_draft(self):
        bridge = CurveColorPromptBridge('red')
        self.assertEqual(bridge.normalizedColor, '#ff0000')
        self.assertTrue(bridge.canAccept)
        for draft, expected in (('#abc', '#aabbcc'), ('#80123456', '#123456'), ('navy', '#000080')):
            bridge.setDraft(draft)
            self.assertEqual(bridge.input_value(), expected)
            self.assertEqual(bridge.validationError, '')
        bridge.setDraft('not a color')
        self.assertFalse(bridge.canAccept)
        self.assertTrue(bridge.validationError)
        self.assertEqual(bridge.normalizedColor, '#000080')
        accepted = QSignalSpy(bridge.acceptRequested)
        bridge.accept()
        self.assertEqual(accepted.count(), 0)
        bridge.setDraft('blue')
        bridge.accept()
        bridge.accept()
        bridge.cancel()
        self.assertEqual(accepted.count(), 1)

    def test_rgb_hsv_visual_edits_recover_invalid_draft_and_shutdown_freezes(self):
        bridge = CurveColorPromptBridge('invalid')
        self.assertFalse(bridge.canAccept)
        bridge.setHsv(1 / 3, 1., 1.)
        self.assertEqual(bridge.draft, '#00ff00')
        self.assertEqual((bridge.red, bridge.green, bridge.blue), (0, 255, 0))
        bridge.setHsv(float('nan'), 1., 1.)
        self.assertEqual(bridge.draft, '#00ff00')
        bridge.setRgb(12, 34, 56)
        self.assertEqual(bridge.input_value(), '#0c2238')
        bridge.shutdown()
        bridge.setRgb(255, 0, 0)
        bridge.setDraft('red')
        self.assertEqual(bridge.input_value(), '#0c2238')
        self.assertFalse(bridge.canAccept)

    def test_float_finite_inclusive_range_and_decimal_result(self):
        bridge = CommonPromptBridge('float', value=2., minimum=.000001, maximum=50., decimals=6)
        for draft in ('', '-', 'nan', 'inf', '-inf', '1e999', '0', '50.000001', 'no'):
            bridge.setDraft(draft)
            self.assertFalse(bridge.canAccept, draft)
            bridge.chooseAction('ok')
            self.assertTrue(bridge.validationError)
        for draft, expected in (('0.000001', .000001), ('50', 50.), ('1.23456789', 1.234568)):
            bridge.setDraft(draft)
            self.assertTrue(bridge.canAccept)
            self.assertEqual(bridge.input_value(), expected)
            self.assertIsInstance(bridge.input_value(), float)

    def test_float_matches_legacy_six_decimal_initial_and_bound_rounding(self):
        for value, expected in ((1e-8, 0.), (5e-7, 0.), (5.1e-7, .000001),
                                (1.2345665, 1.234566), (75.123456789, 75.123457)):
            bridge = CommonPromptBridge('float', value=value, minimum=min(.000001, value),
                                        maximum=max(50., value), decimals=6)
            self.assertEqual(bridge.input_value(), expected)
            self.assertTrue(bridge.canAccept)
        with self.assertRaises(ValueError):
            CommonPromptBridge('float', value=float('nan'))

    def test_public_cancel_preserves_original_and_accept_normalizes(self):
        with patch('spectra_app.curve_prompts._exec_prompt', return_value=False):
            self.assertEqual(prompt_color(None, 'Color', 'navy'), ('navy', False))
        with patch('spectra_app.curve_prompts._exec_prompt', return_value=True):
            self.assertEqual(prompt_color(None, 'Color', 'navy'), ('#000080', True))
        with patch('spectra_app.common_prompts._exec_prompt', return_value=False):
            self.assertEqual(prompt_float(None, 'Width', 'Width', value=1.23456789), (1.23456789, False))


if __name__ == '__main__':
    unittest.main()
