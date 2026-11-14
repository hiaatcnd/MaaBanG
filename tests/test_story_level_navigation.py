"""Level-sort navigation verifies state before repeating reversible clicks."""
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
from mining_stories import StoryMiningFlow


class StoryLevelNavigationTests(unittest.TestCase):
    box = [129, 34, 1022, 653]

    @classmethod
    def setUpClass(cls):
        with Image.open(ROOT/'tests/fixtures/mining/selection_anon_grid.png') as image:
            cls.list_frame = np.array(image.convert('RGB'))[:, :, ::-1].copy()

    def flow(self, page='filter'):
        flow = StoryMiningFlow(SimpleNamespace(tasker=SimpleNamespace(
            stopping=False, controller=None)), MiningOptions.parse({}), Path('unused'))
        flow.image = self.list_frame.copy()
        state = dict(time=0., frames=0, clicks=[], swipes=[], page=page,
                     selected=False, direction='ascending', label=True,
                     level_box=[805, 350, 50, 30], confirm_box=[700, 600, 100, 35],
                     list_frames=0, on_frame=None, on_click=None, overlay=False)

        def snap():
            flow.check_stop()
            state['frames'] += 1
            if state['on_frame']:
                state['on_frame']()
            flow.check_stop()
            if state['page'] == 'list':
                state['list_frames'] += 1
            return flow.image

        def reco(node):
            if node == 'MN_MemberFilter':
                return state['page'] == 'filter'
            if node == 'MN_Members':
                return state['page'] == 'list'
            return None

        def hit_text(roi, pattern):
            if pattern == '^等级$' and state['label']:
                return SimpleNamespace(box=state['level_box'])
            if pattern == '^确定$' and state['confirm_box']:
                return SimpleNamespace(box=state['confirm_box'])
            return None

        def tap(*point):
            flow.check_stop()
            self.assertIn(state['page'], ('list', 'filter'))
            self.assertFalse(state['overlay'])
            state['clicks'].append((state['time'], point, state['page']))
            if state['on_click']:
                state['on_click']()

        def swipe(*points):
            flow.check_stop()
            state['swipes'].append(points)
            state['time'] += .5

        def clear_overlay():
            if state['overlay']:
                raise FlowError('存在未确认弹窗')

        flow.snap = Mock(side_effect=snap)
        flow.reco = Mock(side_effect=reco)
        flow.hit_text = Mock(side_effect=hit_text)
        flow.checkbox = Mock(side_effect=lambda *args, **kwargs: state['selected'])
        flow.tap = Mock(side_effect=tap)
        flow.swipe = Mock(side_effect=swipe)
        flow.require_clear_notification_overlay = Mock(side_effect=clear_overlay)
        flow.save_frame = Mock()
        return flow, state

    def clock(self, state):
        stack = ExitStack()
        stack.enter_context(patch('mining_stories.time.monotonic', side_effect=lambda: state['time']))
        stack.enter_context(patch('mining_stories.time.sleep', side_effect=lambda seconds:
                                  state.update(time=state['time']+seconds)))
        stack.enter_context(patch('mining_stories.member_filter_box', return_value=self.box))
        stack.enter_context(patch('mining_stories.training_tab_index', return_value=0))
        stack.enter_context(patch('mining_stories.dialog_box', return_value=None))
        stack.enter_context(patch('mining_stories.level_sort_direction',
                                  side_effect=lambda image: state['direction']))
        stack.enter_context(patch('mining_stories.log'))
        return stack

    def test_selected_level_radio_needs_no_click_or_scroll(self):
        flow, state = self.flow()
        state['selected'] = True
        with self.clock(state):
            flow.select_level_sort(self.box)
        self.assertEqual(state['clicks'], [])
        self.assertEqual(state['swipes'], [])
        self.assertGreaterEqual(flow.checkbox.call_count, 2)

    def test_level_radio_recovers_missed_clicks_using_fresh_position(self):
        for success_on in (1, 2, 3):
            with self.subTest(success_on=success_on):
                flow, state = self.flow()

                def clicked():
                    if len(state['clicks']) == success_on:
                        state['selected'] = True
                    else:
                        state['level_box'] = [805, 353, 50, 30]

                state['on_click'] = clicked
                with self.clock(state):
                    flow.select_level_sort(self.box)
                self.assertEqual(len(state['clicks']), success_on)
                self.assertEqual(state['clicks'][0][1], (781, 365))
                if success_on > 1:
                    self.assertEqual(state['clicks'][1][1], (781, 368))
                self.assertTrue(all(b[0]-a[0] >= 3 for a, b in
                                    zip(state['clicks'], state['clicks'][1:])))

    def test_delayed_radio_selection_is_not_toggled_back(self):
        flow, state = self.flow()

        def frame():
            if state['clicks'] and state['time']-state['clicks'][0][0] >= 2.6:
                state['selected'] = True

        state['on_frame'] = frame
        with self.clock(state):
            flow.select_level_sort(self.box)
        self.assertEqual(len(state['clicks']), 1)

    def test_disappearing_level_target_stops_before_reusing_coordinates(self):
        flow, state = self.flow()
        state['on_click'] = lambda: state.update(label=False)
        with self.clock(state), self.assertRaisesRegex(FlowError, '离开可见区域'):
            flow.select_level_sort(self.box)
        self.assertEqual(len(state['clicks']), 1)

    def test_radio_retry_exhaustion_is_failure(self):
        flow, state = self.flow()
        with self.clock(state), self.assertRaisesRegex(FlowError, '未确认等级排序已选中'):
            flow.select_level_sort(self.box)
        self.assertEqual(len(state['clicks']), 3)
        self.assertLessEqual(state['time'], 16.2)

    def test_radio_navigation_stops_when_filter_page_changes(self):
        flow, state = self.flow()
        state['on_click'] = lambda: state.update(page='unknown')
        with self.clock(state), self.assertRaisesRegex(FlowError, '弹窗已变化'):
            flow.select_level_sort(self.box)
        self.assertEqual(len(state['clicks']), 1)

    def test_radio_wait_responds_to_stop_without_another_click(self):
        flow, state = self.flow()
        state['on_click'] = lambda: setattr(flow.ctx.tasker, 'stopping', True)
        with self.clock(state), self.assertRaisesRegex(FlowError, '用户停止'):
            flow.select_level_sort(self.box)
        self.assertEqual(len(state['clicks']), 1)

    def test_already_descending_needs_two_frames_and_zero_clicks(self):
        flow, state = self.flow('list')
        state['direction'] = 'descending'
        with self.clock(state):
            flow.ensure_level_descending()
        self.assertEqual(state['frames'], 2)
        self.assertEqual(state['clicks'], [])

    def test_direction_recovers_only_while_still_ascending(self):
        flow, state = self.flow('list')

        def clicked():
            if len(state['clicks']) == 2:
                state['direction'] = 'descending'

        state['on_click'] = clicked
        with self.clock(state):
            flow.ensure_level_descending()
        self.assertEqual(len(state['clicks']), 2)
        self.assertTrue(all(row[1] == (1148, 131) for row in state['clicks']))
        self.assertGreaterEqual(state['clicks'][1][0]-state['clicks'][0][0], 4)

    def test_delayed_direction_change_never_flips_back_to_ascending(self):
        flow, state = self.flow('list')

        def frame():
            if state['clicks'] and state['time']-state['clicks'][0][0] >= 3.8:
                state['direction'] = 'descending'

        state['on_frame'] = frame
        with self.clock(state):
            flow.ensure_level_descending()
        self.assertEqual(len(state['clicks']), 1)

    def test_unknown_direction_is_never_toggled(self):
        for initial in (True, False):
            with self.subTest(initial=initial):
                flow, state = self.flow('list')
                if initial:
                    state['direction'] = None
                else:
                    state['on_click'] = lambda: state.update(direction=None)
                with self.clock(state), self.assertRaisesRegex(FlowError, '未确认等级降序'):
                    flow.ensure_level_descending()
                self.assertEqual(len(state['clicks']), 0 if initial else 1)

    def test_non_level_sort_cannot_establish_a_level_boundary(self):
        flow, state = self.flow('list')
        state['label'] = False
        with self.clock(state), self.assertRaisesRegex(FlowError, '未使用等级排序'):
            flow.ensure_level_descending()
        self.assertEqual(state['clicks'], [])

    def test_direction_retry_exhaustion_is_failure(self):
        flow, state = self.flow('list')
        with self.clock(state), self.assertRaisesRegex(FlowError, '未确认等级降序'):
            flow.ensure_level_descending()
        self.assertEqual(len(state['clicks']), 3)
        self.assertLessEqual(state['time'], 20.2)

    def test_direction_change_of_page_does_not_reuse_sort_button(self):
        flow, state = self.flow('list')
        state['on_click'] = lambda: state.update(page='unknown')
        with self.clock(state), self.assertRaisesRegex(FlowError, '未确认等级降序'):
            flow.ensure_level_descending()
        self.assertEqual(len(state['clicks']), 1)

    def test_direction_wait_responds_to_stop(self):
        flow, state = self.flow('list')
        state['on_click'] = lambda: setattr(flow.ctx.tasker, 'stopping', True)
        with self.clock(state), self.assertRaisesRegex(FlowError, '用户停止'):
            flow.ensure_level_descending()
        self.assertEqual(len(state['clicks']), 1)

    def test_filter_confirmation_recovers_misses_then_waits_for_two_list_frames(self):
        for success_on in (1, 2, 3):
            with self.subTest(success_on=success_on):
                flow, state = self.flow()

                def clicked():
                    if len(state['clicks']) == success_on:
                        state['page'] = 'list'
                    else:
                        state['confirm_box'] = [702, 602, 100, 35]

                state['on_click'] = clicked
                with self.clock(state):
                    flow.confirm_member_filter(self.box)
                self.assertEqual(len(state['clicks']), success_on)
                self.assertEqual(state['list_frames'], 2)
                self.assertTrue(all(row[2] == 'filter' for row in state['clicks']))
                if success_on > 1:
                    self.assertEqual(state['clicks'][1][1], (752, 619))
                self.assertTrue(all(b[0]-a[0] >= 4 for a, b in
                                    zip(state['clicks'], state['clicks'][1:])))

    def test_delayed_filter_close_is_not_clicked_twice(self):
        flow, state = self.flow()

        def frame():
            if state['clicks'] and state['time']-state['clicks'][0][0] >= 3.8:
                state['page'] = 'list'

        state['on_frame'] = frame
        with self.clock(state):
            flow.confirm_member_filter(self.box)
        self.assertEqual(len(state['clicks']), 1)
        self.assertEqual(state['list_frames'], 2)

    def test_reappearing_filter_after_list_is_not_submitted_again(self):
        flow, state = self.flow()
        state['on_click'] = lambda: state.update(page='list')

        def frame():
            if state['list_frames']:
                state['page'] = 'filter'

        state['on_frame'] = frame
        with self.clock(state), self.assertRaisesRegex(FlowError, '页面再次变化'):
            flow.confirm_member_filter(self.box)
        self.assertEqual(len(state['clicks']), 1)

    def test_missing_filter_confirm_never_clicks_stale_coordinates(self):
        flow, state = self.flow()
        state['on_click'] = lambda: state.update(confirm_box=None)
        with self.clock(state), self.assertRaisesRegex(FlowError, '未确认成员筛选已应用'):
            flow.confirm_member_filter(self.box)
        self.assertEqual(len(state['clicks']), 1)

    def test_filter_confirm_retry_exhaustion_is_not_completion(self):
        flow, state = self.flow()
        with self.clock(state), self.assertRaisesRegex(FlowError, '不报告完成'):
            flow.confirm_member_filter(self.box)
        self.assertEqual(len(state['clicks']), 3)
        self.assertLessEqual(state['time'], 20.2)

    def test_filter_confirmation_responds_to_stop(self):
        flow, state = self.flow()
        state['on_click'] = lambda: setattr(flow.ctx.tasker, 'stopping', True)
        with self.clock(state), self.assertRaisesRegex(FlowError, '用户停止'):
            flow.confirm_member_filter(self.box)
        self.assertEqual(len(state['clicks']), 1)

    def test_unknown_popup_after_filter_submit_is_not_clicked(self):
        flow, state = self.flow()
        state['on_click'] = lambda: state.update(page='unknown', overlay=True)
        with self.clock(state), self.assertRaisesRegex(FlowError, '未确认弹窗'):
            flow.confirm_member_filter(self.box)
        self.assertEqual(len(state['clicks']), 1)

    def test_unknown_popup_blocks_the_sort_arrow(self):
        flow, state = self.flow('list')
        state['overlay'] = True
        with self.clock(state), self.assertRaisesRegex(FlowError, '未确认弹窗'):
            flow.ensure_level_descending()
        self.assertEqual(state['clicks'], [])


if __name__ == '__main__':
    unittest.main()
