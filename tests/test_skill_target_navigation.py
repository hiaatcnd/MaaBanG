"""Navigation recovery must use fresh state before any repeated member click."""
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
from skill_practice import SkillPracticeFlow
from mining_policy import member_signature


class SkillTargetNavigationTests(unittest.TestCase):
    def flow(self, page='list'):
        tasker = SimpleNamespace(stopping=False, controller=None)
        flow = SkillPracticeFlow(SimpleNamespace(tasker=tasker), Path('unused'))
        flow.image = np.full((720, 1280, 3), 255, dtype=np.uint8)
        state = dict(time=0., page=page, frames=0,
                     candidates=[((344, 258), 'member')],
                     clicks=[], returns=[], on_frame=None,
                     on_click=None, on_return=None)
        back_button = SimpleNamespace(box=[10, 15, 85, 75])

        def snap():
            flow.check_stop()
            state['frames'] += 1
            if state['on_frame']:
                state['on_frame']()
            return flow.image

        def ensure_filter():
            snap()
            if state['page'] != 'list':
                raise FlowError('培养成员列表页面已变化')

        def require_material():
            if state['page'] != 'detail':
                raise FlowError('未确认技能练习休息室页面')

        def reco(node):
            if node == 'SP_Filter':
                return state['page'] == 'list'
            if node in ('SP_Page', 'SP_Lounge'):
                return state['page'] == 'detail'
            if node == 'CU_Back':
                return back_button if state['page'] == 'detail' else None
            return None

        def tap(*point):
            state['clicks'].append((state['time'], point))
            if state['on_click']:
                state['on_click']()

        def tap_hit(button):
            self.assertIs(button, back_button)
            state['returns'].append(state['time'])
            if state['on_return']:
                state['on_return']()

        flow.snap = Mock(side_effect=snap)
        flow.ensure_filter = Mock(side_effect=ensure_filter)
        flow.require_material_page = Mock(side_effect=require_material)
        flow.require_clear_notification_overlay = Mock()
        flow.reco = Mock(side_effect=reco)
        flow.target_candidates = Mock(side_effect=lambda **kwargs: state['candidates'])
        flow.tap = Mock(side_effect=tap)
        flow.tap_hit = Mock(side_effect=tap_hit)
        flow.submit_batch = Mock()
        return flow, state

    def clock(self, state):
        stack = ExitStack()
        stack.enter_context(patch('skill_practice.time.monotonic', side_effect=lambda: state['time']))
        stack.enter_context(patch('skill_practice.time.sleep', side_effect=lambda seconds:
                                  state.update(time=state['time']+seconds)))
        stack.enter_context(patch('skill_practice.same_member_portrait',
                                  side_effect=lambda current, previous: current == previous))
        stack.enter_context(patch('skill_practice.log'))
        return stack

    def test_missed_target_click_rechecks_same_member_then_retries(self):
        for success_on in (1, 2, 3):
            with self.subTest(success_on=success_on):
                flow, state = self.flow()
                def clicked():
                    if len(state['clicks']) == success_on:
                        state['page'] = 'detail'
                state['on_click'] = clicked
                with self.clock(state):
                    flow.open_target('member')
                self.assertEqual(len(state['clicks']), success_on)
                self.assertTrue(all(second[0]-first[0] >= 8
                                    for first, second in zip(state['clicks'], state['clicks'][1:])))
                self.assertGreaterEqual(flow.target_candidates.call_count, success_on*2)
                flow.submit_batch.assert_not_called()

    def test_delayed_target_page_does_not_repeat_click(self):
        flow, state = self.flow()
        def frame():
            if state['clicks'] and state['time']-state['clicks'][0][0] >= 3:
                state['page'] = 'detail'
        state['on_frame'] = frame
        with self.clock(state):
            flow.open_target('member')
        self.assertEqual(len(state['clicks']), 1)
        flow.submit_batch.assert_not_called()

    def test_unexpected_detail_before_first_click_is_rejected(self):
        flow, state = self.flow('detail')
        with self.clock(state), self.assertRaisesRegex(FlowError, '页面已变化'):
            flow.open_target('member')
        flow.tap.assert_not_called()
        flow.target_candidates.assert_not_called()

    def test_delayed_target_during_retry_confirmation_is_not_clicked_again(self):
        flow, state = self.flow()
        def frame():
            if state['clicks'] and state['time']-state['clicks'][0][0] >= 8.2:
                state['page'] = 'detail'
        state['on_frame'] = frame
        with self.clock(state):
            flow.open_target('member')
        self.assertEqual(len(state['clicks']), 1)

    def test_target_retry_exhaustion_is_bounded(self):
        flow, state = self.flow()
        with self.clock(state), self.assertRaisesRegex(FlowError, '补点上限'):
            flow.open_target('member')
        self.assertEqual(len(state['clicks']), 3)
        self.assertLess(state['time'], 30)
        flow.submit_batch.assert_not_called()

    def test_delayed_detail_during_filter_wait_accepts_success_without_retry(self):
        flow, state = self.flow()
        original = flow.ensure_filter.side_effect
        def ensure():
            if state['clicks']:
                state['page'] = 'detail'
                raise FlowError('页面识别超时：SP_Filter')
            original()
        flow.ensure_filter.side_effect = ensure
        with self.clock(state):
            flow.open_target('member')
        self.assertEqual(len(state['clicks']), 1)
        flow.submit_batch.assert_not_called()

    def test_reflowed_target_uses_its_fresh_location(self):
        flow, state = self.flow()
        state['candidates'] = [((467, 381), 'member')]
        state['on_click'] = lambda: state.update(page='detail')
        with self.clock(state):
            flow.open_target('member')
        self.assertEqual([point for _, point in state['clicks']], [(467, 381)])

    def test_changed_target_is_not_clicked_again(self):
        flow, state = self.flow()
        state['on_click'] = lambda: state.update(candidates=[((344, 258), 'other')])
        with self.clock(state), self.assertRaisesRegex(FlowError, '已变化|唯一定位'):
            flow.open_target('member')
        self.assertEqual(len(state['clicks']), 1)
        flow.submit_batch.assert_not_called()

    def test_unknown_page_is_not_clicked_again(self):
        flow, state = self.flow()
        state['on_click'] = lambda: state.update(page='unknown')
        with self.clock(state), self.assertRaisesRegex(FlowError, '页面已变化'):
            flow.open_target('member')
        self.assertEqual(len(state['clicks']), 1)

    def test_stopping_interrupts_target_wait_without_retry(self):
        flow, state = self.flow()
        def frame():
            if state['clicks'] and state['time']-state['clicks'][0][0] >= 1:
                flow.ctx.tasker.stopping = True
        state['on_frame'] = frame
        with self.clock(state), self.assertRaisesRegex(FlowError, '用户停止'):
            flow.open_target('member')
        self.assertEqual(len(state['clicks']), 1)
        self.assertLess(state['time'], 3)

    def test_stable_target_requires_two_matching_fresh_frames(self):
        flow, state = self.flow()
        observations = []
        def candidates(**kwargs):
            observations.append((state['frames'], state['time']))
            return state['candidates']
        flow.target_candidates.side_effect = candidates
        with self.clock(state):
            self.assertEqual(flow.stable_target('member'), (344, 258))
        self.assertEqual(len(observations), 2)
        self.assertGreater(observations[1][0], observations[0][0])
        self.assertGreaterEqual(observations[1][1]-observations[0][1], .2)
        flow.tap.assert_not_called()

    def test_real_bonus_animation_does_not_block_first_member_click(self):
        flow, state = self.flow()
        images = [np.array(Image.open(ROOT/'tests/fixtures/skill_practice'/filename)
                           .convert('RGB'))[:, :, ::-1].copy()
                  for filename in ('target_bonus_before.png', 'target_bonus_after.png')]
        flow.image = images[0].copy()
        reads = iter(images)
        flow.ensure_filter.side_effect = lambda: setattr(flow, 'image', next(reads).copy())
        flow.target_candidates = SkillPracticeFlow.target_candidates.__get__(flow, SkillPracticeFlow)
        signature = member_signature(images[0], (344, 257))
        state['on_click'] = lambda: state.update(page='detail')
        with patch('skill_practice.time.sleep'):
            flow.open_target(signature)
        self.assertEqual([point for _, point in state['clicks']], [(344, 257)])
        flow.submit_batch.assert_not_called()

    def test_real_card_motion_or_replacement_still_blocks_first_click(self):
        before = np.array(Image.open(ROOT/'tests/fixtures/skill_practice/target_bonus_before.png')
                          .convert('RGB'))[:, :, ::-1].copy()
        for change in ('movement', 'artwork'):
            with self.subTest(change=change):
                flow, state = self.flow()
                after = before.copy()
                if change == 'movement':
                    after[:] = 255
                    after[197:677, 285:1140] = before[185:665, 285:1140]
                else:
                    after[235:278, 315:370] = (170, 50, 80)
                images = iter((before, after))
                flow.image = before.copy()
                flow.ensure_filter.side_effect = lambda: setattr(flow, 'image', next(images).copy())
                flow.target_candidates = SkillPracticeFlow.target_candidates.__get__(flow, SkillPracticeFlow)
                with patch('skill_practice.time.sleep'), self.assertRaises(FlowError):
                    flow.open_target(member_signature(before, (344, 257)))
                flow.tap.assert_not_called()

    def test_moving_or_ambiguous_target_is_rejected_before_click(self):
        for changed in ('position', 'artwork', 'duplicate'):
            with self.subTest(changed=changed):
                flow, state = self.flow()
                def candidates(**kwargs):
                    if flow.target_candidates.call_count == 2:
                        if changed == 'position':
                            state['candidates'] = [((344, 270), 'member')]
                        elif changed == 'duplicate':
                            state['candidates'].append(((467, 258), 'member'))
                        else:
                            state['candidates'] = [((344, 258), 'changed artwork')]
                    return state['candidates']
                flow.target_candidates.side_effect = candidates
                with self.clock(state), self.assertRaises(FlowError):
                    flow.open_target('member')
                flow.tap.assert_not_called()

    def test_missed_return_click_relocates_back_then_retries(self):
        for success_on in (1, 2, 3):
            with self.subTest(success_on=success_on):
                flow, state = self.flow('detail')
                def returned():
                    if len(state['returns']) == success_on:
                        state['page'] = 'list'
                state['on_return'] = returned
                with self.clock(state):
                    flow.return_to_list()
                self.assertEqual(len(state['returns']), success_on)
                self.assertTrue(all(second-first >= 8
                                    for first, second in zip(state['returns'], state['returns'][1:])))
                self.assertEqual(flow.reco.call_args_list.count(unittest.mock.call('CU_Back')), success_on)

    def test_delayed_return_does_not_click_back_on_list(self):
        flow, state = self.flow('detail')
        def frame():
            if state['returns'] and state['time']-state['returns'][0] >= 3:
                state['page'] = 'list'
        state['on_frame'] = frame
        with self.clock(state):
            flow.return_to_list()
        self.assertEqual(len(state['returns']), 1)

    def test_return_retry_exhaustion_is_bounded(self):
        flow, state = self.flow('detail')
        with self.clock(state), self.assertRaisesRegex(FlowError, '补点上限'):
            flow.return_to_list()
        self.assertEqual(len(state['returns']), 3)
        self.assertLess(state['time'], 27)

    def test_already_returned_does_not_press_back_again(self):
        flow, state = self.flow('list')
        with self.clock(state):
            flow.return_to_list()
        flow.tap_hit.assert_not_called()

    def test_unknown_return_page_prevents_repeated_back(self):
        flow, state = self.flow('detail')
        state['on_return'] = lambda: state.update(page='unknown')
        with self.clock(state), self.assertRaisesRegex(FlowError, '未确认技能练习休息室页面'):
            flow.return_to_list()
        self.assertEqual(len(state['returns']), 1)

    def test_changed_member_page_prevents_repeated_back(self):
        flow, state = self.flow('detail')
        state['on_return'] = lambda: flow.image.__setitem__((slice(145, 525), slice(110, 610)), 120)
        with self.clock(state), self.assertRaisesRegex(FlowError, '页面已变化'):
            flow.return_to_list()
        self.assertEqual(len(state['returns']), 1)

    def test_stopping_interrupts_return_wait_without_retry(self):
        flow, state = self.flow('detail')
        def frame():
            if state['returns'] and state['time']-state['returns'][0] >= 1:
                flow.ctx.tasker.stopping = True
        state['on_frame'] = frame
        with self.clock(state), self.assertRaisesRegex(FlowError, '用户停止'):
            flow.return_to_list()
        self.assertEqual(len(state['returns']), 1)
        self.assertLess(state['time'], 3)


if __name__ == '__main__':
    unittest.main()
