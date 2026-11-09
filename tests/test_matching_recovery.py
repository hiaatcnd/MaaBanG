"""Matchmaking timeout recovery, including the November 9 failure screenshot."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions
from costume_unlock import FlowError
from notifications import dialog_box
from online_live import OnlineLiveFlow


def frame():
    return np.asarray(Image.open(ROOT/'tests/fixtures/online_recovery/matching_timeout.png')
                      .convert('RGB'))[:,:,::-1].copy()


def hit(text,box):
    return SimpleNamespace(text=text,box=box)


def matching_hits():
    return [hit('匹配中',[393,153,91,36]),
            hit('正在寻找房间。',[556,230,153,27]),
            hit('系统拥挤时可能会花费较长的时间。',[443,426,378,23]),
            hit('取消',[601,504,74,42])]


class MatchingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.now=0.
        clock=patch('online_live.time.monotonic',side_effect=lambda:self.now)
        clock.start();self.addCleanup(clock.stop)
        self.flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                                 ChartOptions.parse({'mode':'coop','fire':0}),self.folder.name)
        flow=self.flow
        flow.image=frame()
        flow.current_attempt={}
        flow.report['completed_rounds']=5
        flow.foreground_package=Mock(return_value='com.bilibili.star.bili')
        flow.snap=Mock()
        flow.ocr=Mock(side_effect=lambda _:matching_hits())
        flow.hit_text=Mock(return_value=None)
        flow.reco=Mock(side_effect=lambda name:name=='CU_HomeBand')
        flow.tap_hit=Mock()
        flow.back=Mock(side_effect=AssertionError('No background navigation'))
        flow.background_room=Mock(side_effect=AssertionError('No focus-loss for a modal'))
        flow.pause=Mock(side_effect=self.advance)

    def advance(self,seconds):
        self.now+=seconds

    def close(self,*args):
        self.flow.image.fill(0)

    def test_cancel_then_observe_uncovered_page_without_recount(self):
        self.flow.tap_hit.side_effect=self.close
        self.flow.recover_room()
        self.assertEqual(self.flow.snap.call_count,3)
        self.flow.tap_hit.assert_called_once()
        self.assertEqual(self.flow.tap_hit.call_args.args[0].text,'取消')
        self.assertEqual(self.flow.report['completed_rounds'],5)

    def test_missed_click_retries_only_after_wait_and_fresh_stable_target(self):
        taps=[]
        def tap(button):
            taps.append(self.now)
            if len(taps)==2:self.close()
        self.flow.tap_hit.side_effect=tap
        with patch('online_live.log') as log:
            self.flow.recover_room()
        self.assertEqual(len(taps),2)
        self.assertGreaterEqual(taps[1]-taps[0],3)
        self.assertTrue(any('补点取消' in call.args[0] for call in log.call_args_list))
        self.assertEqual(self.flow.current_attempt['matching_cancel_clicks'],2)

    def test_delayed_success_never_repeats_cancel(self):
        tapped=[]
        self.flow.tap_hit.side_effect=lambda _:tapped.append(self.now)
        def snap():
            if tapped and self.now-tapped[0]>=2.5:self.close()
        self.flow.snap.side_effect=snap
        self.flow.recover_room()
        self.flow.tap_hit.assert_called_once()

    def test_matching_finishes_before_first_click_does_not_use_stale_target(self):
        def snap():
            if self.flow.snap.call_count==2:self.close()
        self.flow.snap.side_effect=snap
        self.flow.recover_room()
        self.flow.tap_hit.assert_not_called()

    def test_room_header_behind_modal_is_not_a_confirmed_exit(self):
        at_home=[]
        self.flow.reco.side_effect=lambda name:(name=='CU_HomeBand' if at_home
                                                else name==self.flow.home_node)
        self.flow.tap_hit.side_effect=self.close
        self.flow.back.side_effect=lambda:at_home.append(True)
        self.flow.recover_room()
        self.flow.tap_hit.assert_called_once()
        self.flow.back.assert_called_once()
        self.assertEqual(self.flow.snap.call_count,4)

    def test_ineffective_cancel_stops_after_three_clicks_and_observation(self):
        with self.assertRaisesRegex(FlowError,'点击3次后弹窗仍未消失'):
            self.flow.recover_room()
        self.assertEqual(self.flow.tap_hit.call_count,3)
        self.assertGreaterEqual(self.now,9)
        self.assertLess(self.now,30)

    def test_user_stop_during_wait_never_retries(self):
        self.flow.tap_hit.side_effect=lambda _:setattr(self.flow.ctx.tasker,'stopping',True)
        with self.assertRaisesRegex(FlowError,'用户停止'):
            self.flow.recover_room()
        self.flow.tap_hit.assert_called_once()

    def test_new_dialog_after_cancel_never_replays_old_coordinates(self):
        def tap(_):
            self.flow.ocr.side_effect=lambda _:[hit('购买确认',[393,153,150,36]),
                                               hit('取消',[601,504,74,42])]
        self.flow.tap_hit.side_effect=tap
        with self.assertRaisesRegex(FlowError,'安全入口'):
            self.flow.recover_room()
        self.flow.tap_hit.assert_called_once()
        self.assertEqual(self.flow.report['completed_rounds'],5)

    def test_changed_target_requires_new_stable_observations(self):
        seen=[]
        def ocr(_):
            hits=matching_hits()
            if self.now>=.25:hits[-1].box[0]+=20
            return hits
        self.flow.ocr.side_effect=ocr
        self.flow.tap_hit.side_effect=lambda h:(seen.append((self.now,h.box[0])),self.close())
        self.flow.recover_room()
        self.assertEqual(seen,[(.5,621)])

    def test_ambiguous_or_transaction_dialog_has_no_safe_cancel(self):
        original=matching_hits()
        cases=[original[1:], original[:1]+original[2:],
               original+[hit('重试',[740,504,74,42])],
               original+[hit('消耗星石购买',[450,300,200,30])],
               original[:-1]+[hit('取消',[260,504,74,42])],
               original[:-1]+[hit('取消',[601,450,74,42])],
               original[:-1]+[hit('确认',[601,504,74,42])]]
        for hits in cases:
            with self.subTest(text=[h.text for h in hits]):
                self.flow.ocr=Mock(return_value=hits)
                self.assertIsNone(self.flow.matching_cancel_button(dialog_box(self.flow.image)))

    def test_production_ocr_recovers_recorded_matching_dialog(self):
        bundle=Path(os.environ.get('MAABANG_TEST_RESOURCE_DIR',ROOT/'assets/resource'))
        if not (bundle/'model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        screenshot=frame()
        class Controller(CustomController):
            def connect(self):return True
            def request_uuid(self):return 'matching-timeout-replay'
            def screencap(self):return screenshot
        resource=Resource();self.assertTrue(resource.post_bundle(bundle).wait().succeeded)
        controller=Controller();self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker();self.assertTrue(tasker.bind(resource,controller))
        case=self;errors=[]
        class Check(CustomAction):
            def run(self,context,argv):
                try:
                    flow=case.flow
                    flow.ctx=context
                    flow.reco=OnlineLiveFlow.reco.__get__(flow)
                    flow.ocr=OnlineLiveFlow.ocr.__get__(flow)
                    case.assertIsNotNone(dialog_box(flow.image))
                    case.assertTrue(flow.reco('OL_Matching'))
                    case.assertFalse(flow.reco('OL_Disconnected'))
                    real_reco=flow.reco
                    flow.reco=Mock(side_effect=lambda name,**kw:real_reco(name,**kw)
                                   if flow.image.any() else name=='CU_HomeBand')
                    flow.tap_hit.side_effect=case.close
                    flow.recover_room()
                    flow.tap_hit.assert_called_once()
                    case.assertEqual(flow.tap_hit.call_args.args[0].text,'取消')
                    case.assertEqual(flow.report['completed_rounds'],5)
                    return True
                except Exception:
                    import traceback
                    errors.append(traceback.format_exc())
                    return False
        resource.register_custom_action('MatchingReplay',Check())
        job=tasker.post_task('MatchingReplay',{'MatchingReplay':{
            'action':'Custom','custom_action':'MatchingReplay','pre_delay':0,'post_delay':0}}).wait()
        self.assertTrue(job.succeeded,'\n'.join(errors))


if __name__=='__main__':unittest.main()
