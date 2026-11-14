"""Story mining must select the member list before accepting list contents."""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from costume_unlock import FlowError
from mining_policy import MiningOptions
from mining_stories import StoryMiningFlow, training_tab_index


class StoryMemberTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = {
            'members': 'mining/selection_anon_grid.png',
            'skills': 'skill_practice/list.png',
            'empty_skills': 'skill_practice/empty_list.png',
            'materials': 'skill_practice/materials.png',
        }
        cls.frames = {}
        for name, path in paths.items():
            with Image.open(ROOT/'tests/fixtures'/path) as frame:
                cls.frames[name] = np.array(frame.convert('RGB'))[:, :, ::-1].copy()
        cls.frames['unknown'] = np.full((720, 1280, 3), 255, dtype=np.uint8)

    def flow(self, page='skills'):
        flow = StoryMiningFlow(SimpleNamespace(tasker=SimpleNamespace(
            stopping=False, controller=None)), MiningOptions.parse({'memories': False}), Path('unused'))
        flow.image = self.frames[page].copy()
        state = dict(page=page, time=0., frames=0, overlay=False,
                     label=True, clicks=[], on_frame=None, on_click=None)
        tab = SimpleNamespace(text='成员一览', box=[10, 185, 210, 68])

        def snap():
            flow.check_stop()
            state['frames'] += 1
            if state['on_frame']:
                state['on_frame']()
            flow.image = self.frames[state['page']].copy()
            return flow.image

        def reco(node):
            return tab if node == 'MN_Members' and state['label'] else None

        def tap_hit(hit):
            self.assertIs(hit, tab)
            self.assertFalse(state['overlay'])
            state['clicks'].append(state['time'])
            if state['on_click']:
                state['on_click']()

        def clear_overlay():
            if state['overlay']:
                raise FlowError('存在未确认弹窗')

        flow.snap = Mock(side_effect=snap)
        flow.save_frame = Mock()
        flow.reco = Mock(side_effect=reco)
        flow.tap_hit = Mock(side_effect=tap_hit)
        flow.tap = Mock(side_effect=AssertionError('must locate the tab again before tapping'))
        flow.click = Mock(side_effect=AssertionError('must confirm selected state before clicking'))
        flow.require_clear_notification_overlay = Mock(side_effect=clear_overlay)
        return flow, state

    def clock(self, state):
        stack = ExitStack()
        stack.enter_context(patch('mining_stories.time.monotonic', side_effect=lambda: state['time']))
        stack.enter_context(patch('mining_stories.time.sleep', side_effect=lambda seconds:
                                  state.update(time=state['time']+seconds)))
        stack.enter_context(patch('mining_stories.dialog_box', side_effect=lambda image:
                                  [100, 100, 900, 500] if state['overlay'] else None))
        stack.enter_context(patch('mining_stories.log'))
        return stack

    def test_real_selected_tabs_distinguish_member_list_from_skill_list(self):
        for name, expected in [('members', 0), ('skills', 3), ('empty_skills', 3),
                               ('materials', None), ('unknown', None)]:
            with self.subTest(name=name):
                self.assertEqual(training_tab_index(self.frames[name]), expected)

    def test_ambiguous_selected_tabs_and_wrong_shape_are_unknown(self):
        ambiguous = self.frames['skills'].copy()
        ambiguous[185:265, :230] = self.frames['members'][185:265, :230]
        self.assertIsNone(training_tab_index(ambiguous))
        self.assertIsNone(training_tab_index(np.zeros((360, 640, 3), dtype=np.uint8)))

    def test_already_selected_member_list_needs_no_click(self):
        flow, state = self.flow('members')
        with self.clock(state):
            flow.ensure_member_list()
        flow.tap_hit.assert_not_called()
        self.assertGreaterEqual(state['frames'], 2)

    def test_missed_tab_click_is_relocated_and_retried(self):
        for success_on in (1, 2, 3):
            with self.subTest(success_on=success_on):
                flow, state = self.flow()
                def clicked():
                    if len(state['clicks']) == success_on:
                        state['page'] = 'members'
                state['on_click'] = clicked
                with self.clock(state):
                    flow.ensure_member_list()
                self.assertEqual(len(state['clicks']), success_on)
                self.assertTrue(all(second-first >= 8
                                    for first, second in zip(state['clicks'], state['clicks'][1:])))
                self.assertEqual(training_tab_index(flow.image), 0)

    def test_delayed_tab_selection_does_not_repeat_click(self):
        flow, state = self.flow()
        def frame():
            if state['clicks'] and state['time']-state['clicks'][0] >= 3:
                state['page'] = 'members'
        state['on_frame'] = frame
        with self.clock(state):
            flow.ensure_member_list()
        self.assertEqual(len(state['clicks']), 1)

    def test_tab_selection_retries_have_a_total_limit(self):
        flow, state = self.flow()
        with self.clock(state), self.assertRaises(FlowError):
            flow.ensure_member_list()
        self.assertEqual(len(state['clicks']), 3)
        self.assertLessEqual(state['time'], 29)
        self.assertNotEqual(flow.report['status'], 'finished')

    def test_unknown_page_after_click_is_not_clicked_again(self):
        flow, state = self.flow()
        state['on_click'] = lambda: state.update(page='unknown', label=False)
        with self.clock(state), self.assertRaises(FlowError):
            flow.ensure_member_list()
        self.assertEqual(len(state['clicks']), 1)
        self.assertLessEqual(state['time'], 29)

    def test_missing_tab_label_prevents_click_even_when_skill_tab_is_visible(self):
        flow, state = self.flow()
        state['label'] = False
        with self.clock(state), self.assertRaises(FlowError):
            flow.ensure_member_list()
        flow.tap_hit.assert_not_called()

    def test_overlay_prevents_navigation_click(self):
        flow, state = self.flow()
        state['overlay'] = True
        with self.clock(state), self.assertRaises(FlowError):
            flow.ensure_member_list()
        flow.tap_hit.assert_not_called()
        self.assertLessEqual(state['time'], 29)

    def test_closing_dialog_is_waited_out_without_extra_clicks(self):
        for method, initial_page, expected_clicks in (
                ('ensure_member_list', 'skills', 1), ('wait_member_list', 'members', 0)):
            with self.subTest(method=method):
                flow, state = self.flow(initial_page)
                state['overlay'] = True
                state['on_frame'] = lambda: state.update(overlay=state['time'] < .6)
                state['on_click'] = lambda: state.update(page='members')
                with self.clock(state):
                    getattr(flow, method)()
                self.assertEqual(len(state['clicks']), expected_clicks)
                self.assertTrue(all(clicked_at >= .6 for clicked_at in state['clicks']))
                self.assertGreaterEqual(state['time'], .8)
                self.assertEqual(training_tab_index(flow.image), 0)

    def test_nonconsecutive_sidebar_states_cannot_trigger_a_click_or_success(self):
        flow, state = self.flow('skills')
        pages = ['skills', 'members', 'skills', 'members', 'members']
        state['on_frame'] = lambda: state.update(page=pages[min(state['frames']-1, len(pages)-1)])
        with self.clock(state):
            flow.ensure_member_list()
        self.assertEqual(state['frames'], 5)
        flow.tap_hit.assert_not_called()

    def test_stopping_interrupts_tab_recovery_without_second_click(self):
        flow, state = self.flow()
        def frame():
            if state['clicks'] and state['time']-state['clicks'][0] >= 1:
                flow.ctx.tasker.stopping = True
        state['on_frame'] = frame
        with self.clock(state), self.assertRaisesRegex(FlowError, '用户停止'):
            flow.ensure_member_list()
        self.assertEqual(len(state['clicks']), 1)
        self.assertLess(state['time'], 3)

    def test_member_list_guard_requires_two_fresh_selected_frames(self):
        flow, state = self.flow('members')
        observations = []
        state['on_frame'] = lambda: observations.append((state['frames'], state['time']))
        with self.clock(state):
            flow.wait_member_list()
        self.assertGreaterEqual(len(observations), 2)
        self.assertGreater(observations[-1][0], observations[0][0])
        self.assertGreater(observations[-1][1], observations[0][1])
        flow.tap_hit.assert_not_called()

    def test_guard_rejects_skill_empty_message_without_switching_tabs(self):
        flow, state = self.flow('empty_skills')
        with self.clock(state), self.assertRaises(FlowError):
            flow.wait_member_list()
        self.assertLess(state['time'], 1)
        self.assertNotEqual(flow.report['status'], 'finished')
        flow.tap_hit.assert_not_called()
        flow.tap.assert_not_called()

    def test_story_filter_cannot_open_on_the_skill_tab(self):
        flow, state = self.flow('empty_skills')
        flow.wait = Mock()
        with self.clock(state), self.assertRaisesRegex(FlowError, '未选中成员一览'):
            flow.filter_unread(False)
        flow.tap.assert_not_called()
        flow.tap_hit.assert_not_called()
        flow.wait.assert_not_called()

    def test_story_run_cannot_report_skill_empty_page_as_finished(self):
        flow, state = self.flow('empty_skills')
        flow.member_list = Mock()
        flow.filter_unread = Mock()
        flow.home = Mock()
        flow.open_member_detail = Mock()
        flow.read_story = Mock()
        flow.hit_text = Mock(return_value=SimpleNamespace(text='没有可以进行技能练习的成员。'))
        with self.clock(state), patch('mining_stories.member_cards', return_value=[]), \
                self.assertRaisesRegex(FlowError, '未选中成员一览'):
            flow.run()
        self.assertEqual(flow.report['status'], 'running')
        self.assertEqual(flow.report['read'], 0)
        flow.hit_text.assert_not_called()
        flow.home.assert_not_called()
        flow.open_member_detail.assert_not_called()
        flow.read_story.assert_not_called()

    def test_guard_rejects_second_frame_switch_to_skill_list(self):
        flow, state = self.flow('members')
        def frame():
            if state['frames'] >= 2:
                state['page'] = 'skills'
        state['on_frame'] = frame
        with self.clock(state), self.assertRaises(FlowError):
            flow.wait_member_list()
        flow.tap_hit.assert_not_called()

    def test_guard_restarts_two_frame_confirmation_after_unknown_frame(self):
        flow, state = self.flow('members')
        pages = ['members', 'unknown', 'members', 'members']
        state['on_frame'] = lambda: state.update(page=pages[min(state['frames']-1, len(pages)-1)])
        with self.clock(state):
            flow.wait_member_list()
        self.assertEqual(state['frames'], 4)
        flow.tap_hit.assert_not_called()

    def test_guard_waits_for_delayed_member_list_without_clicking(self):
        flow, state = self.flow('unknown')
        def frame():
            if state['time'] >= 1:
                state['page'] = 'members'
        state['on_frame'] = frame
        with self.clock(state):
            flow.wait_member_list()
        self.assertGreaterEqual(state['time'], 1)
        self.assertLess(state['time'], 3)
        flow.tap_hit.assert_not_called()

    def test_guard_unknown_page_wait_is_bounded(self):
        flow, state = self.flow('unknown')
        with self.clock(state), self.assertRaises(FlowError):
            flow.wait_member_list()
        self.assertLessEqual(state['time'], 13)
        flow.tap_hit.assert_not_called()

    def test_guard_overlay_cannot_be_accepted_as_selected_list(self):
        flow, state = self.flow('members')
        state['overlay'] = True
        with self.clock(state), self.assertRaises(FlowError):
            flow.wait_member_list()
        self.assertLessEqual(state['time'], 13)
        flow.tap_hit.assert_not_called()

    def test_guard_honors_stop_request(self):
        flow, state = self.flow('unknown')
        def frame():
            if state['time'] >= 1:
                flow.ctx.tasker.stopping = True
        state['on_frame'] = frame
        with self.clock(state), self.assertRaisesRegex(FlowError, '用户停止'):
            flow.wait_member_list()
        self.assertLess(state['time'], 3)
        flow.tap_hit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
