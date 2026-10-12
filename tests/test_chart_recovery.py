import json
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'agent'))
from chart_sync import ChartPhaseTracker, PhaseReacquirer
from chart_timing import GestureRecovery, compile_chart


def visible_notes(tracker, timestamp, phase):
    """Simulated frames at the measured note speed, not recorded gameplay."""
    positions = []
    for lane, notes in tracker.notes.items():
        ys = 590*np.exp(np.clip((timestamp-notes-tracker.phase_reference-phase)/.245, -100, 100))
        ys = ys[(ys >= 60) & (ys <= 554)]
        positions.append((lane, float(max(ys)) if len(ys) else None))
    return positions


class PhaseRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((Path(__file__).parent/'fixtures/chart_sync/matsuri_stalls.json')
                                 .read_text(encoding='utf8'))

    def test_recorded_stall_locations_reacquire_game_that_advanced_or_paused(self):
        for row in self.fixture['stalls']:
            late = row['late_ms']/1000
            prior = row['phase_before_ms']/1000
            for delay in (0., late, late+.060, -.025):
                with self.subTest(run=row['run'], delay=delay):
                    tracker = ChartPhaseTracker(self.fixture['chart'])
                    started = row['event']['time']+late+prior
                    probe = PhaseReacquirer(tracker, started, prior, late)
                    result = None
                    for k, stamp in enumerate(np.arange(started, started+probe.timeout, .04)):
                        positions = visible_notes(tracker, stamp, prior+delay+(k%3-1)*.003)
                        result = probe.observe_positions(stamp, positions)
                        if result:
                            break
                    self.assertIsNotNone(result)
                    self.assertAlmostEqual(result['correction'], prior+delay, delta=.004)
                    self.assertGreaterEqual(result['notes'], 4)
                    self.assertGreaterEqual(result['frames'], 4)
                    self.assertLess(stamp-started, .8)

                    # Use the new song origin to seek future heads. Never replay
                    # the missed backlog or resume the middle of a held gesture.
                    recovery = GestureRecovery()
                    active = {row['event']['contact']}
                    recovery.begin(stamp-result['correction'], active)
                    active.clear()
                    sent = []
                    for event in compile_chart(self.fixture['chart']):
                        if event.time < row['event']['time'] or recovery.skip(event):
                            continue
                        self.assertGreaterEqual(event.time, recovery.cutoff)
                        if event.action == 'down':
                            self.assertNotIn(event.contact, active)
                            active.add(event.contact)
                        else:
                            self.assertIn(event.contact, active)
                            if event.action == 'up':
                                active.remove(event.contact)
                        sent.append(event)
                    self.assertTrue(sent)
                    self.assertFalse(active)

    def test_pre_stall_phase_history_cannot_confirm_recovery(self):
        tracker = ChartPhaseTracker(self.fixture['chart'])
        tracker.samples = [(3+i*.01, i%2, i, .005) for i in range(12)]
        tracker.sparse_samples = list(tracker.samples)
        probe = PhaseReacquirer(tracker, 3.5, .005, .18)
        for stamp in np.arange(3.5, 5., .04):
            self.assertIsNone(probe.observe_positions(stamp, []))
        self.assertIsNone(probe.result)

    def test_out_of_range_drift_is_not_used_to_move_song_time(self):
        for row in self.fixture['stalls']:
            tracker = ChartPhaseTracker(self.fixture['chart'])
            start = row['event']['time']+row['late_ms']/1000
            probe = PhaseReacquirer(tracker, start, 0., .18)
            for stamp in np.arange(start, start+probe.timeout, .04):
                self.assertIsNone(probe.observe_positions(stamp, visible_notes(tracker, stamp, .8)))

    def test_single_note_or_single_lane_cannot_confirm_recovery(self):
        for single_note in (False, True):
            chart = [{'type':'BPM', 'beat':0, 'bpm':60}]+[
                {'type':'Single', 'beat':1+i*.13, 'lane':3}
                for i in range(1 if single_note else 12)]
            tracker = ChartPhaseTracker(chart)
            probe = PhaseReacquirer(tracker, .7, 0., .18)
            for stamp in np.arange(.7, 1.9, .04):
                self.assertIsNone(probe.observe_positions(stamp, visible_notes(tracker, stamp, .18)))

    def test_repeated_chart_pattern_with_two_plausible_phases_is_rejected(self):
        chart = [{'type':'BPM', 'beat':0, 'bpm':60}]+[
            {'type':'Single', 'beat':1+i*.1, 'lane':i%2} for i in range(40)]
        tracker = ChartPhaseTracker(chart)
        probe = PhaseReacquirer(tracker, 2., 0., .25)
        for stamp in np.arange(2., 3.25, .04):
            self.assertIsNone(probe.observe_positions(stamp, visible_notes(tracker, stamp, .05)))

    def test_repeated_capture_does_not_manufacture_distinct_frames(self):
        tracker = ChartPhaseTracker(self.fixture['chart'])
        probe = PhaseReacquirer(tracker, 3.6, 0., .18)
        positions = visible_notes(tracker, 3.6, 0.)
        for _ in range(30):
            self.assertIsNone(probe.observe_positions(3.6, positions*3))
        self.assertLessEqual(len(probe.rows), 7)

    def test_pre_request_and_expired_frames_are_not_accepted(self):
        tracker = ChartPhaseTracker(self.fixture['chart'])
        probe = PhaseReacquirer(tracker, 3.5, 0., .18)
        for stamp in (3.4, 5.):
            self.assertIsNone(probe.observe_positions(stamp, visible_notes(tracker, stamp, .18)))
        self.assertEqual(probe.rows, [])

    def test_invalid_recovery_request_is_rejected(self):
        tracker = ChartPhaseTracker(self.fixture['chart'])
        for delay in (0., -1., 2.1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                PhaseReacquirer(tracker, 3., 0., delay)


if __name__ == '__main__':
    unittest.main()
