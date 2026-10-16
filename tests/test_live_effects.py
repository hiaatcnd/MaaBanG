"""Shared live effects checks, using actual bottom-bar screenshots offline."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from costume_unlock import FlowError
from live_effects import LiveEffectsMixin


def hit(text, x=185, y=635, w=100, h=30):
    return SimpleNamespace(text=text, box=(x, y, w, h))


def frame(mode='OFF', *, cut_in=False, cut_label=True, member_portrait=False):
    image = np.full((720, 1280, 3), 255, dtype=np.uint8)
    hits = []
    if mode is not None:
        image[635:665, 130:155] = 100  # Selector arrow remains present.
        hits.append(hit(mode))
    if cut_label:
        hits.append(hit('3D Cut in模式', 528, w=150))
        image[637:663, 487:513] = [150, 30, 255] if cut_in else [160] * 3
    if member_portrait:
        hits.extend([hit('默认乐队成员', 365), hit('进行演出', 365, 652)])
        image[637:663, 487:513] = [150, 30, 255]
    return image, hits


class FakeFlow(LiveEffectsMixin):
    def __init__(self, frames, *, ready=True, stuck=False):
        self.frames = frames
        self.index = 0
        self.image = frames[0][0]
        self.snap = Mock(side_effect=lambda: setattr(self, 'image', frames[self.index][0]))
        self.ocr = Mock(side_effect=lambda roi: frames[self.index][1]
                        if roi == [110, 615, 610, 75]
                        else [hit('准备完毕!')] if ready else [])
        def tap(*point):
            if not stuck:
                self.index = min(self.index + 1, len(frames) - 1)
        self.tap = Mock(side_effect=tap)


class LiveEffectsTests(unittest.TestCase):
    def test_one_pass_when_already_off_reuses_current_frame(self):
        flow = FakeFlow([frame()])
        state = flow.disable_mv(fresh=False)
        self.assertEqual(state.background, 'off')
        self.assertFalse(state.cut_in)
        flow.snap.assert_not_called()
        flow.tap.assert_not_called()
        flow.ocr.assert_called_once_with([110, 615, 610, 75])

    def test_cycles_background_then_disables_newly_exposed_cut_in(self):
        flow = FakeFlow([frame('3D演奏', cut_label=False, member_portrait=True),
                         frame('FILM LIVE MV', cut_label=False),
                         frame(cut_in=True), frame()])
        flow.disable_mv()
        self.assertEqual([call.args for call in flow.tap.call_args_list],
                         [(145, 650), (145, 650), (500, 650)])
        self.assertEqual(flow.snap.call_count, 4)

    def test_member_checkbox_and_portrait_are_never_treated_as_cut_in(self):
        flow = FakeFlow([frame('3D演奏', cut_label=False, member_portrait=True), frame()])
        flow.disable_mv()
        flow.tap.assert_called_once_with(145, 650)

    def test_no_selector_needs_blank_pixels_and_a_ready_button(self):
        flow = FakeFlow([frame(None)])
        state = flow.disable_mv(fresh=False)
        self.assertIsNone(state.background)
        flow.tap.assert_not_called()
        self.assertEqual(flow.ocr.call_count, 2)
        with self.assertRaisesRegex(FlowError, '准备页'):
            FakeFlow([frame(None)], ready=False).disable_mv()
        data = frame(None)
        data[0][620:680, 110:320] = 100
        with self.assertRaisesRegex(FlowError, 'MV/3D'):
            FakeFlow([data]).disable_mv()

    def test_cut_in_turns_off_on_song_without_selector(self):
        flow = FakeFlow([frame(None, cut_in=True), frame(None)])
        flow.disable_mv()
        flow.tap.assert_called_once_with(500, 650)

    def test_missing_label_or_unrecognized_checkbox_blocks_input(self):
        missing = frame(cut_label=False, member_portrait=True)
        unknown = frame()
        unknown[0][637:663, 487:513] = [220, 100, 30]
        for data in (missing, unknown):
            with self.subTest(data=data[1]):
                flow = FakeFlow([data])
                with self.assertRaisesRegex(FlowError, 'Cut in'):
                    flow.disable_mv()
                flow.tap.assert_not_called()

    def test_unknown_or_ambiguous_background_blocks_input(self):
        for text in ('', '加载中', 'OFFMV', 'OFFON'):
            with self.subTest(text=text):
                flow = FakeFlow([frame(text)])
                with self.assertRaisesRegex(FlowError, 'MV/3D'):
                    flow.disable_mv()
                flow.tap.assert_not_called()

    def test_nonconverging_controls_do_not_click_indefinitely(self):
        for data, count in ((frame('3D演奏', cut_label=False), 4), (frame(cut_in=True), 1)):
            flow = FakeFlow([data], stuck=True)
            with self.assertRaisesRegex(FlowError, '未能关闭'):
                flow.disable_mv()
            self.assertEqual(flow.tap.call_count, count)

    def test_already_off_does_not_reuse_a_previous_song_state(self):
        flow = FakeFlow([frame(), frame(cut_in=True)])
        flow.disable_mv()
        flow.index = 1
        flow.frames.append(frame())
        flow.disable_mv()
        flow.tap.assert_called_once_with(500, 650)

    def test_supported_ready_button_labels_for_selectorless_songs(self):
        for label in ('演出开始', '准备完毕!', '准备开始', '第1曲开始', '第2曲开始', '第3曲开始'):
            with self.subTest(label=label):
                flow = FakeFlow([frame(None)])
                flow.ocr.side_effect = lambda roi: flow.frames[0][1] if roi == [110, 615, 610, 75] else [hit(label)]
                flow.disable_mv()
                flow.tap.assert_not_called()

    def test_online_quick_click_keeps_short_existing_wait(self):
        flow = FakeFlow([frame(cut_in=True), frame()])
        flow.quick_tap = flow.tap
        flow.tap = Mock(side_effect=AssertionError('slow tap should not be used'))
        flow.disable_mv()
        flow.quick_tap.assert_called_once_with(500, 650)

    def test_real_screenshots_with_local_ocr(self):
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from daily_tasks import DailyFlow
        model = ROOT / 'assets/resource/model/ocr'
        if not all((model / name).is_file() for name in ('det.onnx', 'rec.onnx', 'keys.txt')):
            self.skipTest('Local OCR models are required for the screenshot integration test')

        def load(relative):
            image = np.asarray(Image.open(ROOT / 'tests/fixtures' / relative).convert('RGB'))[:, :, ::-1].copy()
            if image.shape[:2] == (80, 610):
                full = np.full((720, 1280, 3), 255, dtype=np.uint8)
                full[610:690, 100:710] = image
                image = full
            return image

        cycle = [load(f'live_presets/{name}.png') for name in ('3d', 'film_mv', 'off')]
        stage_3d = load('live_presets/stage_3d.png')
        absent = [load(name) for name in ('coop/ready.png', 'cp/ready.png', 'lowres/challenge_ready.png')]
        resource = Resource()
        self.assertTrue(resource.post_bundle(ROOT / 'assets/resource').wait().succeeded)

        class Screens(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'live-effects-regression'
            def screencap(self): return cycle[0]

        class Flow(LiveEffectsMixin, DailyFlow):
            pass

        controller = Screens()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        case = self
        observed = {}

        class Check(CustomAction):
            def run(self, context, argv):
                try:
                    for initial in (cycle[0], stage_3d):
                        frames = [initial, *cycle[1:]]
                        flow = Flow(context)
                        index = [0]
                        flow.snap = lambda: setattr(flow, 'image', frames[index[0]])
                        def tap(x, y):
                            case.assertEqual((x, y), (145, 650))
                            index[0] += 1
                        flow.tap = Mock(side_effect=tap)
                        flow.disable_mv()
                        case.assertEqual(flow.tap.call_count, 2)
                    flow.tap = Mock(side_effect=AssertionError('already-off controls should not be clicked'))
                    for image in absent:
                        flow.image = image
                        state = flow.disable_mv(fresh=False)
                        case.assertIsNone(state.background)
                        case.assertFalse(state.cut_in)
                    observed['ok'] = True
                    return True
                except Exception as exc:
                    observed['error'] = repr(exc)
                    return False

        resource.register_custom_action('LiveEffectsFixture', Check())
        job = tasker.post_task('LiveEffectsFixture', {
            'LiveEffectsFixture': {'action': 'Custom', 'custom_action': 'LiveEffectsFixture'}}).wait()
        self.assertTrue(job.succeeded, observed)


if __name__ == '__main__':
    unittest.main()
