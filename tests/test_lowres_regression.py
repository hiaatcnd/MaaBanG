"""Regression cases from the native 1280x720 test account."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from daily_tasks import DailyFlow
from costume_unlock import FlowError
from mining_stories import member_filter_box
import test_song_filters
import test_costume_flow


class LowResolutionTests(unittest.TestCase):
    def flow(self):
        f=DailyFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)))
        f.tap=Mock();f.snap=Mock();f.dismiss_notifications=Mock(return_value=False)
        f.require_clear_notification_overlay=Mock()
        f.image=np.full((720,1280,3),255,dtype=np.uint8)
        return f

    def test_ten_distinct_members_do_not_share_three_attempt_budget(self):
        f=self.flow();state={'index':0}
        f.snap=lambda:state.update(index=state['index']+1)
        f.text=lambda _:f'成员{state["index"]}'
        def reco(name):
            node='DY_FullscreenMemberReveal' if state['index']<=10 else (
                'DY_RecruitResult' if state['index']==11 else 'DY_RecruitDetails')
            return SimpleNamespace(box=[0,0,10,10]) if name==node else None
        f.reco=reco
        with patch('daily_tasks.time.monotonic',side_effect=range(0,500,4)):
            f.finish_draw('DY_RecruitDetails')
        self.assertEqual(f.tap.call_count,11)
        self.assertNotIn((770,476),[c.args for c in f.tap.call_args_list])

    def test_same_member_still_stops_after_three_attempts(self):
        f=self.flow();f.text=Mock(return_value='同一成员')
        f.reco=lambda n:n=='DY_FullscreenMemberReveal'
        with patch('daily_tasks.time.monotonic',side_effect=range(0,500,4)):
            with self.assertRaisesRegex(FlowError,'点击 3 次'):
                f.finish_draw()
        self.assertEqual(f.tap.call_count,3)

    def test_gift_slide_is_observed_before_submission(self):
        f=self.flow();f.home=Mock();f.click=Mock();f.wait=Mock();f.tap_hit=Mock()
        empty={'value':False}
        f.reco=lambda _:empty['value']
        f.hit_text=Mock(side_effect=[None,None,SimpleNamespace(box=[1020,120,100,30])])
        f.finish_claim=lambda _:empty.update(value=True)
        with patch('daily_tasks.time.sleep'):
            f.gifts()
        self.assertEqual(f.tap_hit.call_count,1)
        self.assertEqual(f.snap.call_count,2)

    def test_default_costume_wording_keeps_currency_checks(self):
        f=test_costume_flow.FlowTests().purchase_flow(True)
        f.text=lambda roi:'默认服装' if roi[1]==607 else '测试服装'
        with patch('costume_unlock.time.sleep'):
            self.assertEqual(f.unlock_one('牛込里美',65),66)
        self.assertEqual(f.report['purchases'][0]['status'],'success_confirmed')
        for value in ('配色1','特殊服装',''):
            f=test_costume_flow.FlowTests().purchase_flow(True)
            f.text=lambda roi:value
            with self.assertRaisesRegex(FlowError,'不是默认配色'):
                f.unlock_one('牛込里美',65)
            self.assertEqual(f.report['purchases'],[])

    def test_slider_stall_changes_gesture_and_never_excludes_target(self):
        f,c=test_song_filters.SongFilterTests().slider_flow()
        f.text=Mock(side_effect=['5','30','5','25','5','25','5','27','25','27'])
        with patch('song_navigation.level_slider_handles',side_effect=[[772,1150],[772,1088],[772,1088],[772,1115]]):
            f.filter_expert_level(26)
        steps=f.report['level_filter_steps']
        self.assertGreater(steps[2]['x'],steps[1]['x'])
        self.assertEqual(f.report['song_filters'],[{'verified_range':[25,27],'target_range':[26,26],'tolerance':1}])

    def test_filter_bounds_match_both_panel_widths(self):
        for x,y,w,h in ((129,34,1021,652),(205,75,870,570)):
            a=np.zeros((720,1280,3),dtype=np.uint8)
            a[y:y+h,x:x+w]=255
            self.assertEqual(member_filter_box(a),[x,y,w,h])
        with self.assertRaises(FlowError):
            member_filter_box(np.zeros((720,1280,3),dtype=np.uint8))

    def test_challenge_turns_off_inherited_auto_before_start(self):
        from mining_live import ChallengeMiningFlow
        from chart_policy import ChartSelection
        selection=ChartSelection.parse('3','expert')
        f=ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        f.wait_ready=Mock();f.pause=Mock();f.snap=Mock()
        f.text=Mock(side_effect=[selection.song['title'],'EXPERT'])
        state={'auto':True}
        f.reco=lambda name:state['auto'] if name=='LV_AutoOn' else not state['auto']
        f.tap=Mock(side_effect=lambda *_:state.update(auto=False))
        f.fire_preview=lambda:(30,30);f.fire_balance=lambda:30
        self.assertEqual(f.verify_chart_start(1,selection,0),30)
        f.tap.assert_called_once_with(883,580)

    def test_reset_drags_past_endpoints_but_requires_exact_ocr(self):
        f,c=test_song_filters.SongFilterTests().slider_flow()
        f.text=Mock(side_effect=['6','30','5','30'])
        f.set_song_level_range(5,30)
        self.assertEqual(c.post_touch_move.call_args.args[0],735)
        self.assertEqual(f.report['song_filters'],[{'verified_range':[5,30]}])

    def test_real_reveal_templates_reject_unrelated_screens(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        def read(name):
            return np.asarray(Image.open(ROOT/'tests/fixtures'/name).convert('RGB'))[:,:,::-1].copy()
        frames=[read('daily/'+name+'.png') for name in ('ThreeStarReveal','TwoStarReveal')]
        class Controller(CustomController):
            def connect(self):return True
            def request_uuid(self):return 'lowres-reveals'
            def screencap(self):return frames[0]
        r=Resource();self.assertTrue(r.post_bundle(ROOT/'assets/resource').wait().succeeded)
        c=Controller();self.assertTrue(c.post_connection().wait().succeeded)
        t=Tasker();t.bind(r,c);checks=[]
        class Check(CustomAction):
            def run(self,ctx,argv):
                f=DailyFlow(ctx)
                for frame in frames:
                    f.image=frame;checks.append(bool(f.reco('DY_FullscreenMemberReveal')))
                for name in ('coop/ready.png','coop/group_result.png','notifications/costume.png'):
                    f.image=read(name);checks.append(not bool(f.reco('DY_FullscreenMemberReveal')))
                f.image=read('lowres/member_filter.png')
                checks.append(bool(f.reco('MN_MemberFilter')))
                checks.append(member_filter_box(f.image)==[129,34,1021,652])
                f.image=read('lowres/story_reward.png')
                checks.append(bool(f.reco('MN_StoryReward')))
                from song_navigation import SongNavigationMixin
                from song_catalog import BY_ID
                f.image=read('lowres/challenge_ready.png')
                checks.append(SongNavigationMixin.title_matches(f,f.text([220,541,570,38]),BY_ID['3']))
                checks.append(not SongNavigationMixin.title_matches(f,f.text([220,541,600,38]),BY_ID['3']))
                return all(checks)
        r.register_custom_action('RevealCheck',Check())
        self.assertTrue(t.post_task('RevealCheck',{'RevealCheck':{'action':'Custom','custom_action':'RevealCheck'}}).wait().succeeded)
        self.assertEqual(checks,[True]*10)


if __name__=='__main__':unittest.main()
