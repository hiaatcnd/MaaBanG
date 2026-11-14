"""Pause/exit recovery with fresh-page stubs and optional private OCR replay."""
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from chart_policy import ChartOptions
from chart_sync import stage_state
from costume_unlock import FlowError
from online_live import OnlineLiveFlow


def hit(text, box):
    return SimpleNamespace(text=text, box=list(box), score=.99)


PAUSE_BOX = (1208, 20, 64, 65)
INTERRUPT = hit('中断', (470, 430, 150, 55))
CONFIRM = hit('确定', (740, 450, 120, 55))


def stage_frame():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[587:589, 280:1000] = (230, 230, 40)
    frame[36:47, 980:1170] = (40, 220, 40)
    return frame


def page(name, *, target=PAUSE_BOX):
    return SimpleNamespace(name=name, target=target, image=stage_frame())


class OnlinePauseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.current = page('stage')
        self.select = lambda: self.current
        self.actions = []
        self.on_action = self.advance
        self.capture_count = 0
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        context = SimpleNamespace(tasker=SimpleNamespace(controller=None, stopping=False),
                                  get_node_data=lambda name: None)
        self.flow = OnlineLiveFlow(context, ChartOptions.parse({'mode': 'coop', 'fire': 0}),
                                   self.folder.name)
        self.flow.current_attempt = {}
        self.flow.image = self.current.image
        self.flow.foreground_package = Mock(return_value='com.bilibili.star.bili')
        self.flow.resume_game = Mock()
        self.flow.background_room = Mock()
        self.flow.back = Mock()
        self.flow.check_stop = Mock()
        self.flow.snap = Mock(side_effect=self.snap)
        self.flow.reco = Mock(side_effect=self.reco)
        self.flow.hit_text = Mock(side_effect=self.hit_text)
        self.flow.ocr = Mock(side_effect=self.modal_ocr)
        self.flow.ready_loading = Mock(side_effect=lambda: self.current.name == 'footer_loading')
        self.flow.room_disconnected = Mock(return_value=False)
        self.flow.recovery_pause_button = Mock(side_effect=lambda: self.current.target)
        self.flow.tap = Mock(side_effect=self.tap)
        self.flow.tap_hit = Mock(side_effect=lambda button: self.tap(
            button.box[0]+button.box[2]//2, button.box[1]+button.box[3]//2))
        self.flow.pause = Mock(side_effect=self.pause)
        clock = patch('online_live.time.monotonic', side_effect=lambda: self.now)
        clock.start(); self.addCleanup(clock.stop)
        modal = patch('online_live.dialog_box', side_effect=lambda image:
                      [250, 180, 780, 370] if self.current.name in
                      ('unknown_modal', 'purchase', 'paused', 'confirm',
                       'unknown_over_pause', 'purchase_over_leave') else None)
        modal.start(); self.addCleanup(modal.stop)

    def snap(self):
        self.flow.check_stop()
        self.now += .05  # Captures consume time even if a failed action loops.
        self.capture_count += 1
        self.current = self.select()
        self.flow.image = self.current.image
        return self.flow.image

    def pause(self, seconds):
        self.flow.check_stop()
        self.now += seconds

    def reco(self, name, **kwargs):
        if name == 'OL_LeaveConfirm' and self.current.name == 'purchase_over_leave':
            return True  # The broad recognizer can still see the background dialog.
        matches = {'CU_HomeBand': 'home', 'OL_LeaveConfirm': 'confirm',
                   'OL_WaitingMembers': 'waiting', 'OL_Loading': 'loading'}
        return self.current.name == matches.get(name)

    def modal_ocr(self, roi, *args, **kwargs):
        # Only the current panel is visible to bounded modal OCR. Fixed screen
        # queries below deliberately retain text from behind some overlays.
        pause = [hit('暂停', (560, 230, 100, 50)), INTERRUPT]
        confirm = [hit('确认中断', (540, 220, 150, 45)),
                   hit('中断演出返回主页？', (350, 315, 480, 60)), CONFIRM]
        purchase = [hit('确认', (560, 220, 100, 45)),
                    hit('是否消耗星石继续演出', (350, 315, 500, 60)), CONFIRM]
        unknown = [hit('通知', (560, 220, 100, 45)),
                   hit('尚未适配的弹窗内容', (350, 315, 480, 60))]
        return {'paused': pause, 'confirm': confirm, 'purchase': purchase,
                'unknown_modal': unknown, 'unknown_over_pause': unknown,
                'purchase_over_leave': purchase}.get(self.current.name, [])

    def hit_text(self, roi, expected, *args, **kwargs):
        candidates = {'paused': [hit('暂停', (560, 230, 100, 50)), INTERRUPT],
                      'confirm': [CONFIRM],
                      'purchase': [hit('是否消耗星石继续演出', (350, 260, 500, 60)), CONFIRM],
                      'unknown_over_pause': [hit('暂停', (560, 230, 100, 50)), INTERRUPT],
                      'purchase_over_leave': [CONFIRM]}
        x, y, w, h = roi
        for candidate in candidates.get(self.current.name, []):
            bx, by, bw, bh = candidate.box
            if re.search(expected, candidate.text) and x <= bx < x+w and y <= by < y+h:
                return candidate
        return None

    def tap(self, x, y):
        self.actions.append((self.now, self.current.name, (x, y), self.capture_count))
        self.on_action(x, y)

    def advance(self, x, y):
        following = {'stage': 'paused', 'paused': 'confirm', 'confirm': 'home'}
        if self.current.name in following:
            self.current = page(following[self.current.name])

    def pause_actions(self):
        return [action for action in self.actions if action[1] == 'stage']

    def assert_no_focus_loss(self):
        self.flow.background_room.assert_not_called()
        self.flow.back.assert_not_called()

    def test_waiting_members_then_real_stage_pauses_only_after_overlay_disappears(self):
        waiting = page('waiting')
        # Keep the selected post-wait stage independent of the previous capture.
        stage = self.current
        self.select = lambda: waiting if self.now < 104 else (stage if self.current is waiting else self.current)
        self.flow.recover_room()
        self.assertEqual([action[1] for action in self.actions], ['stage', 'paused', 'confirm'])
        self.assertGreaterEqual(self.actions[0][0], 104)
        self.assertGreaterEqual(self.actions[0][3], 2)
        self.assertEqual(self.current.name, 'home')
        self.assert_no_focus_loss()

    def test_first_pause_click_has_no_effect_then_bounded_retry_reaches_home(self):
        def advance(x, y):
            if self.current.name != 'stage' or len(self.pause_actions()) >= 2:
                self.advance(x, y)
        self.on_action = advance
        self.flow.recover_room()
        clicks = self.pause_actions()
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1][0]-clicks[0][0], 3)
        self.assertGreater(clicks[1][3], clicks[0][3])
        self.assertEqual([a[1] for a in self.actions], ['stage', 'stage', 'paused', 'confirm'])
        self.assert_no_focus_loss()

    def test_delayed_pause_menu_is_observed_without_another_pause_click(self):
        def advance(x, y):
            if self.current.name != 'stage':
                self.advance(x, y)
        self.on_action = advance
        self.select = lambda: (page('paused') if self.current.name == 'stage' and self.actions and
                               self.now-self.actions[0][0] >= 2 else self.current)
        self.flow.recover_room()
        self.assertEqual(len(self.pause_actions()), 1)
        self.assertEqual([a[1] for a in self.actions], ['stage', 'paused', 'confirm'])
        self.assert_no_focus_loss()

    def test_stage_waiting_and_loading_never_consume_pause_budget_or_background(self):
        for name in ('waiting', 'loading', 'footer_loading'):
            with self.subTest(name=name):
                self.current = page(name)
                self.actions.clear()
                self.flow.background_room.reset_mock()
                start = self.now
                with self.assertRaisesRegex(FlowError, '安全入口|等待|超时'):
                    self.flow.recover_room()
                self.assertEqual(self.actions, [])
                self.assertLess(self.now-start, 31)
                self.assert_no_focus_loss()

    def test_unknown_modal_and_purchase_confirmation_never_use_background_pause(self):
        for name in ('unknown_modal', 'purchase'):
            with self.subTest(name=name):
                self.current = page(name)
                self.actions.clear()
                with self.assertRaisesRegex(FlowError, '安全入口|超时'):
                    self.flow.recover_room()
                self.assertEqual(self.actions, [])
                self.assert_no_focus_loss()

    def test_unknown_overlay_does_not_use_background_pause_title_or_interrupt_button(self):
        self.current = page('unknown_over_pause')
        self.assertIsNotNone(self.flow.hit_text([210, 210, 850, 85], '^暂停$'))
        self.assertIsNotNone(self.flow.hit_text([250, 400, 700, 110], '^中断$'))
        with self.assertRaisesRegex(FlowError, '安全入口|超时'):
            self.flow.recover_room()
        self.assertEqual(self.actions, [])
        self.assertTrue(self.flow.ocr.called)
        self.assert_no_focus_loss()

    def test_background_leave_prompt_cannot_authorize_foreground_purchase_confirmation(self):
        self.current = page('purchase_over_leave')
        self.assertTrue(self.flow.reco('OL_LeaveConfirm'))
        self.assertIsNotNone(self.flow.hit_text([640, 410, 275, 170], '^确定$'))
        with self.assertRaisesRegex(FlowError, '安全入口|超时'):
            self.flow.recover_room()
        self.assertEqual(self.actions, [])
        self.assertTrue(self.flow.ocr.called)
        self.assert_no_focus_loss()

    def test_pause_resume_button_does_not_hide_the_unique_interrupt_action(self):
        for label in ('继续', '继续演出', '恢复演出'):
            with self.subTest(label=label):
                self.current = page('paused')
                self.actions.clear()
                resume = hit(label, (720, 430, 200, 55))
                self.flow.ocr.side_effect = lambda *args, **kwargs: (
                    self.modal_ocr(*args, **kwargs)+([resume] if self.current.name == 'paused' else []))
                self.flow.recover_room()
                self.assertEqual([action[1] for action in self.actions], ['paused', 'confirm'])
                self.assertEqual(self.actions[0][2],
                                 (INTERRUPT.box[0]+INTERRUPT.box[2]//2,
                                  INTERRUPT.box[1]+INTERRUPT.box[3]//2))
                self.assertEqual(self.current.name, 'home')
                self.assert_no_focus_loss()

    def test_pause_resume_label_does_not_allow_consumption_text_in_modal_body(self):
        self.current = page('paused')
        resume = hit('继续演出', (720, 430, 200, 55))
        cost = hit('是否消耗星石继续演出', (350, 315, 500, 60))
        self.flow.ocr.side_effect = lambda *args, **kwargs: self.modal_ocr(*args, **kwargs)+[resume, cost]
        with self.assertRaisesRegex(FlowError, '安全入口|超时'):
            self.flow.recover_room()
        self.assertEqual(self.actions, [])
        self.assert_no_focus_loss()

    def test_target_must_be_seen_stable_on_two_fresh_frames(self):
        normal = page('stage')
        moved = page('stage', target=(1188, 20, 64, 65))
        initial = [normal, moved, moved]
        self.select = lambda: initial[self.capture_count-1] if self.capture_count <= 3 else self.current
        self.flow.recover_room()
        first = self.pause_actions()[0]
        self.assertGreaterEqual(first[3], 3)
        self.assertEqual(first[2], (1220, 52))

    def test_missing_or_continuously_moving_button_never_uses_old_coordinates(self):
        for moving in (False, True):
            with self.subTest(moving=moving):
                self.actions.clear()
                self.select = lambda: page('stage', target=(
                    (1208 if self.capture_count % 2 else 1188), 20, 64, 65) if moving else None)
                with self.assertRaisesRegex(FlowError, '安全入口|暂停|超时'):
                    self.flow.recover_room()
                self.assertEqual(self.actions, [])
                self.assert_no_focus_loss()

    def test_menu_or_target_disappearing_after_first_click_stops_stale_retry(self):
        def advance(x, y):
            self.current = page('unknown_modal')
        self.on_action = advance
        with self.assertRaisesRegex(FlowError, '安全入口|超时'):
            self.flow.recover_room()
        self.assertEqual(len(self.actions), 1)
        self.assert_no_focus_loss()

    def test_stuck_pause_stops_after_three_clicks_and_last_result_grace(self):
        self.on_action = lambda x, y: None
        with self.assertRaisesRegex(FlowError, '暂停|3次|上限|未消失'):
            self.flow.recover_room()
        self.assertEqual(len(self.pause_actions()), 3)
        self.assertGreaterEqual(self.now-self.actions[-1][0], 3)
        self.assert_no_focus_loss()

    def test_last_pause_click_can_finish_after_delayed_response(self):
        self.on_action = lambda x, y: self.advance(x, y) if self.current.name != 'stage' else None
        self.select = lambda: (page('paused') if len(self.pause_actions()) == 3 and
                               self.current.name == 'stage' and
                               self.now-self.pause_actions()[-1][0] >= 2 else self.current)
        self.flow.recover_room()
        self.assertEqual(len(self.pause_actions()), 3)
        self.assertEqual(self.current.name, 'home')
        self.assert_no_focus_loss()

    def test_stop_request_after_missed_pause_prevents_retry(self):
        self.on_action = lambda x, y: None
        def stop():
            if self.actions:
                raise FlowError('任务已被用户停止')
        self.flow.check_stop.side_effect = stop
        with self.assertRaisesRegex(FlowError, '用户停止'):
            self.flow.recover_room()
        self.assertEqual(len(self.actions), 1)
        self.assert_no_focus_loss()

    def test_foreground_change_before_input_does_not_tap_other_application(self):
        reads = [0]
        def foreground():
            reads[0] += 1
            return 'com.bilibili.star.bili' if reads[0] == 1 else 'com.android.settings'
        self.flow.foreground_package.side_effect = foreground
        with self.assertRaisesRegex(FlowError, '前台|停止'):
            self.flow.recover_room()
        self.assertEqual(self.actions, [])

    def test_missed_interrupt_and_confirmation_retry_only_current_visible_button(self):
        counts = {'paused': 0, 'confirm': 0}
        def advance(x, y):
            name = self.current.name
            if name in counts:
                counts[name] += 1
                if counts[name] == 1:
                    return
            self.advance(x, y)
        self.on_action = advance
        self.flow.recover_room()
        self.assertEqual([a[1] for a in self.actions], ['stage', 'paused', 'paused', 'confirm', 'confirm'])
        for name in counts:
            clicks = [a for a in self.actions if a[1] == name]
            self.assertGreaterEqual(clicks[1][0]-clicks[0][0], 3)
        self.assertEqual(self.flow.report['completed_rounds'], 0)
        self.assert_no_focus_loss()


class RecordedOnlinePauseRecoveryTests(unittest.TestCase):
    """Set MAABANG_ONLINE_PAUSE_FIXTURES to the private attempt report directory."""

    def test_recorded_waiting_overlay_and_running_stage_with_production_recognition(self):
        fixture_dir = Path(os.environ.get('MAABANG_ONLINE_PAUSE_FIXTURES',
                                         ROOT/'debug/online-pause-recovery'))
        paths = [fixture_dir/'interrupted_attempt6.png', fixture_dir/'error.png']
        if not all(path.is_file() for path in paths):
            self.skipTest('Private waiting-members and running-stage screenshots required')
        bundle = Path(os.environ.get('MAABANG_TEST_RESOURCE_DIR', ROOT/'assets/resource'))
        if not (bundle/'model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        frames = [np.asarray(Image.open(path).convert('RGB'))[:, :, ::-1].copy() for path in paths]
        self.assertTrue(stage_state(frames[0]))
        self.assertIsNotNone(stage_state(frames[1]))
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'online-pause-recovery-replay'
            def screencap(self): return frames[0]
        resource = Resource()
        self.assertTrue(resource.post_bundle(bundle).wait().succeeded)
        controller = Controller()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        case, errors = self, []
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self, context, argv):
                    try:
                        flow = OnlineLiveFlow(context, ChartOptions.parse({'mode': 'coop', 'fire': 0}), folder)
                        for index, frame in enumerate(frames):
                            flow.invalidate_frame_recognition()
                            flow.image = frame
                            target = flow.recovery_pause_button()
                            if index == 0:
                                case.assertIsNone(target, 'The waiting overlay dims the pause button')
                            else:
                                case.assertIsNotNone(target)
                                x, y, w, h = target
                                case.assertTrue(1200 <= x < x+w <= 1280 and 0 <= y < y+h <= 110)
                            case.assertEqual(bool(flow.reco('OL_WaitingMembers')), index == 0)
                        # Real OCR and button pixels feed the production recovery;
                        # the controller never sends input to a game device.
                        now, captures, clicks = [100.], [0], []
                        done = [False]
                        real_reco, real_hit_text = flow.reco, flow.hit_text
                        flow.foreground_package = Mock(return_value='com.bilibili.star.bili')
                        flow.background_room = Mock(side_effect=AssertionError('No focus-loss shortcut'))
                        flow.back = Mock(side_effect=AssertionError('No stale Back input'))
                        flow.current_attempt = {}
                        def snap():
                            captures[0] += 1
                            now[0] += .1
                            flow.invalidate_frame_recognition()
                            flow.image = frames[0 if now[0] < 102 else 1]
                            return flow.image
                        flow.snap = Mock(side_effect=snap)
                        flow.pause = Mock(side_effect=lambda seconds: now.__setitem__(0, now[0]+seconds))
                        flow.reco = Mock(side_effect=lambda name, **kwargs:
                            (name == 'CU_HomeBand') if done[0] else real_reco(name, **kwargs))
                        flow.hit_text = Mock(side_effect=lambda *args, **kwargs:
                            None if done[0] else real_hit_text(*args, **kwargs))
                        def tap(x, y):
                            case.assertGreaterEqual(now[0], 102)
                            case.assertTrue(1200 < x < 1280 and 0 < y < 110)
                            clicks.append((now[0], captures[0]))
                            if len(clicks) == 2:
                                done[0] = True
                        flow.tap = Mock(side_effect=tap)
                        with patch('online_live.time.monotonic', side_effect=lambda: now[0]):
                            flow.recover_room()
                        case.assertEqual(len(clicks), 2)
                        case.assertGreaterEqual(clicks[1][0]-clicks[0][0], 3)
                        case.assertGreater(clicks[1][1], clicks[0][1])
                        return True
                    except Exception:
                        import traceback
                        errors.append(traceback.format_exc())
                        return False
            resource.register_custom_action('OnlinePauseRecoveryReplay', Check())
            job = tasker.post_task('OnlinePauseRecoveryReplay', {'OnlinePauseRecoveryReplay': {
                'action': 'Custom', 'custom_action': 'OnlinePauseRecoveryReplay',
                'pre_delay': 0, 'post_delay': 0}}).wait()
            self.assertTrue(job.succeeded, '\n'.join(errors))


if __name__ == '__main__':
    unittest.main()
