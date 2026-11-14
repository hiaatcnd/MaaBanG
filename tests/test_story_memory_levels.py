"""Memory screening must save detail visits without skipping eligible members."""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from costume_unlock import FlowError
from mining_policy import MiningOptions
from mining_stories import StoryMiningFlow
from task_logging import finish


class MemoryLevelsTests(unittest.TestCase):
    def flow(self, **options):
        flow=StoryMiningFlow(SimpleNamespace(tasker=SimpleNamespace(stopping=False,controller=None)),
                             MiningOptions.parse({'stories':False,**options}),Path('unused'))
        flow.image=np.full((720,1280,3),255,dtype=np.uint8)
        flow.active_story_stars=flow.mining.stars
        for name in ('member_list','filter_unread','wait_member_list','ensure_level_descending',
                     'ensure_member_list_top','home','back','save_frame','swipe'):
            setattr(flow,name,Mock())
        flow.open_member_detail=Mock(return_value='成员')
        flow.read_story=Mock(side_effect=lambda memory,row: row.update(status='read_reward_confirmed'))
        flow.empty_member_list=Mock(return_value=True)
        return flow

    def cards(self, pages):
        stack=ExitStack()
        stack.enter_context(patch('mining_stories.member_cards',side_effect=pages))
        stack.enter_context(patch('mining_stories.member_signature',side_effect=lambda image,point:point))
        stack.enter_context(patch('mining_stories.same_member_portrait',side_effect=lambda first,second:first==second))
        stack.enter_context(patch('mining_stories.boundary_prefix_accounted',return_value=True))
        stack.enter_context(patch('mining_stories.log'))
        return stack

    def test_grouped_by_equal_threshold_without_modifying_user_options(self):
        flow=self.flow(stars='1,2,3,4,5')
        groups=[]
        flow.scan_story_members=Mock(side_effect=lambda memory,minimum:
            groups.append((memory,minimum,flow.active_story_stars)))
        flow.run()
        self.assertEqual(groups,[(True,60,frozenset({4,5})),(True,50,frozenset({3})),
                                 (True,30,frozenset({2})),(True,20,frozenset({1}))])
        self.assertEqual(flow.mining.stars,frozenset({1,2,3,4,5}))

    def test_practice_keeps_all_selected_stars_and_disables_level_cutoff(self):
        flow=self.flow(stars='3,4,5',practice=True)
        flow.scan_story_members=Mock()
        flow.run()
        flow.scan_story_members.assert_called_once_with(True,None)
        self.assertEqual(flow.active_story_stars,frozenset({3,4,5}))

    def test_first_low_level_ends_group_without_entering_or_claiming_a_read(self):
        flow=self.flow(stars='4,5')
        flow.confirmed_list_level=Mock(return_value=50)
        with self.cards([[(344,258),(467,258)],[(344,258),(467,258)]]):
            flow.scan_story_members(True,60)
        flow.open_member_detail.assert_not_called()
        flow.read_story.assert_not_called()
        flow.swipe.assert_not_called()
        self.assertEqual(flow.report['read'],0)
        self.assertEqual(flow.report['level_boundaries'][0]['reason'],'remaining_below_memory_level')

    def test_last_eligible_member_is_opened_before_the_lower_tail(self):
        flow=self.flow(stars='3')
        flow.confirmed_list_level=Mock(side_effect=[50,49])
        with self.cards([[(344,258)],[(467,258)],[(467,258)]]):
            flow.scan_story_members(True,50)
        flow.open_member_detail.assert_called_once_with((344,258))
        self.assertEqual(len(flow.report['members']),1)
        self.assertEqual(flow.report['level_boundaries'][0]['level'],49)

    def test_unknown_level_is_opened_and_does_not_hide_later_eligible_card(self):
        flow=self.flow(stars='3')
        flow.confirmed_list_level=Mock(side_effect=[None,50])
        with self.cards([[(344,258)],[(467,258)],[]]):
            flow.scan_story_members(True,50)
        self.assertEqual(flow.open_member_detail.call_count,2)
        self.assertNotIn('level_boundaries',flow.report)

    def test_no_cutoff_does_not_read_list_levels(self):
        flow=self.flow(practice=True)
        flow.confirmed_list_level=Mock(side_effect=AssertionError('practice candidates must not be excluded'))
        with self.cards([[(344,258)],[]]):
            flow.scan_story_members(True)
        flow.read_story.assert_called_once()

    def test_unconfirmed_sort_cannot_finish(self):
        flow=self.flow(stars='4')
        flow.ensure_level_descending.side_effect=FlowError('未确认等级降序')
        with self.assertRaises(FlowError):
            flow.run()
        self.assertEqual(flow.report['status'],'running')
        flow.open_member_detail.assert_not_called()

    def test_two_agreeing_levels_and_same_portrait_are_required(self):
        for levels,expected in (([1,1],1),([1,50],None),([1,None],None),([None],None)):
            flow=self.flow()
            flow.level_sort_is_descending=Mock(return_value=True)
            flow.list_member_level=Mock(side_effect=levels)
            with self.cards([[(344,258)]]):
                self.assertEqual(flow.confirmed_list_level((344,258),(344,258)),expected)

    def test_sort_change_between_level_reads_stops_instead_of_excluding(self):
        flow=self.flow()
        flow.level_sort_is_descending=Mock(side_effect=[True,False])
        flow.list_member_level=Mock(return_value=1)
        with self.assertRaisesRegex(FlowError,'排序已变化'):
            flow.confirmed_list_level((344,258),(344,258))

    def test_moved_missing_or_duplicated_card_stops_level_decision(self):
        for page in ([],[(344,381)],[(344,258),(467,258)]):
            flow=self.flow()
            flow.level_sort_is_descending=Mock(return_value=True)
            flow.list_member_level=Mock(return_value=1)
            with patch('mining_stories.member_cards',return_value=page), patch(
                    'mining_stories.member_signature',return_value='same'),patch(
                    'mining_stories.same_member_portrait',return_value=True):
                with self.assertRaisesRegex(FlowError,'成员列表已变化'):
                    flow.confirmed_list_level((344,258),'same')

    def test_ocr_requires_two_confident_consistent_reads(self):
        for number,footer,score,expected in (('1','Lv.1',.99,1),('1','Lv.10',.99,None),
                                            ('50','Lv.50',.7,None),('1','1',.99,None)):
            flow=self.flow()
            flow.ocr=Mock(side_effect=[[SimpleNamespace(text=number,score=score)],
                                       [SimpleNamespace(text=footer,score=score)]])
            self.assertEqual(flow.list_member_level((344,258)),expected)
            self.assertTrue(all(call.kwargs['only_rec'] for call in flow.ocr.call_args_list))

    def test_finish_explains_low_level_memories_remain_locked(self):
        with patch('task_logging.log') as output:
            finish('挖矿故事',{'status':'finished','read':0,'level_boundaries':[{}]})
        self.assertIn('当前等级可处理',output.call_args.args[0])
        self.assertIn('仍有等级不足的回忆未解锁',output.call_args.args[0])

    def test_top_check_does_not_swipe_when_top_or_empty_is_confirmed(self):
        for empty in (True,False):
            flow=self.flow()
            del flow.ensure_member_list_top
            with patch('mining_stories.member_cards',return_value=[] if empty else [(344,258)]),patch(
                    'mining_stories.member_list_at_top',return_value=True):
                flow.ensure_member_list_top()
            flow.swipe.assert_not_called()

    def test_top_check_stops_swiping_on_observed_success(self):
        flow=self.flow()
        del flow.ensure_member_list_top
        with patch('mining_stories.member_cards',return_value=[(344,258)]),patch(
                'mining_stories.member_list_at_top',side_effect=[False,False,True]):
            flow.ensure_member_list_top()
        self.assertEqual(flow.swipe.call_count,2)

    def test_unknown_or_unreachable_top_does_not_claim_completion(self):
        for at_top,attempts in ((None,0),(False,20)):
            flow=self.flow()
            del flow.ensure_member_list_top
            with patch('mining_stories.member_cards',return_value=[(344,258)]),patch(
                    'mining_stories.member_list_at_top',return_value=at_top):
                with self.assertRaises(FlowError):
                    flow.ensure_member_list_top()
            self.assertEqual(flow.swipe.call_count,attempts)

    def test_low_level_boundary_with_unknown_earlier_pixels_cannot_finish(self):
        flow=self.flow(stars='4')
        flow.confirmed_list_level=Mock(return_value=1)
        with self.cards([[(344,258)],[(344,258)]]),patch(
                'mining_stories.boundary_prefix_accounted',return_value=False):
            with self.assertRaisesRegex(FlowError,'未核对区域'):
                flow.scan_story_members(True,60)
        self.assertNotIn('level_boundaries',flow.report)

    def test_ignored_scroll_is_retried_until_new_member_appears(self):
        flow=self.flow()
        with self.cards([[(344,258)],[(344,258)],[(467,258)]]),patch(
                'mining_stories.member_list_at_bottom',return_value=False):
            self.assertFalse(flow.advance_story_list([(344,258)]))
        self.assertEqual(flow.swipe.call_count,2)

    def test_verified_bottom_requires_all_cards_seen_and_full_coverage(self):
        flow=self.flow()
        with self.cards([[(344,258)],[(344,258)]]),patch(
                'mining_stories.member_list_at_bottom',return_value=True):
            self.assertTrue(flow.advance_story_list([(344,258)]))
        flow.swipe.assert_not_called()
        self.assertEqual(flow.wait_member_list.call_count,2)

    def test_scroll_retry_exhaustion_or_unknown_bottom_does_not_finish(self):
        for bottom in (False,None):
            flow=self.flow()
            with self.cards([[((344,258))]]*4),patch(
                    'mining_stories.member_list_at_bottom',return_value=bottom):
                with self.assertRaisesRegex(FlowError,'未确认后续成员'):
                    flow.advance_story_list([(344,258)])
            self.assertEqual(flow.swipe.call_count,3)

    def test_unaccounted_area_at_bottom_is_not_a_completed_scan(self):
        flow=self.flow()
        with self.cards([[(344,258)]]),patch('mining_stories.member_list_at_bottom',return_value=True),patch(
                'mining_stories.boundary_prefix_accounted',return_value=False):
            with self.assertRaisesRegex(FlowError,'未核对'):
                flow.advance_story_list([(344,258)])
        flow.swipe.assert_not_called()


if __name__=='__main__':
    unittest.main()
