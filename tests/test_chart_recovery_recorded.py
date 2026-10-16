"""Replay the recorded input stall without connecting to an emulator."""
import json
from collections import Counter
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from chart_sync import ChartPhaseTracker, PhaseReacquirer
from chart_timing import GestureRecovery, compile_chart
from chart_worker import prime_phase_recovery


class RecordedInputStallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = Path(__file__).parent / 'fixtures/chart_sync/round53_stall.json'
        cls.data = json.loads(fixture.read_text(encoding='utf8'))

    def test_recorded_stall_reuses_completed_frames_and_waits_for_in_flight_frame(self):
        data = self.data
        old = data['recovery']
        requested = old['resync']['requested_at']
        correction = data['blocked_event']['phase_ms']/1000
        boundary = old['event']['time']+correction
        probe = PhaseReacquirer(ChartPhaseTracker(data['chart']), requested,
                                correction, old['late_ms']/1000,
                                evidence_since=boundary)
        self.assertEqual(prime_phase_recovery(probe, data['phase_trace']), 4)
        self.assertIsNone(probe.result)  # The decisive capture is still in flight.
        fresh = next(row for row in data['phase_trace'] if row['capture_end'] > requested)
        self.assertGreater(fresh['capture_end'], requested)
        result = probe.observe_positions(fresh['time'], fresh['positions'],
                                         received_at=fresh['capture_end'])
        self.assertIsNotNone(result)
        expected = data['expected']
        self.assertAlmostEqual(result['correction'], expected['during_stall_correction'])
        self.assertEqual(result['notes'], 4)
        self.assertEqual(result['frames'], 5)
        self.assertLess(fresh['capture_end']-requested, .010)

        # Resume only future whole gestures, never the interrupted slide or backlog.
        recovery = GestureRecovery()
        recovery.begin(fresh['capture_end']-result['correction'], old['released_contacts'])
        sent, skipped, active = [], Counter(), set()
        for event in compile_chart(data['chart'], **data['compile_options']):
            if event.time < old['event']['time']:
                continue
            if recovery.skip(event):
                skipped[event.action] += 1
                continue
            if event.action == 'down':
                self.assertNotIn(event.contact, active)
                active.add(event.contact)
            else:
                self.assertIn(event.contact, active)
                if event.action == 'up':
                    active.remove(event.contact)
            sent.append(event)
        self.assertFalse(active)
        self.assertAlmostEqual(sent[0].time, expected['anticipated_first_resumed_event'])
        self.assertEqual(dict(skipped), expected['anticipated_skipped_events'])
        self.assertGreater(old['resumed_at']-sent[0].time, .6)

    def test_default_fresh_only_recovery_reproduces_the_recorded_extra_wait(self):
        data = self.data
        old = data['recovery']
        probe = PhaseReacquirer(ChartPhaseTracker(data['chart']),
                                old['resync']['requested_at'],
                                data['blocked_event']['phase_ms']/1000,
                                old['late_ms']/1000)
        result = None
        for row in data['phase_trace']:
            result = probe.observe_positions(row['time'], row['positions'],
                                             received_at=row['capture_end'])
            if result:
                break
        self.assertIsNotNone(result)
        self.assertAlmostEqual(row['time'], data['expected']['fresh_only_first_fit_at'])
        self.assertAlmostEqual(result['correction'], data['expected']['fresh_only_correction'])


if __name__ == '__main__':
    unittest.main()
