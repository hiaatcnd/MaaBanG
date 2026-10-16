"""Recovery must reuse only completed, fresh evidence from the current stall."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from chart_sync import ChartPhaseTracker, PhaseReacquirer
from chart_worker import prime_phase_recovery


def tracker_at(beat=4., lanes=(0, 1, 3, 6)):
    # Four distinct simultaneous notes avoid a periodic-pattern ambiguity.
    chart = [{'type': 'BPM', 'beat': 0, 'bpm': 60}]
    chart.extend({'type': 'Single', 'beat': beat, 'lane': lane} for lane in lanes)
    return ChartPhaseTracker(chart)


def positions_at(tracker, stamp, phase=0.):
    positions = []
    for lane, notes in tracker.notes.items():
        ys = 590 * np.exp(np.clip((stamp - notes - tracker.phase_reference - phase)
                                 / tracker.travel_scale, -100, 100))
        ys = ys[(ys >= 60) & (ys <= 554)]
        positions.append((lane, float(max(ys)) if len(ys) else None))
    return positions


def trace_row(tracker, stamp, capture_ms=20., phase=0.):
    return {'time': stamp, 'capture_ms': capture_ms,
            'positions': positions_at(tracker, stamp, phase), 'recovering': False}


class RecoveryEvidenceWindowTests(unittest.TestCase):
    def test_completed_in_stall_frames_can_confirm_without_new_wait(self):
        tracker = tracker_at()
        probe = PhaseReacquirer(tracker, 3.95, 0., .3, evidence_since=3.65)
        trace = [trace_row(tracker, stamp) for stamp in (3.72, 3.76, 3.80, 3.84, 3.88)]
        self.assertEqual(prime_phase_recovery(probe, trace), len(trace))
        self.assertIsNotNone(probe.result)
        self.assertAlmostEqual(probe.result['correction'], 0., places=8)
        self.assertGreaterEqual(probe.result['notes'], 4)
        self.assertGreaterEqual(probe.result['frames'], 4)

    def test_timeout_stays_relative_to_request_after_a_long_stall(self):
        tracker = tracker_at(4.2)
        probe = PhaseReacquirer(tracker, 3.8, 0., 1.8, evidence_since=2.)
        result = None
        for stamp in (3.94, 3.98, 4.02, 4.06):
            result = probe.observe_positions(stamp, positions_at(tracker, stamp))
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['correction'], 0., places=8)
        expired = PhaseReacquirer(tracker_at(5.4), 3.8, 0., 1.8, evidence_since=2.)
        for stamp in (5.10, 5.14, 5.18, 5.22):
            self.assertIsNone(expired.observe_positions(stamp, positions_at(expired.tracker, stamp)))
        self.assertEqual(expired.rows, [])

    def test_default_boundary_still_rejects_pre_request_history(self):
        tracker = tracker_at()
        probe = PhaseReacquirer(tracker, 3.95, 0., .3)
        for stamp in (3.72, 3.76, 3.80, 3.84, 3.88):
            self.assertIsNone(probe.observe_positions(stamp, positions_at(tracker, stamp)))
        self.assertEqual(probe.rows, [])
        self.assertIsNone(probe.result)

    def test_unsafe_capture_intervals_are_excluded_before_the_probe(self):
        class Probe:
            started = 3.95
            evidence_since = 3.65

            def __init__(self):
                self.calls = []

            def observe_positions(self, stamp, positions, *, evaluate=True, received_at=None):
                self.calls.append((stamp, positions, evaluate, received_at))

        tracker = tracker_at()
        bad = [trace_row(tracker, 3.50),             # wholly before this stall
               trace_row(tracker, 3.655, 20.),     # capture began before boundary
               trace_row(tracker, 3.948, 20.),     # capture not complete at request
               trace_row(tracker, 3.80, 40.),      # too slow for timing inference
               trace_row(tracker, 3.81, -1.),      # invalid duration
               trace_row(tracker, 3.82, float('nan')),
               {'time': 3.83, 'capture_ms': 20., 'positions': None}]
        good = [trace_row(tracker, 3.86, 0.), trace_row(tracker, 3.90, 20.)]
        probe = Probe()
        self.assertEqual(prime_phase_recovery(probe, bad + good), len(good))
        self.assertEqual({call[0] for call in probe.calls}, {3.86, 3.90})
        self.assertTrue(any(call[2] for call in probe.calls))
        self.assertTrue(all(call[0] == 3.90 for call in probe.calls if call[2]))
        self.assertTrue(all(call[3] == probe.started for call in probe.calls))

    def test_delayed_live_frame_cannot_confirm_until_a_current_frame_arrives(self):
        tracker = tracker_at()
        probe = PhaseReacquirer(tracker, 3.80, 0., .3, evidence_since=3.5)
        for stamp in (3.72, 3.73, 3.74, 3.75):
            # A short capture can still be delayed by OCR or a held phase lock.
            self.assertIsNone(probe.observe_positions(stamp, positions_at(tracker, stamp),
                                                      received_at=3.90))
        self.assertGreaterEqual(len(probe.rows), 6)
        self.assertIsNone(probe.result)
        result = probe.observe_positions(3.92, positions_at(tracker, 3.92), received_at=3.94)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['correction'], 0., places=8)

    def test_frame_delivered_after_recovery_timeout_cannot_confirm(self):
        tracker = tracker_at()
        probe = PhaseReacquirer(tracker, 3.80, 0., .3, evidence_since=3.5)
        for stamp in (3.72, 3.76, 3.80):
            probe.observe_positions(stamp, positions_at(tracker, stamp), evaluate=False)
        rows = list(probe.rows)
        self.assertIsNone(probe.observe_positions(3.84, positions_at(tracker, 3.84),
                                                  received_at=5.1))
        self.assertEqual(probe.rows, rows)
        self.assertIsNone(probe.result)

    def test_invalid_delivery_timestamp_does_not_poison_later_frames(self):
        tracker = tracker_at()
        for stamp, received in ((float('nan'), 3.8), (3.8, float('nan')),
                                (3.8, float('inf')), (3.8, 3.7)):
            with self.subTest(stamp=stamp, received=received):
                probe = PhaseReacquirer(tracker, 3.80, 0., .3, evidence_since=3.5)
                self.assertIsNone(probe.observe_positions(stamp, positions_at(tracker, 3.8),
                                                          received_at=received))
                self.assertEqual(probe.rows, [])
                self.assertEqual(probe.last_stamp, float('-inf'))

    def test_stale_consensus_cannot_immediately_resume_input(self):
        tracker = tracker_at()
        probe = PhaseReacquirer(tracker, 4.10, 0., .45, evidence_since=3.65)
        trace = [trace_row(tracker, stamp) for stamp in (3.72, 3.76, 3.80, 3.84, 3.88)]
        self.assertEqual(prime_phase_recovery(probe, trace), len(trace))
        self.assertGreaterEqual(len(probe.rows), 6)
        self.assertIsNone(probe.result)

    def test_fresh_empty_frame_cannot_make_an_old_consensus_look_current(self):
        for empty in ([], [(lane, None) for lane in range(7)], [(0, 80.)]):
            with self.subTest(positions=empty):
                tracker = tracker_at()
                probe = PhaseReacquirer(tracker, 3.95, 0., .3, evidence_since=3.65)
                trace = [trace_row(tracker, stamp) for stamp in (3.72, 3.76, 3.80, 3.84)]
                trace.append({'time': 3.91, 'capture_ms': 20., 'positions': empty})
                prime_phase_recovery(probe, trace)
                self.assertGreaterEqual(len(probe.rows), 6)
                self.assertIsNone(probe.result)

    def test_batch_does_not_latch_early_consensus_before_later_conflicting_frames(self):
        tracker = tracker_at()
        probe = PhaseReacquirer(tracker, 4.08, 0., .45, evidence_since=3.65)
        trace = [trace_row(tracker, stamp) for stamp in (3.72, 3.76, 3.80, 3.84)]
        # The game changes phase later during the same host stall. Early rows
        # alone agree, but the complete window has incompatible evidence.
        trace += [trace_row(tracker, stamp, phase=.10) for stamp in (3.92, 3.96, 4.00, 4.04)]
        prime_phase_recovery(probe, trace)
        self.assertIsNone(probe.result)

    def test_too_few_frames_or_notes_never_become_sufficient_by_replay(self):
        for lanes, stamps in (((0, 1, 3, 6), (3.84, 3.88)),
                              ((0,), (3.72, 3.76, 3.80, 3.84, 3.88)),
                              ((0, 1, 3, 6), (3.88,) * 8)):
            with self.subTest(lanes=lanes, stamps=stamps):
                tracker = tracker_at(lanes=lanes)
                probe = PhaseReacquirer(tracker, 3.95, 0., .3, evidence_since=3.65)
                trace = [trace_row(tracker, stamp) for stamp in stamps]
                prime_phase_recovery(probe, trace)
                self.assertIsNone(probe.result)

    def test_explicit_evidence_boundary_must_be_finite_and_no_later_than_request(self):
        for boundary in (float('nan'), float('inf'), float('-inf'), 4.):
            with self.subTest(boundary=boundary):
                with self.assertRaises(ValueError):
                    PhaseReacquirer(tracker_at(), 3.95, 0., .3, evidence_since=boundary)


if __name__ == '__main__':
    unittest.main()
