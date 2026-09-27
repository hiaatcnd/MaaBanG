import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from chart_timing import GestureEvent, GestureRecovery, compile_chart


class RecoveryTests(unittest.TestCase):
    def test_stalled_hold_is_suppressed_until_contact_reuse(self):
        recovery = GestureRecovery()
        recovery.begin(1.6, {0})
        events = [GestureEvent(t, 0, c, 2, a) for t, c, a in (
            (1.7, 0, 'move'), (1.8, 1, 'down'), (1.825, 1, 'up'),
            (2., 0, 'up'), (2., 0, 'down'), (2.025, 0, 'up'))]
        self.assertEqual([recovery.skip(e) for e in events],
                         [True, False, False, True, False, False])

    def test_expired_head_discards_future_flick_and_release(self):
        recovery = GestureRecovery()
        recovery.begin(1.6, set())
        events = [GestureEvent(t, 0, 0, 2, a) for t, a in (
            (1.61, 'down'), (1.65, 'move'), (1.67, 'up'), (1.8, 'down'))]
        self.assertEqual([recovery.skip(e) for e in events], [True, True, True, False])

    def test_uninterrupted_chart_passes_unchanged(self):
        recovery = GestureRecovery()
        events = compile_chart([{'type': 'BPM', 'beat': 0, 'bpm': 120},
                                {'type': 'Single', 'beat': 1, 'lane': 3}])
        self.assertFalse(any(recovery.skip(e) for e in events))

    def test_repeated_stalls_never_send_orphan_moves_or_old_heads(self):
        chart = [{'type': 'BPM', 'beat': 0, 'bpm': 120}]
        for beat in range(1, 50, 2):
            chart.extend([
                {'type': 'Slide', 'connections': [
                    {'beat': beat, 'lane': 1}, {'beat': beat+1, 'lane': 3}]},
                {'type': 'Single', 'beat': beat+.5, 'lane': 5, 'flick': True}])
        events = compile_chart(chart, seed=7, jitter_ms=35)
        for delay in (.2, .627, 1.5):
            recovery, active, sent = GestureRecovery(), set(), []
            stalls = iter((3., 8., 13.))
            next_stall = next(stalls)
            cutoff = float('-inf')
            for event in events:
                if event.time >= next_stall:
                    cutoff = event.time+delay+.030
                    recovery.begin(event.time+delay, active)
                    active.clear()
                    next_stall = next(stalls, float('inf'))
                if recovery.skip(event):
                    continue
                self.assertGreaterEqual(event.time, cutoff)
                if event.action == 'down':
                    self.assertNotIn(event.contact, active)
                    active.add(event.contact)
                else:
                    self.assertIn(event.contact, active)
                    if event.action == 'up':
                        active.remove(event.contact)
                sent.append(event)
            self.assertFalse(active)
            self.assertGreater(sent[-1].time, 24)


if __name__ == '__main__':
    unittest.main()
