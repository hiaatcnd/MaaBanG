import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from daily_policy import (integer, remaining_draws, verify_exchange, verify_free_confirmation,
                          EXCHANGE_CATEGORIES, EXCHANGE_OPTION_NODES, selected_exchange_categories)
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
    def test_empty_invitation_and_locked_missions_are_skipped_without_clicks(self):
        import re
        for category,text,reason in [('邀请邦友','输入邀请码','invitation_not_linked'),
                                     ('邀请邦友','创建邀请码','invitation_not_linked'),
                                     ('EX任务','此任务已被锁定','locked')]:
            f=self.flow();f.tap_hit=Mock()
            f.hit_text=Mock(side_effect=lambda roi,pattern:bool(re.search(pattern,text)))
            f.claim_mission_category(category)
            self.assertEqual(f.report['skipped_missions'],[{'category':category,'reason':reason}])
            f.tap_hit.assert_not_called()

    def test_unknown_missing_mission_button_still_fails(self):
        f=self.flow();f.hit_text=Mock(return_value=None);f.tap_hit=Mock()
        with self.assertRaisesRegex(FlowError,'没有已支持的领取按钮'):
            f.claim_mission_category('EX任务')
        f.tap_hit.assert_not_called()

    def flow(self):
        f=DailyFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False),
                                   get_node_data=lambda node: {'attach': {'enabled': True}}))
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

    def test_exchange_empty_top_finishes_each_category_without_scrolling(self):
        f=self.flow(); f.open_exchange=Mock(); f.select_exchange_category=Mock()
        f.ocr=Mock(return_value=[]); f.swipe=Mock(); f.image=np.zeros((720,1280,3),dtype=np.uint8)
        f.exchange()
        self.assertEqual([c.args[0] for c in f.select_exchange_category.call_args_list],list(EXCHANGE_CATEGORIES))
        f.swipe.assert_not_called()
        self.assertEqual(f.ocr.call_count,5)
        f.home.assert_called_once()

    def test_exchange_rechecks_top_after_each_purchase_then_stops(self):
        f=self.flow(); f.open_exchange=Mock(); f.select_exchange_category=Mock()
        first=SimpleNamespace(box=[250,492,42,25])
        second=SimpleNamespace(box=[490,492,42,25])
        f.ocr=Mock(side_effect=[[second,first],[second],[]])
        f.exchange_item=Mock(return_value=True); f.swipe=Mock()
        f.exchange(['背景'])
        self.assertEqual([c.args for c in f.exchange_item.call_args_list],
                         [('背景',first),('背景',second)])
        f.swipe.assert_not_called()
        self.assertEqual(f.report['status'],'finished')
        f.home.assert_called_once()

    def test_exchange_insufficient_balance_stops_without_next_category(self):
        f=self.flow(); f.open_exchange=Mock(); f.select_exchange_category=Mock()
        f.ocr=Mock(return_value=[SimpleNamespace(box=[250,492,42,25])])
        f.exchange_item=Mock(return_value=False)
        f.exchange()
        self.assertEqual(f.report['status'],'insufficient_stickers')
        f.select_exchange_category.assert_called_once_with('成员')
        f.home.assert_called_once()

    def test_exchange_only_visits_selected_categories(self):
        f=self.flow(); f.open_exchange=Mock(); f.select_exchange_category=Mock()
        f.ctx.get_node_data=lambda node: {'attach': {'enabled': node in (
            EXCHANGE_OPTION_NODES['表情'], EXCHANGE_OPTION_NODES['背景'])}}
        f.ocr=Mock(return_value=[]); f.swipe=Mock(); f.image=np.zeros((720,1280,3),dtype=np.uint8)
        f.exchange()
        self.assertEqual([c.args[0] for c in f.select_exchange_category.call_args_list],['表情','背景'])
        self.assertEqual(f.report['categories'],['表情','背景'])

    def test_no_categories_returns_home_without_opening_exchange(self):
        f=self.flow(); f.ctx.get_node_data=lambda node: {'attach': {'enabled': False}}
        f.open_exchange=Mock()
        f.exchange()
        f.open_exchange.assert_not_called(); f.home.assert_called_once(); f.tap.assert_not_called()
        self.assertEqual(f.report['status'],'no_categories_selected')

    def test_invalid_category_configuration_does_not_open_shop(self):
        for data in (None, {}, {'attach': {'enabled': 'true'}}):
            f=self.flow(); f.ctx.get_node_data=lambda node: data; f.open_exchange=Mock()
            with self.assertRaises(ValueError): f.exchange()
            f.open_exchange.assert_not_called()

    def test_ui_checkbox_combinations_preserve_every_selection(self):
        import itertools
        import json
        from copy import deepcopy
        root=Path(__file__).resolve().parents[1]
        interface=json.loads((root/'assets/interface.json').read_text(encoding='utf-8'))
        pipeline=json.loads((root/'assets/resource/pipeline/daily.json').read_text(encoding='utf-8'))
        option=interface['option']['贴纸交换分类']
        self.assertEqual(option['default_case'],list(EXCHANGE_CATEGORIES))
        for flags in itertools.product((False,True),repeat=5):
            selected=[c for c,enabled in zip(EXCHANGE_CATEGORIES,flags) if enabled]
            nodes=deepcopy(pipeline)
            for case in option['cases']:
                if case['name'] in selected:
                    for name,override in case['pipeline_override'].items():
                        nodes[name].update(override)
            self.assertEqual(selected_exchange_categories(SimpleNamespace(get_node_data=nodes.get)),selected)

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
        scenes=iter(['skip','member','dimmed_result','items','result','banner'])
        current={}
        def snap():
            current['scene']=next(scenes)
            f.image=np.full((720,1280,3), 100 if current['scene']=='dimmed_result' else 255,dtype=np.uint8)
        def reco(node):
            matched = node in {
                'skip':{'DY_RecruitSkip'}, 'member':{'DY_MemberReveal'},
                'dimmed_result':{'DY_RecruitResult'},
                'items':{'DY_ObtainedHeader','DY_RecruitResult'},
                'result':{'DY_RecruitResult'},
                'banner':{'DY_FreeBanner'},
            }[current['scene']]
            return SimpleNamespace(box=[1177,34,48,48]) if matched else None
        f.snap=Mock(side_effect=snap); f.reco=Mock(side_effect=reco); f.click=Mock()
        with patch('daily_tasks.time.sleep'):
            f.finish_draw()
        f.click.assert_not_called()
        self.assertEqual([c.args for c in f.tap.call_args_list],[(1201,58),(985,570),(640,602),(1067,647)])
        f.wait.assert_not_called()

    def test_costume_notice_is_closed_before_recruit_result_without_resubmission(self):
        from PIL import Image
        f=self.flow(); state={'scene':'costume'}
        costume=np.array(Image.open(Path(__file__).parent/'fixtures/notifications/costume.png').convert('RGB'))[:,:,::-1].copy()
        def snap():
            f.image=costume.copy() if state['scene']=='costume' else np.full((720,1280,3),255,dtype=np.uint8)
        f.snap=Mock(side_effect=snap)
        f.ocr=Mock(return_value=[SimpleNamespace(text='获得服装',box=[400,140,130,35]),
                                SimpleNamespace(text='确定',box=[600,530,80,35])])
        f.reco=Mock(side_effect=lambda node:node=={'costume':'DY_RecruitResult',
                  'result':'DY_RecruitResult','banner':'DY_FreeBanner'}[state['scene']])
        def tap(x,y):
            state['scene']='result' if state['scene']=='costume' else 'banner'
        f.tap=Mock(side_effect=tap)
        with patch('daily_tasks.time.sleep'):
            f.finish_draw()
        self.assertEqual([c.args for c in f.tap.call_args_list],[(640,547),(1067,647)])
        f.free_remaining=Mock()
        self.assertNotIn((770,476),[c.args for c in f.tap.call_args_list])

    def recruit_scenes(self, scenes):
        f=self.flow()
        frames=iter(scenes)
        current={}
        def snap():
            current['node']=next(frames)
            f.image=np.full((720,1280,3),255,dtype=np.uint8)
        f.snap=Mock(side_effect=snap)
        f.reco=Mock(side_effect=lambda node: SimpleNamespace(box=[470,600,340,48])
                    if node==current['node'] else None)
        return f

    def test_obscured_skip_uses_cut_prompt_and_retries_missed_click(self):
        f=self.recruit_scenes(['DY_RecruitCut','DY_RecruitCut',
                               'DY_RecruitResult','DY_FreeBanner'])
        with patch('daily_tasks.time.monotonic',side_effect=range(0,100,4)):
            f.finish_draw()
        self.assertEqual([c.args for c in f.tap.call_args_list],
                         [(640,624),(640,624),(1067,647)])

    def test_result_return_is_retried_after_missed_click(self):
        f=self.recruit_scenes(['DY_RecruitResult','DY_RecruitResult','DY_FreeBanner'])
        with patch('daily_tasks.time.monotonic',side_effect=range(0,100,4)):
            f.finish_draw()
        self.assertEqual([c.args for c in f.tap.call_args_list],[(1067,647)]*2)

    def test_stuck_navigation_stops_after_three_attempts_without_resubmitting(self):
        for node in ('DY_RecruitCut','DY_RecruitSkip','DY_MemberReveal',
                     'DY_ObtainedHeader','DY_RecruitResult'):
            with self.subTest(node=node):
                f=self.recruit_scenes([node]*4)
                f.free_remaining=Mock(return_value=3)
                with patch('daily_tasks.time.monotonic',side_effect=range(0,100,4)):
                    with self.assertRaisesRegex(FlowError,'点击 3 次'):
                        f.recruit()
                self.assertEqual(f.tap.call_count,5)  # Open, submit, three navigation attempts.
                self.assertEqual([c.args for c in f.tap.call_args_list].count((770,476)),1)
                self.assertTrue(f.report['draw_in_flight'])
                self.assertEqual(f.report['draws'],0)

    def test_navigation_retry_waits_before_clicking_again(self):
        f=self.recruit_scenes(['DY_RecruitCut','DY_RecruitCut',
                               'DY_RecruitResult','DY_FreeBanner'])
        with patch('daily_tasks.time.monotonic',side_effect=[0,1,1,2,2,3,3,4]), \
             patch('daily_tasks.time.sleep'):
            f.finish_draw()
        self.assertEqual([c.args for c in f.tap.call_args_list],[(640,624),(1067,647)])

    def test_unrecognized_or_premature_banner_times_out_without_clicking(self):
        for node in ('unknown','DY_FreeBanner'):
            with self.subTest(node=node):
                f=self.recruit_scenes([node])
                with patch('daily_tasks.time.monotonic',side_effect=[0,1,121]), \
                     patch('daily_tasks.time.sleep'):
                    with self.assertRaisesRegex(FlowError,'招募结果未确认'):
                        f.finish_draw()
                f.tap.assert_not_called()

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
