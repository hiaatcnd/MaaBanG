"""Connection-dialog recovery from the captured failure, without device input."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions
from costume_unlock import FlowError
from online_live import OnlineLiveFlow


def failure_frame():
    from PIL import Image
    return np.asarray(Image.open(ROOT/'tests/fixtures/online_recovery/connection_failed.png')
                      .convert('RGB'))[:,:,::-1].copy()


def hit(text,x,w=70):
    return SimpleNamespace(text=text,box=[x,510,w,35])


class OnlineRecoveryTests(unittest.TestCase):
    def flow(self,folder):
        flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                            ChartOptions.parse({'mode':'coop','fire':0}),folder)
        flow.image=failure_frame()
        flow.foreground_package=Mock(return_value='com.bilibili.star.bili')
        flow.current_attempt={}
        flow.snap=Mock()
        flow.hit_text=Mock(return_value=None)
        flow.ocr=Mock(return_value=[hit('取消',477),hit('重试',733)])
        flow.reco=Mock(side_effect=lambda name:name=='CU_HomeBand' or
                       (name=='OL_Disconnected' and flow.image.any()))
        flow.tap_hit=Mock(side_effect=lambda _:flow.image.fill(0))
        flow.back=Mock();flow.background_room=Mock()
        flow.pause=Mock()
        return flow

    def test_cancel_full_footer_then_confirm_uncovered_home(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.recover_room()
            self.assertEqual(flow.snap.call_count,2)
            self.assertEqual(flow.tap_hit.call_args.args[0].text,'取消')
            self.assertEqual(flow.report['completed_rounds'],0)
            flow.back.assert_not_called();flow.background_room.assert_not_called()

    def test_ambiguous_or_unknown_dialog_never_uses_background_home(self):
        cases=([hit('取消',477)], [hit('重试',733)],
               [hit('取消',477),hit('重试',733),hit('购买',600)],
               [hit('取',477),hit('重试',733)],
               [hit('取消',733),hit('重试',477)],
               [hit('取消',260),hit('重试',733)],
               [hit('取消',477),hit('取消',500),hit('重试',733)])
        for buttons in cases:
            with self.subTest(buttons=[b.text for b in buttons]),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder);flow.ocr.return_value=buttons
                now=[0.]
                flow.pause.side_effect=lambda _:now.__setitem__(0,now[0]+10)
                with patch('online_live.time.monotonic',side_effect=lambda:now[0]):
                    with self.assertRaisesRegex(FlowError,'安全入口'):
                        flow.recover_room()
                flow.tap_hit.assert_not_called();flow.back.assert_not_called()
                flow.background_room.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder);flow.reco.side_effect=lambda name:name=='CU_HomeBand'
            now=[0.];flow.pause.side_effect=lambda _:now.__setitem__(0,now[0]+10)
            with patch('online_live.time.monotonic',side_effect=lambda:now[0]):
                with self.assertRaisesRegex(FlowError,'安全入口'):flow.recover_room()
            flow.tap_hit.assert_not_called();flow.back.assert_not_called()

    def test_stuck_connection_dialog_stops_after_three_clicks(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder);flow.tap_hit.side_effect=None
            with self.assertRaisesRegex(FlowError,'未消失'):flow.recover_room()
            self.assertEqual(flow.tap_hit.call_count,3)

    def test_recorded_dialog_with_production_ocr(self):
        bundle=Path(os.environ.get('MAABANG_TEST_RESOURCE_DIR',ROOT/'assets/resource'))
        if not (bundle/'model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        frame=failure_frame()
        class Controller(CustomController):
            def connect(self):return True
            def request_uuid(self):return 'connection-failure-replay'
            def screencap(self):return frame
        resource=Resource();self.assertTrue(resource.post_bundle(bundle).wait().succeeded)
        controller=Controller();self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker();tasker.bind(resource,controller)
        case=self;observed={}
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        flow=OnlineLiveFlow(context,ChartOptions.parse({'mode':'coop','fire':0}),folder)
                        flow.image=frame.copy();flow.snap=Mock()
                        flow.foreground_package=Mock(return_value='com.bilibili.star.bili')
                        case.assertTrue(flow.reco('OL_Disconnected'))
                        case.assertTrue(flow.reco('CU_HomeBand'))
                        real_reco=flow.reco
                        flow.reco=Mock(side_effect=lambda name,**kwargs:real_reco(name,**kwargs) if flow.image.any()
                                       else name=='CU_HomeBand')
                        flow.tap_hit=Mock(side_effect=lambda _:flow.image.fill(0))
                        flow.back=Mock();flow.background_room=Mock()
                        flow.recover_room()
                        case.assertEqual(flow.tap_hit.call_count,1)
                        case.assertEqual(flow.tap_hit.call_args.args[0].text,'取消')
                        case.assertEqual(flow.snap.call_count,2)
                        flow.back.assert_not_called();flow.background_room.assert_not_called()
                        return True
                    except Exception:
                        import traceback
                        observed['error']=traceback.format_exc()
                        return False
            resource.register_custom_action('ConnectionRecoveryReplay',Check())
            job=tasker.post_task('ConnectionRecoveryReplay',{'ConnectionRecoveryReplay':{
                'action':'Custom','custom_action':'ConnectionRecoveryReplay','pre_delay':0,'post_delay':0}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__':unittest.main()
