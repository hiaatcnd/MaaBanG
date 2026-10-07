import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from chart_policy import ChartOptions, ChartSelection
from chart_timing import compile_chart


BPM = {'type': 'BPM', 'beat': 0, 'bpm': 120}


class AvoidFullComboTests(unittest.TestCase):
    def assert_balanced(self, events):
        active = set()
        for event in events:
            if event.action == 'down':
                self.assertNotIn(event.contact, active)
                active.add(event.contact)
            else:
                self.assertIn(event.contact, active)
                if event.action == 'up':
                    active.remove(event.contact)
        self.assertFalse(active)

    def test_last_tap_or_flick_omits_entire_gesture(self):
        for tail in ({'type': 'Single', 'beat': 5, 'lane': 6},
                     {'type': 'Single', 'beat': 5, 'lane': 6, 'flick': True},
                     {'type': 'Directional', 'beat': 5, 'lane': 6,
                      'direction': 'Left', 'width': 2}):
            with self.subTest(tail=tail):
                chart = [BPM, tail, {'type': 'Single', 'beat': 1, 'lane': 0}]
                original = compile_chart(chart)
                self.assertEqual(original, compile_chart(chart, avoid_full_combo=False))
                events = compile_chart(chart, avoid_full_combo=True)
                self.assertEqual(events, [e for e in original if e.time < 1])
                self.assert_balanced(events)

    def test_final_hold_or_slide_including_flick_tail_is_preserved(self):
        for kind in ('Long', 'Slide'):
            for flick in (False, True):
                with self.subTest(kind=kind, flick=flick):
                    chart = [BPM, {'type': kind, 'connections': [
                        {'beat': 1, 'lane': 1},
                        {'beat': 12, 'lane': 4 if kind == 'Slide' else 1, 'flick': flick}]},
                        {'type': 'Single', 'beat': 9, 'lane': 6}]
                    original = compile_chart(chart, seed=42, jitter_ms=180, position_jitter=22)
                    events = compile_chart(chart, seed=42, jitter_ms=180, position_jitter=22,
                                           avoid_full_combo=True)
                    self.assertEqual(events, [e for e in original if e.lane != 6])
                    self.assertEqual(events[0].action, 'down')
                    self.assertEqual(events[-1].action, 'up')
                    self.assert_balanced(events)

    def test_holds_only_chart_is_rejected_before_playback(self):
        for kind in ('Long', 'Slide'):
            chart = [BPM, {'type': kind, 'connections': [
                {'beat': 1, 'lane': 1}, {'beat': 12, 'lane': 1, 'flick': True}]}]
            self.assertTrue(compile_chart(chart))
            with self.assertRaisesRegex(ValueError, '没有可跳过的非长条音符'):
                compile_chart(chart, avoid_full_combo=True)

    def test_last_tap_before_final_hold_head_is_skipped_not_the_hold(self):
        chart = [BPM, {'type': 'Single', 'beat': 1, 'lane': 0},
                 {'type': 'Single', 'beat': 8, 'lane': 6},
                 {'type': 'Long', 'connections': [
                     {'beat': 9, 'lane': 1}, {'beat': 12, 'lane': 1}]}]
        original = compile_chart(chart)
        events = compile_chart(chart, avoid_full_combo=True)
        self.assertEqual(events, [e for e in original if e.lane != 6])
        self.assert_balanced(events)

    def test_final_chord_omits_only_one_and_preserves_other_contacts(self):
        chart = [BPM, {'type': 'Long', 'connections': [
            {'beat': 1, 'lane': 1}, {'beat': 8, 'lane': 1}]},
            {'type': 'Single', 'beat': 8, 'lane': 6},
            {'type': 'Single', 'beat': 8, 'lane': 5}]
        original = compile_chart(chart)
        events = compile_chart(chart, avoid_full_combo=True)
        self.assertEqual(events, [e for e in original if e.lane != 6])
        self.assert_balanced(events)

    def test_chart_judgment_wins_over_jitter_and_flick_release_order(self):
        chart = [BPM, {'type': 'Single', 'beat': 2, 'lane': 0, 'flick': True},
                 {'type': 'Single', 'beat': 2.02, 'lane': 6}]
        for seed in range(30):
            original = compile_chart(chart, seed=seed, jitter_ms=180, position_jitter=22)
            events = compile_chart(chart, seed=seed, jitter_ms=180, position_jitter=22,
                                   avoid_full_combo=True)
            self.assertEqual(events, [e for e in original if e.lane == 0])
            self.assert_balanced(events)

    def test_only_gesture_can_be_omitted_but_empty_or_invalid_chart_still_rejected(self):
        chart = [BPM, {'type': 'Single', 'beat': 1, 'lane': 0}]
        self.assertEqual(compile_chart(chart, avoid_full_combo=True), [])
        for invalid in ([BPM], [BPM, {'type': 'Single', 'beat': 1, 'lane': 7}]):
            with self.assertRaises(ValueError):
                compile_chart(invalid, avoid_full_combo=True)

    def test_option_defaults_and_strict_boolean(self):
        self.assertFalse(ChartOptions.parse({}).avoid_full_combo)
        for mode in ('free', 'tour_free', 'tour_fixed', 'team', 'coop', 'challenge'):
            self.assertTrue(ChartOptions.parse({'mode': mode, 'avoid_full_combo': True}).avoid_full_combo)
        for value in ('false', 'true', 0, 1, None):
            with self.assertRaises(ValueError):
                ChartOptions.parse({'avoid_full_combo': value})
            with self.assertRaises(ValueError):
                compile_chart([BPM], avoid_full_combo=value)

    def test_ui_switch_and_pipeline_defaults(self):
        interface = json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        task = next(t for t in interface['task'] if t['entry'] == 'ChartLive')
        self.assertIn('谱面避免FullCombo', task['option'])
        option = interface['option']['谱面避免FullCombo']
        self.assertEqual(option['default_case'], '关')
        self.assertEqual([c['pipeline_override']['CL_avoid_full_combo']['attach']['value']
                          for c in option['cases']], [False, True])
        pipeline = json.loads((ROOT/'assets/resource/pipeline/chart_live.json').read_text(encoding='utf8'))
        self.assertIs(pipeline['CL_avoid_full_combo']['attach']['value'], False)

    def test_playback_receives_option_and_original_duration(self):
        from chart_live import ChartLiveFlow
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as folder:
                context = SimpleNamespace(tasker=SimpleNamespace(
                    controller=SimpleNamespace(info={}), stopping=False))
                flow = ChartLiveFlow(context, ChartOptions.parse({'avoid_full_combo': enabled}), folder)
                flow.wait_ready = Mock()
                flow.disable_mv = Mock()
                flow.verify_chart_start = Mock(return_value=None)
                flow.save_frame = Mock()
                metadata = {'duration': 120}
                def worker(args, **kwargs):
                    path = Path(args[-1])
                    config = json.loads(path.read_text(encoding='utf8'))
                    self.assertIs(config['avoid_full_combo'], enabled)
                    self.assertEqual(config['chart']['duration'], 120)
                    (path.parent/'armed').write_text('ready')
                    (path.parent/'playback.json').write_text('{"status":"input_complete"}')
                    return SimpleNamespace(poll=lambda: 0, returncode=0)
                with patch('chart_live.subprocess.Popen', side_effect=worker):
                    flow.play_chart(1, ChartSelection('306', 'expert'), metadata, 0, {})


if __name__ == '__main__':
    unittest.main()
