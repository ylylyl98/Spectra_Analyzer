import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel

from spectra_app.fitting import fit_multi_peak
from tests.background_helpers import wait_jobs
from tests.settings_helpers import isolate_settings
from tests.test_workflow import _window

APP = QApplication.instance() or QApplication([])


class FitWidthLabelTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_settings(self).parent
        x = np.linspace(0, 20, 101)
        y = 0.7 + 4 * np.exp(-0.5 * ((x - 10) / 1.0) ** 2)
        self.w = _window(np.vstack([y, y]))
        self.errors = []
        self.w._show_error = self.errors.append
        self.w.plot_tabs.setCurrentIndex(0)
        self.w._sidebar_tabs.setCurrentWidget(self.w._analysis_sidebar_scroll)
        self.w._analysis_tabs.setCurrentIndex(1)
        self.w.show()
        APP.processEvents()

    def tearDown(self):
        wait_jobs(self.w)
        self.w.close()
        self.w.deleteLater()
        APP.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def assert_explanation(self, model, parameter, factor):
        box = next(box for box in self.w.findChildren(QGroupBox) if box.title() == 'Fit Parameters')
        explanations = [label for label in box.findChildren(QLabel) if 'FWHM' in label.text()]
        self.assertTrue(explanations, 'Fit Parameters needs an always-visible width explanation')
        label = explanations[0]
        self.assertTrue(label.isVisibleTo(self.w))
        for meaning in (model, parameter, factor):
            self.assertIn(meaning, label.text())
        for spin in (self.w.lw_min_spin, self.w.lw_max_spin):
            self.assertIn(parameter, spin.toolTip())
            self.assertIn('points', spin.toolTip())

    def test_model_switch_refreshes_visible_meaning_without_changing_bounds(self):
        w = self.w
        w.lw_min_spin.setValue(2)
        w.lw_max_spin.setValue(8)
        self.assert_explanation('Lorentzian', 'gamma', '2')
        w.fit_model_combo.setCurrentText('Gaussian')
        self.assert_explanation('Gaussian', 'sigma', '2.35482')
        self.assertEqual((w.lw_min_spin.value(), w.lw_max_spin.value()), (2, 8))
        w.fit_model_combo.setCurrentText('Lorentzian')
        self.assert_explanation('Lorentzian', 'gamma', '2')
        self.assertEqual((w.lw_min_spin.value(), w.lw_max_spin.value()), (2, 8))
        w.lw_max_spin.setValue(0)
        self.assertIn('auto', w.lw_max_value_label.text().lower())
        w.fit_model_combo.setCurrentText('Gaussian')
        self.assertIn('auto', w.lw_max_value_label.text().lower())
        self.assertEqual((w.lw_min_spin.value(), w.lw_max_spin.value()), (2, 0))

    def test_inline_fit_conversions_do_not_overlap_editors_at_default_sidebar_width(self):
        w = self.w
        w.resize(1320, 850)
        w.resizeDocks([w.dat_details_dock], [390], Qt.Horizontal)
        QTest.qWait(30)
        self.assertGreaterEqual(w._sidebar_tabs.width(), 350)
        self.assertLessEqual(w._sidebar_tabs.width(), 400)
        w.lw_min_spin.setValue(2)
        for model in ('Lorentzian', 'Gaussian'):
            w.fit_model_combo.setCurrentText(model)
            for maximum in (8, 0):
                w.lw_max_spin.setValue(maximum)
                QTest.qWait(30)
                for spin, label in (
                    (w.lw_min_spin, w.lw_min_value_label),
                    (w.lw_max_spin, w.lw_max_value_label),
                    (w.peak_move_limit_spin, w.peak_move_value_label),
                ):
                    with self.subTest(model=model, maximum=maximum, conversion=label.text()):
                        row = spin.parentWidget()
                        self.assertIs(row, label.parentWidget())
                        self.assertTrue(spin.isVisibleTo(w))
                        self.assertTrue(label.isVisibleTo(w))
                        self.assertFalse(spin.geometry().intersects(label.geometry()),
                                         (spin.geometry(), label.geometry(), row.rect()))
                        self.assertTrue(row.rect().contains(spin.geometry()))
                        self.assertTrue(row.rect().contains(label.geometry()))

    def test_dataset_and_project_restore_refresh_meaning_and_keep_saved_values(self):
        w = self.w
        for name, model, minimum, maximum in (
            ('gaussian', 'Gaussian', 4, 8), ('lorentzian', 'Lorentzian', 6, 0)
        ):
            path = self.folder / f'{name}.dat'
            path.write_text('Energy(eV) 0 1\n' + '\n'.join(
                f'{x:.12g} {x*x:.12g} {x:.12g}' for x in np.linspace(1, 4, 31)), encoding='utf-8')
            w._load_dat_file_direct(path)
            w.fit_model_combo.setCurrentText(model)
            w.lw_min_spin.setValue(minimum)
            w.lw_max_spin.setValue(maximum)
            w.analysis_text.setPlainText('Stored old summary: wid=0.5')
        gaussian, lorentzian = list(w.workspace.datasets.values())
        for session, model, parameter, factor, bounds in (
            (gaussian, 'Gaussian', 'sigma', '2.35482', (4, 8)),
            (lorentzian, 'Lorentzian', 'gamma', '2', (6, 0)),
            (gaussian, 'Gaussian', 'sigma', '2.35482', (4, 8))
        ):
            w.activate_dataset(session.dataset_id)
            self.assert_explanation(model, parameter, factor)
            self.assertEqual((w.lw_min_spin.value(), w.lw_max_spin.value()), bounds)
            self.assertEqual(w.analysis_text.toPlainText(), 'Stored old summary: wid=0.5')
        project = w.save_project(self.folder / 'widths.spectra')
        w.fit_model_combo.setCurrentText('Lorentzian')
        self.assertTrue(w.open_project(project))
        self.assert_explanation('Gaussian', 'sigma', '2.35482')
        self.assertEqual((w.lw_min_spin.value(), w.lw_max_spin.value()), (4, 8))
        self.assertEqual(w.analysis_text.toPlainText(), 'Stored old summary: wid=0.5')
        self.assertTrue(w.new_project())
        self.assert_explanation('Lorentzian', 'gamma', '2')
        self.assertEqual((w.lw_min_spin.value(), w.lw_max_spin.value()), (0, 0))
        self.assertEqual(self.errors, [])

    def test_current_and_batch_fit_keep_raw_bounds_and_report_raw_sigma(self):
        w = self.w
        w.fit_model_combo.setCurrentText('Gaussian')
        w.lw_min_spin.setValue(2)
        w.lw_max_spin.setValue(8)
        w.peak_indices = np.array([50])
        w._peak_gate_idx = 0
        w.batch_peak_map = {0: np.array([50]), 1: np.array([50])}
        with patch('spectra_app.main_window.fit_multi_peak', wraps=fit_multi_peak) as solver:
            w._on_fit()
            wait_jobs(w)
            self.assertIsNotNone(w.fit_result)
            self.assertIn('sigma=', w.analysis_text.toPlainText())
            self.assertNotIn('wid=', w.analysis_text.toPlainText())
            self.assertAlmostEqual(float(w.fit_result.widths[0]), 1.0, places=5)
            w._on_batch_fit_all_gates()
            wait_jobs(w)
        self.assertEqual(solver.call_count, 3)
        for call in solver.call_args_list:
            self.assertEqual(call.kwargs['model'], 'Gaussian')
            # Existing point conversion uses the 20-unit span / 101 points.
            self.assertAlmostEqual(call.kwargs['linewidth_min'], 40 / 101)
            self.assertAlmostEqual(call.kwargs['linewidth_max'], 160 / 101)
        self.assertEqual(len(w.batch_fit_result_map), 2)
        for row in w._batch_fit_export_rows:
            self.assertAlmostEqual(float(row['peak_1_width']), 1.0, places=5)
        self.assertEqual(self.errors, [])
