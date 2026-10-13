"""Reusable candidate pools retain scientific coordinates and filter reversibly."""
import importlib
import json
import unittest
from dataclasses import replace

import numpy as np


class PeakCandidateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('spectra_app.peak_candidates'),
                             'The reusable candidate engine is missing')
        self.engine = importlib.import_module('spectra_app.peak_candidates')

    def test_relaxing_filter_recovers_weak_candidates_from_unchanged_pool(self):
        x = np.linspace(-8, 8, 1601)
        y = np.exp(-.5*((x+3)/.3)**2) + .04*np.exp(-.5*((x-3)/.3)**2)
        pool = self.engine.collect_peak_candidates(x, y, mode='Peaks')
        before = [candidate.to_dict() for candidate in pool]
        strict = self.engine.filter_peak_candidates(pool, self.engine.candidate_filter_preset('Strict'))
        loose = self.engine.filter_peak_candidates(pool, self.engine.candidate_filter_preset('Loose'))
        self.assertEqual(len(strict), 1)
        self.assertEqual(len(loose), 2)
        self.assertEqual([candidate.to_dict() for candidate in pool], before)

    def test_distance_preserves_nearby_opposite_kinds_and_cap_is_optional(self):
        pool = self.engine.collect_peak_candidates(np.arange(9.), [0, 5, 0, -4, 0, 3, 0, 0, 0])
        settings = self.engine.CandidateFilter(preset='Custom', min_prominence_fraction=0,
            min_noise_strength=0, min_distance=5)
        kept = self.engine.filter_peak_candidates(pool, settings)
        self.assertEqual([(c.index, c.kind) for c in kept], [(1, 'peak'), (3, 'dip')])
        capped = self.engine.filter_peak_candidates(pool, replace(settings, max_count=1))
        self.assertEqual(len(capped), 1)
        self.assertEqual(len(pool), 3)

    def test_width_uses_physical_coordinates_and_source_amplitude(self):
        x = np.linspace(-3, 3, 1201)
        y = 7 + 2*np.exp(-.5*(x/.4)**2)
        candidate, = self.engine.collect_peak_candidates(x, y, mode='Peaks')
        self.assertAlmostEqual(candidate.width, 2.354820045*.4, places=4)
        self.assertAlmostEqual(candidate.amplitude, 9)
        self.assertAlmostEqual(candidate.position, 0)
        self.assertGreater(candidate.noise_strength, 1)
        doubled, = self.engine.collect_peak_candidates(x*2, y, mode='Peaks')
        self.assertAlmostEqual(doubled.width, candidate.width*2)

    def test_finite_gaps_do_not_create_edge_peaks_or_mutate_input(self):
        x = np.arange(13., dtype=float)
        y = np.array([0, 2, 0, np.nan, 5, 4, 3, np.nan, 0, -3, 0, 1, 2.])
        old_x, old_y = x.copy(), y.copy()
        pool = self.engine.collect_peak_candidates(x, y)
        self.assertEqual([(c.index, c.kind) for c in pool], [(1, 'peak'), (9, 'dip')])
        np.testing.assert_equal(x, old_x)
        np.testing.assert_equal(y, old_y)
        # The fraction uses the full valid source spread, not each short run.
        self.assertAlmostEqual(pool[0].prominence_fraction, .25)

    def test_d2_on_irregular_descending_x_reports_source_positions_and_amplitudes(self):
        ascending = np.linspace(-5, 5, 801)
        x = (ascending + .03*np.sin(ascending*2))[::-1]
        y = 3 + 4*np.exp(-.5*((x-.7)/.4)**2)
        old_y = y.copy()
        pool = self.engine.collect_peak_candidates(x, y, mode='Peaks', detector='D2')
        strongest = max(pool, key=lambda c: c.prominence)
        self.assertLess(abs(strongest.position-.7), .03)
        self.assertEqual(strongest.position, x[strongest.index])
        self.assertEqual(strongest.amplitude, y[strongest.index])
        stretched = self.engine.collect_peak_candidates(x*2, y, mode='Peaks', detector='D2')
        strongest_stretched = max(stretched, key=lambda c: c.prominence)
        self.assertAlmostEqual(strongest_stretched.prominence, strongest.prominence/4, places=7)
        self.assertAlmostEqual(strongest_stretched.width, strongest.width*2, places=7)
        np.testing.assert_equal(y, old_y)

    def test_candidate_and_custom_filter_roundtrip_reapply_identically(self):
        pool = self.engine.collect_peak_candidates(np.arange(7.), [0, 1, 0, 2, 0, -1, 0])
        serialized = json.loads(json.dumps([c.to_dict() for c in pool], allow_nan=False))
        restored = [self.engine.PeakCandidate.from_dict(record) for record in serialized]
        settings = self.engine.CandidateFilter(preset='Custom', min_prominence_fraction=.4,
            min_noise_strength=0, min_width=.5, max_width=4, max_count=2)
        restored_settings = self.engine.CandidateFilter.from_dict(json.loads(json.dumps(settings.to_dict())))
        self.assertEqual(self.engine.filter_peak_candidates(pool, settings),
                         self.engine.filter_peak_candidates(restored, restored_settings))
        with self.assertRaises(ValueError):
            self.engine.PeakCandidate.from_dict({**serialized[0], 'kind': 'unknown'})
        with self.assertRaises(ValueError):
            self.engine.CandidateFilter.from_dict({'min_width': -1})
        with self.assertRaises(ValueError):
            self.engine.CandidateFilter.from_dict({'max_count': 2.0})
        with self.assertRaises(ValueError):
            self.engine.PeakCandidate.from_dict({**serialized[0], 'index': 1.0})

    def test_invalid_grids_fail_explicitly_and_short_or_constant_runs_are_empty(self):
        for x in ([0., 1., 1., 2.], [0., 2., 1., 3.]):
            with self.assertRaisesRegex(ValueError, 'monotonic'):
                self.engine.collect_peak_candidates(x, [0., 2., 1., 0.], detector='D2')
        self.assertEqual(self.engine.collect_peak_candidates([0, 1], [0, 1]), [])
        self.assertEqual(self.engine.collect_peak_candidates(np.arange(12), np.ones(12)), [])

    def test_d2_constant_runs_do_not_turn_roundoff_into_candidates(self):
        x = np.linspace(1, 4, 31)
        y = np.full(31, 7.)
        self.assertEqual(self.engine.collect_peak_candidates(x, y, detector='D2'), [])

    def test_numpy_constructed_candidates_are_json_serializable(self):
        candidate = self.engine.PeakCandidate(index=np.int64(1), position=np.float64(2),
            amplitude=np.float64(3), kind='peak', prominence=np.float64(1),
            prominence_fraction=np.float64(.5), width=np.float64(.1),
            noise_strength=np.float64(3), detector='Original')
        settings = self.engine.CandidateFilter(max_count=np.int64(2))
        self.assertEqual(json.loads(json.dumps(candidate.to_dict()))['index'], 1)
        self.assertEqual(json.loads(json.dumps(settings.to_dict()))['max_count'], 2)

    def test_legacy_both_detector_keeps_nearby_opposite_types(self):
        from spectra_app.fitting import find_peak_indices
        indices, kinds = find_peak_indices(np.arange(9.), np.array([0, 5, 0, -4, 0, 3, 0, 0, 0]),
            mode='Both', prominence_frac=0, min_distance=5, max_peaks=6, max_dips=6)
        self.assertEqual(indices.tolist(), [1, 3])
        self.assertEqual(kinds.tolist(), [True, False])
