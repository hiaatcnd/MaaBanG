"""Ready-page screenshots and worker boundaries for the additional FC option."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'agent'), str(ROOT/'tools')]
from chart_live import ChartLiveFlow
from chart_policy import ChartOptions, ChartSelection
from chart_timing import compile_chart
from costume_unlock import FlowError
from direct_chart_live import DirectChartLiveFlow
from full_combo import ONLY_UNFC, ready_star_row, ready_full_combo_state
from mining_live import ChallengeMiningFlow
from mining_policy import MiningOptions
from online_live import OnlineLiveFlow


def frame(name='coop/ready.png'):
    return np.array(Image.open(ROOT/'tests/fixtures'/name).convert('RGB'))[:, :, ::-1].copy()


class FullComboHistoryTests(unittest.TestCase):
    def flow(self, folder, mode='free', kind=ChartLiveFlow):
        context = SimpleNamespace(tasker=SimpleNamespace(controller=SimpleNamespace(info={}), stopping=False))
        options = (MiningOptions.parse({'avoid_full_combo': ONLY_UNFC}) if kind is ChallengeMiningFlow
                   else ChartOptions.parse({'mode': mode, 'avoid_full_combo': ONLY_UNFC}))
        flow = kind(context, options, folder)
        flow.image = frame()
        flow.snap = Mock()
        flow.pause = Mock()
        flow.text = Mock(return_value='SAVIOR OF SONG')
        flow.wait_ready = Mock()
        flow.disable_mv = Mock()
        flow.verify_chart_start = Mock(return_value=None)
        flow.save_frame = Mock()
        if kind is DirectChartLiveFlow:
            flow._start_mode = 'online_click'
        if mode in ('coop', 'team', 'versus'):
            flow.report['attempts'] = 1
            flow.submit_online_ready = Mock()
        return flow

    def test_additional_choice_preserves_defaults_and_rejects_unresolved_worker_config(self):
        for mode in ('free', 'tour_free', 'tour_fixed', 'coop', 'team', 'versus', 'challenge'):
            self.assertEqual(ChartOptions.parse({'mode': mode, 'avoid_full_combo': ONLY_UNFC}).avoid_full_combo, ONLY_UNFC)
        self.assertEqual(MiningOptions.parse({'avoid_full_combo': ONLY_UNFC}).avoid_full_combo, ONLY_UNFC)
        for parser in (ChartOptions, MiningOptions):
            self.assertIs(parser.parse({}).avoid_full_combo, False)
            for invalid in (0, 1, None, 'true', 'false', 'on', 'unknown', []):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    parser.parse({'avoid_full_combo': invalid})
        with self.assertRaises(ValueError):
            compile_chart([], avoid_full_combo=ONLY_UNFC)

    def test_real_rainbow_red_and_gray_stars_by_difficulty(self):
        image = frame()
        self.assertEqual(ready_star_row(image)[1], ('full_combo', 'unplayed', 'full_combo', 'full_combo', 'unknown'))
        self.assertEqual(ready_full_combo_state(image, 'easy'), 'full_combo')  # Rainbow AP.
        self.assertEqual(ready_full_combo_state(image, 'normal'), 'unplayed')
        self.assertEqual(ready_full_combo_state(image, 'expert'), 'full_combo')  # Pink/red FC.
        self.assertEqual(ready_full_combo_state(image, 'special'), 'unknown')

    def test_real_other_ready_layouts(self):
        for name, first, expert in (
            ('versus/ready.png', 207, 'unplayed'),
            ('fire_preview/team_6_to_3.png', 207, 'full_combo'),
            ('cp/ready.png', 317, 'unplayed'),
            ('lowres/challenge_ready.png', 317, 'unplayed')):
            with self.subTest(name=name):
                self.assertEqual(ready_star_row(frame(name))[0], first)
                self.assertEqual(ready_full_combo_state(frame(name), 'expert'), expert)

    def test_gold_is_not_fc_and_special_has_its_own_star(self):
        image = frame()
        image[587:596, 296:303] = (60, 210, 255)  # Synthetic gold interior.
        self.assertEqual(ready_full_combo_state(image, 'expert'), 'clear')
        # Synthetic fifth star uses a real FC star; EXPERT stays gray.
        image = frame()
        image[575:607, 314:347] = image[575:607, 283:316].copy()
        image[575:607, 283:316] = image[575:607, 222:255].copy()
        self.assertEqual(ready_full_combo_state(image, 'special'), 'full_combo')
        self.assertEqual(ready_full_combo_state(image, 'expert'), 'unplayed')

    def test_covered_partial_blank_and_ambiguous_rows_cannot_exempt(self):
        image = frame()
        broken = image.copy()
        broken[575:607, 222:255] = 255
        for invalid in (None, image[:600], np.zeros_like(image), np.full_like(image, 255),
                        (image.astype(float)*.65).astype(np.uint8), broken):
            self.assertEqual(ready_full_combo_state(invalid, 'expert'), 'unknown')
        # The two real layouts overlap. Inject both detections to exercise
        # ambiguity rejection without constructing an impossible screenshot.
        with patch('full_combo._ready_star', return_value='full_combo'):
            self.assertIsNone(ready_star_row(image))

    def test_real_ocr_and_stars_together_match_the_current_song(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from song_catalog import RECOGNITION_BY_ID
        image = frame()
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'fc-history-fixture'
            def screencap(self): return image
            def forbidden(self, *args): raise AssertionError('Screenshot replay cannot send inputs')
            click = swipe = touch_down = touch_move = touch_up = forbidden
        resource, controller, tasker = Resource(), Controller(), Tasker()
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        self.assertTrue(controller.post_connection().wait().succeeded)
        self.assertTrue(tasker.bind(resource, controller))
        song = next(s for s in RECOGNITION_BY_ID.values() if s['title'] == 'クリスマスのうた')
        owner, errors = self, []
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self, context, argv):
                    try:
                        flow = ChartLiveFlow(context, ChartOptions.parse({'avoid_full_combo': ONLY_UNFC}), folder)
                        flow.pause = Mock()
                        for difficulty, expected in (('easy', 'full_combo'), ('normal', 'unplayed'), ('expert', 'full_combo')):
                            owner.assertEqual(flow.read_full_combo_state(ChartSelection(song['id'], difficulty)), expected)
                        owner.assertEqual(flow.read_full_combo_state(ChartSelection('306', 'expert')), 'unknown')
                        return True
                    except Exception as exc:
                        errors.append(repr(exc))
                        return False
            resource.register_custom_action('FcHistoryCheck', Check())
            result = tasker.post_task('FcHistoryCheck', {'FcHistoryCheck': {
                'action': 'Custom', 'custom_action': 'FcHistoryCheck'}}).wait()
            self.assertTrue(result.succeeded, errors)

    def test_reads_are_stable_bound_to_song_and_not_cached_between_difficulties(self):
        with tempfile.TemporaryDirectory() as folder:
            flow = self.flow(folder)
            for difficulty, expected in (('easy', False), ('normal', True), ('expert', False), ('special', True)):
                row = {}
                self.assertIs(flow.resolve_avoid_full_combo(ChartSelection('306', difficulty), row), expected)
                self.assertEqual(row['full_combo_check']['difficulty'], difficulty)
            self.assertEqual(flow.snap.call_count, 8)
            flow.text.return_value = 'Another song'
            self.assertTrue(flow.resolve_avoid_full_combo(ChartSelection('306', 'expert'), {}))

    def test_transition_and_overlay_require_two_new_consistent_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            flow = self.flow(folder)
            selection = ChartSelection('306', 'expert')
            images = iter([frame(), np.zeros_like(frame()), frame('versus/ready.png'), frame('versus/ready.png')])
            flow.snap.side_effect = lambda: setattr(flow, 'image', next(images))
            self.assertTrue(flow.resolve_avoid_full_combo(selection, {}))
            images = iter([frame(), frame('versus/ready.png'), frame(), frame('versus/ready.png')])
            self.assertEqual(flow.read_full_combo_state(selection), 'unknown')

    def test_ui_generation_keeps_old_choices_and_appends_one_new_choice(self):
        from update_chart_interface import update as chart
        from update_mining_interface import update as mining
        original = json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        generated = mining(chart(copy.deepcopy(original)))
        for name, prefix in (('谱面避免FullCombo', 'CL'), ('直接演出避免FullCombo', 'DL'),
                             ('挖矿舞台避免FullCombo', 'MN')):
            option = original['option'][name]
            self.assertEqual(option, generated['option'][name])
            self.assertEqual(option['default_case'], '关')
            self.assertEqual([c['name'] for c in option['cases']], ['关', '开', '仅避免未 FC 歌曲 FC'])
            self.assertEqual([c['pipeline_override'][prefix+'_avoid_full_combo']['attach']['value']
                              for c in option['cases']], [False, True, ONLY_UNFC])

    def test_each_playback_mode_resolves_to_boolean_before_worker_is_created(self):
        variants = [(ChartLiveFlow, mode) for mode in ('free', 'tour_free', 'tour_fixed', 'challenge')]
        variants += [(OnlineLiveFlow, mode) for mode in ('team', 'coop', 'versus')]
        variants += [(DirectChartLiveFlow, 'free'), (ChallengeMiningFlow, 'free')]
        for kind, mode in variants:
            for state in ('full_combo', 'clear', 'unplayed', 'unknown'):
                with self.subTest(mode=mode, kind=kind.__name__, state=state), tempfile.TemporaryDirectory() as folder:
                    flow = self.flow(folder, mode, kind)
                    flow.read_full_combo_state = Mock(return_value=state)
                    configs = []
                    def worker(args, **kwargs):
                        path = Path(args[-1])
                        config = json.loads(path.read_text(encoding='utf8'))
                        configs.append(config)
                        self.assertIs(config['avoid_full_combo'], state != 'full_combo')
                        self.assertEqual(config['chart']['duration'], 120)
                        (path.parent/'armed').touch()
                        (path.parent/'playback.json').write_text('{"status":"input_complete"}')
                        return SimpleNamespace(poll=lambda: 0, returncode=0)
                    with patch('chart_live.subprocess.Popen', side_effect=worker):
                        flow.play_chart(1, ChartSelection('306', 'expert'), {'duration': 120}, 0, {})
                    self.assertEqual(len(configs), 1)
                    self.assertEqual(flow.read_full_combo_state.call_count, 2 if state == 'full_combo' else 1)

    def test_fc_confirmation_lost_after_worker_prepare_never_authorizes_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            flow = self.flow(folder)
            flow.read_full_combo_state = Mock(side_effect=['full_combo', 'unknown'])
            def worker(args, **kwargs):
                path = Path(args[-1])
                (path.parent/'armed').touch()
                return SimpleNamespace(poll=lambda: 0, returncode=0)
            with patch('chart_live.subprocess.Popen', side_effect=worker), self.assertRaisesRegex(FlowError, '星标'):
                flow.play_chart(1, ChartSelection('306', 'expert'), {'duration': 120}, 0, {})
            self.assertFalse(list(Path(folder).rglob('start')))


if __name__ == '__main__':
    unittest.main()
