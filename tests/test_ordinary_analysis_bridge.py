"""Owner, numeric grammar and atomic scientific edit boundaries (no native GUI)."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import asdict, fields
import unittest
from types import SimpleNamespace
import numpy as np
from PySide6.QtCore import QObject, QLocale
from PySide6.QtWidgets import QApplication
from spectra_app.dataset_state import AnalysisControls, DatasetSession, Workspace
from spectra_app.ordinary_analysis_bridge import OrdinaryAnalysisBridge, FIELD_BY_NAME

APP = QApplication.instance() or QApplication([])


class OwnerWindow(QObject):
    def __init__(self):
        super().__init__()
        self.workspace = Workspace()
        self._unregistered_session = DatasetSession()
        self.current_cube = None
        self._data_generation = 0
        self._close_pending = self._dat_import_pending = False
        self._lasso_mode = self._circle_delete_mode = False
        self._spec_ax = object()
        self.invalidations = self.renders = self.modified = 0
        self.messages = []
        self._dat_details_requested = False
        self.task_index = 0
        self.task_requests = []

    def _workbench_document(self): return None
    def _current_trace_peak_indices(self): return np.asarray([], dtype=int)
    def _invalidate_analysis_jobs(self): self.invalidations += 1
    def _schedule_project_modified(self): self.modified += 1
    def _refresh_fit_range_summary(self): pass
    def _update_fit_param_unit_labels(self): pass
    def _refresh_analysis_controls(self): pass
    def _update_spectrum_only(self): self.renders += 1
    def publish_status(self, text, timeout=0):
        self.messages.append((str(text), timeout))

    def _set_dat_details_requested(self, visible):
        self._dat_details_requested = bool(visible)

    def request_task(self, index):
        if index not in (0, 1, 2):
            return False
        self.task_index = index
        self.task_requests.append(index)
        return True


class OrdinaryBridgeTests(unittest.TestCase):
    def setUp(self):
        # QObject controllers read the application default, not a widget locale.
        self.addCleanup(QLocale.setDefault, QLocale())
        QLocale.setDefault(QLocale('en_US'))
        self.window = OwnerWindow()
        self.bridge = OrdinaryAnalysisBridge(self.window)

    def test_all_29_fields_are_explicit_and_have_guidance(self):
        self.assertEqual(set(FIELD_BY_NAME), {f.name for f in fields(AnalysisControls)})
        self.assertEqual(len(FIELD_BY_NAME), 29)
        self.assertTrue(all(item['help'] for item in self.bridge.fields))
        self.assertEqual(FIELD_BY_NAME['d2_polyorder']['maximum'], 6)
        self.assertEqual(FIELD_BY_NAME['prominence']['step'], .005)
        self.assertEqual(FIELD_BY_NAME['d2_prominence']['step'], .005)

    def test_raw_draft_validation_is_atomic(self):
        b = self.bridge; token = b.ownerToken
        before = asdict(b.owner().analysis_controls)
        self.assertTrue(b.setDraft('distance', '19', token))
        self.assertTrue(b.setDraft('prominence', 'bad', token))
        self.assertTrue(b.has_project_drafts())
        self.assertEqual(asdict(b.owner().analysis_controls), before)
        self.assertFalse(b.commit(token))
        self.assertEqual(asdict(b.owner().analysis_controls), before)
        self.assertEqual(b.errorField, 'prominence')
        self.assertIn('numeric editor', b.errorText)
        self.assertEqual(b.state['texts']['distance'], '19')
        self.assertEqual(self.window.invalidations, 0)
        self.assertTrue(self.window._dat_details_requested)
        self.assertEqual(self.window.task_requests, [1])
        self.assertEqual(self.window.task_index, 1)
        self.assertEqual(self.window.messages, [(b.errorText, 0)])

    def test_locale_and_native_precision_constraints(self):
        b = self.bridge
        QLocale.setDefault(QLocale('de_DE'))
        self.assertEqual(b.parse('prominence', '0,1250'), .125)
        self.assertEqual(b.parse('maxfev', '20.000'), 20000)
        self.assertEqual(b.parse('fit_xmin', '-2,125000'), -2.125)
        for field, text in [('prominence', '0,12345'), ('distance', '1,2'),
                            ('maxfev', '99'), ('d2_polyorder', '7'),
                            ('fit_xmin', 'nan'), ('fit_xmax', '1000000000001')]:
            with self.subTest(field=field, text=text):
                with self.assertRaises(ValueError): b.parse(field, text)

    def test_settings_before_load_and_semantic_invalid_values_preserved(self):
        b = self.bridge; token = b.ownerToken
        self.assertTrue(b.enabled)
        self.assertTrue(b.setValue('linewidth_min', 50, token))
        self.assertTrue(b.setValue('linewidth_max', 10, token))
        self.assertTrue(b.setValue('d2_window', 12, token))
        self.assertEqual((b.owner().analysis_controls.linewidth_min,
                          b.owner().analysis_controls.linewidth_max,
                          b.owner().analysis_controls.d2_window), (50, 10, 12))
        self.assertEqual(self.window.renders, 0)

    def test_step_combines_pending_candidate_in_one_install_and_rounds(self):
        b = self.bridge; token = b.ownerToken
        b.setDraft('fit_xmin', '-0.125000', token)
        self.assertTrue(b.step('prominence', 1, token))
        self.assertEqual(b.owner().analysis_controls.fit_xmin, -.125)
        self.assertEqual(b.owner().analysis_controls.prominence, .055)
        self.assertEqual(self.window.invalidations, 1)
        self.assertEqual(self.window.renders, 1)
        for _ in range(9): b.step('prominence', 1, token)
        self.assertEqual(b.owner().analysis_controls.prominence, .1)
        self.assertEqual(self.window.renders, 1)

    def test_choice_merges_pending_range_once(self):
        b = self.bridge; token = b.ownerToken
        b.setDraft('fit_xmax', '22.000000', token)
        self.assertTrue(b.setValue('fit_model', 'Gaussian', token))
        self.assertEqual(b.owner().analysis_controls.fit_xmax, 22)
        self.assertEqual(b.owner().analysis_controls.fit_model, 'Gaussian')
        self.assertEqual((self.window.invalidations, self.window.renders), (1, 1))
        # Enter followed by blur consumes the same draft once.
        self.assertTrue(b.commit(token))
        self.assertEqual((self.window.invalidations, self.window.renders), (1, 1))

    def test_owner_excludes_gate_but_rejects_generation_and_stale_candidate(self):
        b = self.bridge; token = b.ownerToken
        self.window._unregistered_session.view.gate = 99
        self.assertEqual(b.ownerToken, token)
        b.setDraft('distance', '8', token)
        candidate = b.validate_editor()
        self.window._data_generation += 1
        self.assertFalse(b.setDraft('distance', '9', token))
        self.assertFalse(b.commit(token))
        with self.assertRaises(ValueError): b.commit_candidate(candidate)
        self.assertEqual(b.owner().analysis_controls.distance, 5)

    def test_import_close_and_inactive_removal_preserve_current_draft(self):
        b = self.bridge; token = b.ownerToken
        b.setDraft('distance', '8', token)
        b.retire_owner('unrelated-id')
        self.assertEqual(b.state['texts']['distance'], '8')
        self.window._dat_import_pending = True
        self.assertFalse(b.commit(token))
        self.window._dat_import_pending = False
        b.close()
        self.assertFalse(b.setValue('fit_model', 'Voigt', token))
        self.assertFalse(b.enabled)

    def test_same_owner_projection_preserves_raw_text_and_canonical_summary(self):
        b = self.bridge; token = b.ownerToken
        b.setDraft('prominence', '0.123', token)
        b.owner().summary = '<literal> & plain'
        b.refresh(readinessText='Ready')
        self.assertEqual(b.state['texts']['prominence'], '0.123')
        self.assertEqual(b.state['summary'], '<literal> & plain')
        self.assertEqual(b.state['ui']['readinessText'], 'Ready')
        self.assertEqual(b.ownerToken, token)

    def test_programmatic_endpoint_projection_matches_native_precision_and_clamp(self):
        from PySide6.QtWidgets import QDoubleSpinBox
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(-1e12, 1e12)
        for locale in ('en_US', 'de_DE'):
            QLocale.setDefault(QLocale(locale))
            spin.setLocale(QLocale())
            for value in (1.2345675, -1.2345675, .0000005, -.0000005,
                          1000000000001., -1000000000001., 2.123456789):
                with self.subTest(locale=locale, value=value):
                    spin.setValue(value)
                    self.assertEqual(self.bridge.normalized_number('fit_xmin', value), spin.value())

    def test_unchanged_range_commands_do_not_retire_analysis_jobs(self):
        from types import MethodType
        from spectra_app.main_window import ApplicationController
        w = self.window; b = self.bridge
        w.current_cube = SimpleNamespace(energy=np.asarray([0., 1.]))
        w.ordinary_analysis = b
        w._analysis_controls = lambda: b.owner().analysis_controls
        bounds = dict(xmin=0., xmax=1.)
        w._dat_number = bounds.__getitem__
        w._ordered_pair = ApplicationController._ordered_pair
        w._capture_dataset_controls = lambda: None
        w._refresh_fit_readiness = lambda: None
        w._set_fit_range_to_full = MethodType(ApplicationController._set_fit_range_to_full, w)
        ApplicationController._on_set_fit_full_range(w)
        ApplicationController._on_use_current_x_range(w)
        self.assertEqual(w.invalidations, 0)
        bounds['xmax'] = 1.2345675
        ApplicationController._on_use_current_x_range(w)
        self.assertEqual(w.invalidations, 1)
        self.assertEqual(b.owner().analysis_controls.fit_xmax, 1.234568)

    def test_auxiliary_document_rejects_an_ordinary_gesture(self):
        b = self.bridge; token = b.ownerToken
        self.window.graph_documents = object()
        self.window._auxiliary_active = lambda: True
        self.assertFalse(b.enabled)
        self.assertFalse(b.setDraft('distance', '9', token))
        self.assertFalse(b.setValue('fit_model', 'Voigt', token))
        self.assertFalse(b.trigger('find', token))
        self.assertEqual(b.owner().analysis_controls.distance, 5)
        self.assertEqual(self.window.invalidations, 0)

    def test_validation_candidate_cannot_consume_later_draft(self):
        b = self.bridge; token = b.ownerToken
        b.setDraft('distance', '8', token)
        candidate = b.validate_editor()
        b.setDraft('distance', '9', token)
        with self.assertRaises(ValueError): b.commit_candidate(candidate)
        self.assertEqual(b.owner().analysis_controls.distance, 5)


if __name__ == '__main__': unittest.main()
