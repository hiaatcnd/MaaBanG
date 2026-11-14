"""A missed member tap may be retried only on the same confirmed list target."""
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
from mining_policy import MiningOptions, member_cards, member_signature, same_member_portrait
from mining_stories import StoryMiningFlow


class StoryMemberNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = {}
        for name, path in {
                'list': 'mining/selection_anon_grid.png',
                'skill': 'skill_practice/list.png',
                'detail': 'mining/selection_anon_detail.png',
                'before': 'mining/story_open_before.png',
                'after': 'mining/story_open_after.png'}.items():
            with Image.open(ROOT/'tests/fixtures'/path) as frame:
                cls.frames[name] = np.array(frame.convert('RGB'))[:, :, ::-1].copy()
        cls.frames['unknown'] = np.full((720, 1280, 3), 255, dtype=np.uint8)

    def flow(self, page='list'):
        flow = StoryMiningFlow(SimpleNamespace(tasker=SimpleNamespace(
            stopping=False, controller=None)), MiningOptions.parse({}), Path('unused'))
        flow.image = self.frames[page].copy()
        state = dict(time=0., page=page, frames=0, clicks=[], overlay=False,
                     candidates=[((344, 258), 'target')], on_frame=None,
                     on_click=None, list_frames=None)

        def snap():
            flow.check_stop()
            state['frames'] += 1
            if state['on_frame']:
                state['on_frame']()
            if state['page'] == 'list' and state['list_frames']:
                frame = state['list_frames'][(state['frames']-1) % len(state['list_frames'])]
            else:
                frame = self.frames[state['page']]
            flow.image = frame.copy()
            return flow.image

        def reco(node):
            if node == 'MN_MemberDetail':
                return state['page'] == 'detail'
            if node == 'MN_Members':
                return state['page'] in ('list', 'skill')
            return None

        def tap(*point):
            self.assertEqual(state['page'], 'list')
            self.assertFalse(state['overlay'])
            state['clicks'].append((state['time'], point))
            if state['on_click']:
                state['on_click']()

        def clear_overlay():
            if state['overlay']:
                raise FlowError('存在未确认弹窗')

        flow.snap = Mock(side_effect=snap)
        flow.reco = Mock(side_effect=reco)
        flow.tap = Mock(side_effect=tap)
        flow.require_clear_notification_overlay = Mock(side_effect=clear_overlay)
        flow.save_frame = Mock()
        flow.ocr = Mock(return_value=[])
        flow.read_story = Mock()
        audit = dict(position=[344, 258], status='opening', attempts=0, clicks=[])
        return flow, state, audit

    def clock(self, state, *, real_portraits=False):
        stack = ExitStack()
        stack.enter_context(patch('mining_stories.time.monotonic', side_effect=lambda: state['time']))
        stack.enter_context(patch('mining_stories.time.sleep', side_effect=lambda seconds:
                                  state.update(time=state['time']+seconds)))
        stack.enter_context(patch('mining_stories.log'))
        if not real_portraits:
            stack.enter_context(patch('mining_stories.member_cards',
                                      side_effect=lambda image: [point for point, _ in state['candidates']]))
            stack.enter_context(patch('mining_stories.member_signature', side_effect=lambda image, point:
                                      next(signature for current, signature in state['candidates'] if current == point)))
            stack.enter_context(patch('mining_stories.same_member_portrait',
                                      side_effect=lambda first, second: first == second))
        return stack

    def test_missed_first_or_second_tap_recovers_without_story_consumption(self):
        for success_on in (1, 2, 3):
            with self.subTest(success_on=success_on):
                flow, state, audit = self.flow()
                def clicked():
                    if len(state['clicks']) == success_on:
                        state['page'] = 'detail'
                state['on_click'] = clicked
                with self.clock(state):
                    point = flow.enter_member_detail('target', audit)
                self.assertEqual(point, (344, 258))
                self.assertEqual(audit['attempts'], success_on)
                self.assertEqual(len(audit['clicks']), success_on)
                self.assertTrue(all(second[0]-first[0] >= 8
                                    for first, second in zip(state['clicks'], state['clicks'][1:])))
                flow.read_story.assert_not_called()

    def test_delayed_detail_is_confirmed_without_a_second_tap(self):
        flow, state, audit = self.flow()
        def frame():
            if state['clicks'] and state['time']-state['clicks'][0][0] >= 3:
                state['page'] = 'detail'
        state['on_frame'] = frame
        with self.clock(state):
            flow.enter_member_detail('target', audit)
        self.assertEqual(audit['attempts'], 1)

    def test_detail_arriving_during_retry_confirmation_is_not_tapped(self):
        flow, state, audit = self.flow()
        def frame():
            if state['clicks'] and state['time']-state['clicks'][0][0] >= 8.2:
                state['page'] = 'detail'
        state['on_frame'] = frame
        with self.clock(state):
            flow.enter_member_detail('target', audit)
        self.assertEqual(audit['attempts'], 1)

    def test_detail_requires_two_fresh_frames(self):
        flow, state, audit = self.flow()
        observed = []
        state['on_click'] = lambda: state.update(page='detail')
        def frame():
            if state['page'] == 'detail':
                observed.append((state['frames'], state['time']))
        state['on_frame'] = frame
        with self.clock(state):
            flow.enter_member_detail('target', audit)
        self.assertEqual(len(observed), 2)
        self.assertGreater(observed[1][0], observed[0][0])
        self.assertGreater(observed[1][1], observed[0][1])
        self.assertEqual(audit['attempts'], 1)

    def test_once_detail_has_been_seen_return_to_list_cannot_trigger_retry(self):
        flow, state, audit = self.flow()
        state['detail_emitted'] = False
        def frame():
            if not state['clicks']:
                return
            state['page'] = 'list' if state['detail_emitted'] else 'detail'
            state['detail_emitted'] = True
        state['on_frame'] = frame
        with self.clock(state), self.assertRaisesRegex(FlowError, '不重复打开'):
            flow.enter_member_detail('target', audit)
        self.assertEqual(audit['attempts'], 1)
        flow.read_story.assert_not_called()

    def test_unexpected_detail_before_any_tap_is_not_accepted(self):
        flow, state, audit = self.flow('detail')
        with self.clock(state), self.assertRaisesRegex(FlowError, '未知成员'):
            flow.enter_member_detail('target', audit)
        flow.tap.assert_not_called()

    def test_different_member_in_old_location_is_not_tapped_again(self):
        flow, state, audit = self.flow()
        state['on_click'] = lambda: state.update(candidates=[((344, 258), 'other')])
        with self.clock(state), self.assertRaisesRegex(FlowError, '目标成员已变化'):
            flow.enter_member_detail('target', audit)
        self.assertEqual(audit['attempts'], 1)

    def test_missing_or_ambiguous_target_prevents_first_tap(self):
        for candidates in ([], [((344, 258), 'target'), ((467, 258), 'target')]):
            with self.subTest(candidates=candidates):
                flow, state, audit = self.flow()
                state['candidates'] = candidates
                with self.clock(state), self.assertRaisesRegex(FlowError, '唯一定位'):
                    flow.enter_member_detail('target', audit)
                flow.tap.assert_not_called()

    def test_reflowed_target_is_relocated_before_retry(self):
        flow, state, audit = self.flow()
        def clicked():
            if len(state['clicks']) == 1:
                state['candidates'] = [((467, 381), 'target')]
            else:
                state['page'] = 'detail'
        state['on_click'] = clicked
        with self.clock(state):
            point = flow.enter_member_detail('target', audit)
        self.assertEqual(point, (467, 381))
        self.assertEqual([point for _, point in state['clicks']], [(344, 258), (467, 381)])

    def test_persistently_moving_target_expires_without_click(self):
        flow, state, audit = self.flow()
        state['on_frame'] = lambda: state.update(candidates=[((344 + 6*(state['frames'] % 2), 258), 'target')])
        with self.clock(state), self.assertRaisesRegex(FlowError, '已点击 0 次'):
            flow.enter_member_detail('target', audit)
        flow.tap.assert_not_called()
        self.assertLessEqual(state['time'], 29)

    def test_unknown_page_wait_is_bounded_without_reusing_list_coordinates(self):
        flow, state, audit = self.flow()
        state['on_click'] = lambda: state.update(page='unknown')
        with self.clock(state), self.assertRaises(FlowError):
            flow.enter_member_detail('target', audit)
        self.assertEqual(audit['attempts'], 1)
        self.assertLessEqual(state['time'], 29)

    def test_other_training_tab_is_rejected_before_click(self):
        flow, state, audit = self.flow('skill')
        with self.clock(state), self.assertRaisesRegex(FlowError, '其他培养标签'):
            flow.enter_member_detail('target', audit)
        flow.tap.assert_not_called()

    def test_retry_count_and_total_wait_are_bounded(self):
        flow, state, audit = self.flow()
        with self.clock(state), self.assertRaisesRegex(FlowError, '已点击 3 次'):
            flow.enter_member_detail('target', audit)
        self.assertEqual(audit['attempts'], 3)
        self.assertEqual(len(state['clicks']), 3)
        self.assertLessEqual(state['time'], 29)
        self.assertTrue(all(second[0]-first[0] >= 8
                            for first, second in zip(state['clicks'], state['clicks'][1:])))
        flow.read_story.assert_not_called()

    def test_deadline_expiring_during_recognition_prevents_a_new_tap(self):
        flow, state, audit = self.flow()
        samples = 0
        def slow_signature(image, point):
            nonlocal samples
            samples += 1
            if samples == 2:
                state['time'] = 28.1
            return 'target'
        with self.clock(state), patch('mining_stories.member_signature', side_effect=slow_signature), \
                self.assertRaisesRegex(FlowError, '已点击 0 次'):
            flow.enter_member_detail('target', audit)
        self.assertEqual(samples, 2)
        self.assertEqual(audit['attempts'], 0)
        flow.tap.assert_not_called()

    def test_overlay_prevents_click_or_retry(self):
        for after_tap in (False, True):
            with self.subTest(after_tap=after_tap):
                flow, state, audit = self.flow()
                if after_tap:
                    state['on_click'] = lambda: state.update(overlay=True)
                else:
                    state['overlay'] = True
                with self.clock(state), self.assertRaisesRegex(FlowError, '弹窗'):
                    flow.enter_member_detail('target', audit)
                self.assertEqual(audit['attempts'], int(after_tap))

    def test_stop_interrupts_wait_without_retry(self):
        flow, state, audit = self.flow()
        def frame():
            if state['clicks'] and state['time']-state['clicks'][0][0] >= 1:
                flow.ctx.tasker.stopping = True
        state['on_frame'] = frame
        with self.clock(state), self.assertRaisesRegex(FlowError, '用户停止'):
            flow.enter_member_detail('target', audit)
        self.assertEqual(audit['attempts'], 1)
        self.assertLess(state['time'], 3)

    def test_real_failed_tap_frames_retain_same_target_despite_border_jitter(self):
        before, after = self.frames['before'], self.frames['after']
        first, second = member_cards(before)[0], member_cards(after)[0]
        self.assertEqual(first, (344, 257))
        self.assertEqual(second, (344, 259))
        signature = member_signature(before, first)
        self.assertTrue(same_member_portrait(member_signature(after, second), signature))
        flow, state, audit = self.flow()
        state['list_frames'] = [before, after]
        def clicked():
            if len(state['clicks']) == 2:
                state['page'] = 'detail'
        state['on_click'] = clicked
        with self.clock(state, real_portraits=True):
            point = flow.enter_member_detail(signature, audit)
        self.assertEqual(audit['attempts'], 2)
        self.assertTrue(all(clicked_point in (first, second) for _, clicked_point in state['clicks']))
        self.assertIn(point, (first, second))
        flow.read_story.assert_not_called()

    def test_open_member_records_pending_audit_before_navigation_and_ready_afterward(self):
        flow, state, _ = self.flow()
        def clicked():
            audit = flow.report['selections'][0]
            self.assertEqual(audit['status'], 'opening')
            self.assertEqual(audit['attempts'], 1)
            state['page'] = 'detail'
        state['on_click'] = clicked
        flow.ocr.return_value = [SimpleNamespace(text='测试成员', box=[300, 180, 100, 30])]
        with self.clock(state):
            identity = flow.open_member_detail((344, 258))
        self.assertEqual(identity, '测试成员')
        self.assertEqual(flow.report['selections'][0]['status'], 'detail_ready')
        self.assertEqual(flow.report['selections'][0]['clicks'], [[344, 258]])
        self.assertEqual([call.args[0] for call in flow.save_frame.call_args_list],
                         ['selection_1_list.png', 'selection_1_detail.png'])

    def test_failed_navigation_retains_audit_and_does_not_read_details_or_story(self):
        flow, state, _ = self.flow()
        with self.clock(state), self.assertRaises(FlowError):
            flow.open_member_detail((344, 258))
        audit = flow.report['selections'][0]
        self.assertEqual(audit['status'], 'detail_unconfirmed')
        self.assertEqual(audit['attempts'], 3)
        self.assertEqual(len(audit['clicks']), 3)
        self.assertIn('error', audit)
        flow.ocr.assert_not_called()
        flow.read_story.assert_not_called()
        self.assertEqual(flow.report['status'], 'running')


if __name__ == '__main__':
    unittest.main()
