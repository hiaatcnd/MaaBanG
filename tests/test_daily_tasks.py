import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from daily_policy import integer, remaining_draws, verify_exchange, verify_free_confirmation, EXCHANGE_CATEGORIES
from daily_tasks import DailyFlow
from costume_unlock import CostumeFlow, FlowError


class DailyPolicyTests(unittest.TestCase):
    def test_only_three_free_draws_and_strict_remaining(self):
        for text,value in [('剩余3回',3),('剩余 ２ 次',2),('剩余0回',0)]:
            self.assertEqual(remaining_draws(text),value)
        for text in ['剩余10回','免费','3','剩余-1回','剩余2回 250']:
            with self.assertRaises(ValueError): remaining_draws(text)

    def test_paid_or_unrelated_recruitment_is_rejected(self):
        verify_free_confirmation('每日3次免费！演出招募\n进行招募。确认吗？')
        for text in ['每天免费10连','演出招募 250星石','每日3次免费！演出招募 消耗250星石']:
            with self.assertRaises(ValueError): verify_free_confirmation(text)

    def test_exchange_guards_category_quantity_and_balance(self):
        for category in EXCHANGE_CATEGORIES:
            verify_exchange(category,1,100,20,80)
        for args in [('券',1,100,20,80),('背景',2,100,20,80),('背景',1,70,-10,80),
                     ('背景',1,100,21,80),('背景',1,100,100,0)]:
            with self.assertRaises(ValueError): verify_exchange(*args)
        for text in ['80星石','-1','8O','1/2']:
            with self.assertRaises(ValueError): integer(text)


class DailyFlowTests(unittest.TestCase):
    def flow(self):
        f=DailyFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)))
        f.home=Mock()
        f.tap=Mock()
        f.wait=Mock()
        f.select_free_recruit=Mock()
        f.hit_text=Mock(return_value=True)
        f.text=Mock(return_value='每日3次免费！演出招募')
        return f

    def test_exhausted_free_pool_never_clicks_recruit(self):
        f=self.flow(); f.free_remaining=Mock(return_value=0)
        f.recruit()
        f.tap.assert_not_called()
        f.home.assert_called_once()

    def test_remaining_counts_control_exact_number_of_submissions(self):
        f=self.flow(); f.free_remaining=Mock(side_effect=[2,1,1,0,0]); f.finish_draw=Mock()
        f.recruit()
        self.assertEqual(f.report['draws'],2)
        self.assertEqual(f.finish_draw.call_count,2)
        self.assertEqual([c.args for c in f.tap.call_args_list].count((770,476)),2)
        f.home.assert_called_once()

    def test_uncertain_recruit_result_does_not_retry(self):
        f=self.flow(); f.free_remaining=Mock(return_value=2)
        f.finish_draw=Mock(side_effect=FlowError('result unknown'))
        with self.assertRaises(FlowError): f.recruit()
        self.assertEqual([c.args for c in f.tap.call_args_list].count((770,476)),1)
        self.assertTrue(f.report['draw_in_flight'])
        f.home.assert_not_called()

    def test_remaining_count_must_decrease(self):
        f=self.flow(); f.free_remaining=Mock(return_value=2); f.finish_draw=Mock()
        with self.assertRaises(FlowError): f.recruit()
        self.assertEqual(f.report['draws'],0)
        self.assertEqual([c.args for c in f.tap.call_args_list].count((770,476)),1)

    def test_exchange_scans_every_category_to_stationary_bottom(self):
        f=self.flow(); f.open_exchange=Mock(); f.select_exchange_category=Mock()
        f.ocr=Mock(return_value=[]); f.swipe=Mock(); f.image=np.zeros((720,1280,3),dtype=np.uint8)
        f.exchange()
        self.assertEqual([c.args[0] for c in f.select_exchange_category.call_args_list],list(EXCHANGE_CATEGORIES))
        self.assertEqual(f.swipe.call_count,5)
        f.home.assert_called_once()

    def test_exchange_insufficient_balance_stops_without_next_category(self):
        f=self.flow(); f.open_exchange=Mock(); f.select_exchange_category=Mock()
        f.ocr=Mock(return_value=[SimpleNamespace(box=[250,492,42,25])])
        f.exchange_item=Mock(return_value=False)
        f.exchange()
        self.assertEqual(f.report['status'],'insufficient_stickers')
        f.select_exchange_category.assert_called_once_with('成员')
        f.home.assert_called_once()

    def test_costume_rewards_collected_once_after_batch(self):
        f=CostumeFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),3)
        f.costume_character='A'; f.return_from_costumes=Mock(); f.collect_after_unlock=Mock()
        row={'before':1,'after':3}
        f.finish_character('A',row)
        f.return_from_costumes.assert_called_once_with('A')
        f.collect_after_unlock.assert_called_once_with('A')
        self.assertTrue(row['rewards_collected'])

    def test_costume_no_new_unlock_does_not_revisit_rewards(self):
        f=CostumeFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),3)
        f.collect_after_unlock=Mock()
        f.finish_character('A',{'before':3,'after':3})
        f.collect_after_unlock.assert_not_called()

    def test_recruit_animation_and_modal_close_before_result(self):
        f=self.flow()
        scenes=iter(['skip','member','dimmed_result','items','result'])
        current={}
        def snap():
            current['scene']=next(scenes)
            f.image=np.full((720,1280,3), 100 if current['scene']=='dimmed_result' else 255,dtype=np.uint8)
        def reco(node):
            return node in {
                'skip':{'DY_RecruitSkip'}, 'member':{'DY_MemberReveal'},
                'dimmed_result':{'DY_RecruitResult'},
                'items':{'DY_ObtainedHeader','DY_RecruitResult'},
                'result':{'DY_RecruitResult'},
            }[current['scene']]
        f.snap=Mock(side_effect=snap); f.reco=Mock(side_effect=reco); f.click=Mock()
        with patch('daily_tasks.time.sleep'):
            f.finish_draw()
        f.click.assert_called_once_with('DY_RecruitSkip')
        self.assertEqual([c.args for c in f.tap.call_args_list],[(985,570),(640,602),(1067,647)])
        f.wait.assert_called_once_with('DY_FreeBanner',25)

    def test_gifts_claim_until_empty_then_return_home(self):
        f=self.flow(); f.click=Mock(); f.tap_hit=Mock()
        f.reco=Mock(side_effect=[False,False,True])
        f.gifts()
        self.assertEqual(f.report['claims'],['gifts','gifts'])
        self.assertEqual(f.tap_hit.call_count,2)
        self.assertEqual(f.home.call_count,2)
        self.assertEqual(f.report['status'],'finished')

    def test_exchange_unconfirmed_submission_is_not_retried(self):
        f=self.flow(); f.tap_hit=Mock(); f.reco=Mock(return_value=True)
        f.text=Mock(return_value='日菜的房间')
        f.stable_integer=Mock(side_effect=[80,100,1,100,20,1,100,20])
        def wait(node,*args):
            if node=='DY_ExchangeSuccess': raise FlowError('confirmation lost')
        f.wait=Mock(side_effect=wait)
        with self.assertRaises(FlowError):
            f.exchange_item('背景',SimpleNamespace(box=[980,490,48,24]))
        self.assertEqual(f.report['exchanges'][0]['status'],'submitted')
        self.assertEqual([c.args for c in f.tap.call_args_list],[(770,582),(770,582)])
        f.home.assert_not_called()


if __name__=='__main__': unittest.main()
