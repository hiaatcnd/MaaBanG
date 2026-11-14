"""Story reading unlocks eligible stories without enabling member practice."""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from costume_unlock import FlowError
from mining_policy import MiningOptions
from mining_stories import StoryMiningFlow


class StoryUnlockTests(unittest.TestCase):
    def flow(self, *, level='60 / 60', required=1, amounts=('100/10',), result='reward'):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False, controller=None))
        # Old saved settings must not keep cancelling eligible stories.
        flow = StoryMiningFlow(context, MiningOptions.parse({'practice': False, 'unlock': False}), 'unused')
        flow.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        row = {}
        flow.report['members'].append(row)
        state = dict(time=0., page='detail', submitted=0, submitted_at=None, rewards=0)
        rois = [[400+140*i, 397, 110, 35] for i in range(len(amounts))]

        def scene():
            if state['submitted_at'] is not None and result == 'reward':
                if not state['rewards'] and state['time']-state['submitted_at'] >= 2:
                    return 'MN_StoryReward'
            return {'detail': 'MN_MemberDetail', 'unlock': 'MN_StoryUnlock'}[state['page']]

        def text(roi):
            if roi == [654, 348, 150, 35]:
                return level
            if roi == [465, 444, 360, 48]:
                return f'解锁等级{required}级以上'
            return amounts[rois.index(roi)]

        def tap(x, y):
            if y == 620 and x in (225, 480):
                state['page'] = 'unlock'
            elif (x, y) == (770, 543):
                self.assertEqual(state['page'], 'unlock')
                state['submitted'] += 1
                state['submitted_at'] = state['time']
                if result == 'stop':
                    context.tasker.stopping = True
            else:
                raise AssertionError(f'Unexpected input: {(x, y)}')

        def click(node):
            self.assertEqual(node, 'MN_StoryRewardOK')
            state.update(page='detail', rewards=state['rewards']+1)

        flow.snap = Mock(side_effect=flow.check_stop)
        flow.text = Mock(side_effect=text)
        flow.reco = Mock(side_effect=lambda node: node == scene())
        flow.tap = Mock(side_effect=tap)
        flow.click = Mock(side_effect=click)
        flow.cancel_story_unlock = Mock(side_effect=lambda: state.update(page='detail'))
        flow.save_frame = Mock()
        flow.dismiss_notifications = Mock(return_value=False)
        flow.rarity = Mock(return_value=3)
        return flow, row, state, rois

    def clock(self, state, rois):
        stack = ExitStack()
        stack.enter_context(patch('mining_stories.time.monotonic', side_effect=lambda: state['time']))
        stack.enter_context(patch('mining_stories.time.sleep', side_effect=lambda seconds:
                                  state.update(time=state['time']+seconds)))
        stack.enter_context(patch('mining_stories.material_rois', return_value=rois))
        return stack

    def test_eligible_story_and_memory_unlock_without_practice_or_an_unlock_switch(self):
        for memory in (False, True):
            with self.subTest(memory=memory):
                flow, row, state, rois = self.flow(required=50 if memory else 1,
                                                   amounts=('100/10', '500/100', '200/200'))
                flow.practice = Mock(side_effect=AssertionError('must not train an eligible member'))
                with self.clock(state, rois):
                    flow.read_story(memory, row)
                self.assertEqual(state['submitted'], 1)
                self.assertEqual(row['materials'], [(100, 10), (500, 100), (200, 200)])
                self.assertEqual(row['status'], 'read_reward_confirmed')
                self.assertEqual(flow.report['read'], 1)
                self.assertGreaterEqual(state['time'], 3.5)
                flow.cancel_story_unlock.assert_not_called()
                flow.practice.assert_not_called()

    def test_insufficient_or_unreadable_materials_never_submit(self):
        for amounts in (('9/10',), ('?',), ('100/0',), ('100/10', '?'), ()):
            with self.subTest(amounts=amounts):
                flow, row, state, rois = self.flow(amounts=amounts)
                with self.clock(state, rois):
                    flow.read_story(False, row)
                self.assertEqual(state['submitted'], 0)
                self.assertEqual(flow.report['read'], 0)
                self.assertEqual(row['status'], 'insufficient_or_unreadable_materials')
                flow.cancel_story_unlock.assert_called_once()

    def test_insufficient_level_still_respects_disabled_practice(self):
        flow, row, state, rois = self.flow(level='1 / 60', required=60)
        with self.clock(state, rois):
            flow.read_story(True, row)
        self.assertEqual(state['submitted'], 0)
        self.assertEqual(row['status'], 'practice_disabled_or_ineligible')
        flow.cancel_story_unlock.assert_called_once()
        flow.tap.assert_called_once_with(480, 620)

    def test_unknown_unlock_result_never_repeats_material_submission_or_counts_read(self):
        flow, row, state, rois = self.flow(result='unknown')
        with self.clock(state, rois), self.assertRaisesRegex(FlowError, '奖励确认超时'):
            flow.read_story(False, row)
        self.assertEqual(state['submitted'], 1)
        self.assertEqual(flow.report['read'], 0)
        self.assertNotEqual(row.get('status'), 'read_reward_confirmed')
        self.assertLessEqual(state['time'], 91)

    def test_stop_after_submission_never_repeats_consumption(self):
        flow, row, state, rois = self.flow(result='stop')
        with self.clock(state, rois), self.assertRaisesRegex(FlowError, '用户停止'):
            flow.read_story(False, row)
        self.assertEqual(state['submitted'], 1)
        self.assertEqual(flow.report['read'], 0)
        flow.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
