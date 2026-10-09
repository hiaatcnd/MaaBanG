"""Replay the production story-prompt failure with real Maa OCR and no device."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))


class NotificationOCRTests(unittest.TestCase):
    def test_song_story_is_deferred_by_online_and_daily_flows(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        import numpy as np
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from chart_policy import ChartOptions
        from daily_tasks import DailyFlow
        from online_live import OnlineLiveFlow

        frame=np.array(Image.open(ROOT/'tests/fixtures/notifications/song_story.png').convert('RGB'))[:,:,::-1].copy()
        class Screens(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'story-notification-replay'
            def screencap(self): return frame

        controller=Screens()
        self.assertTrue(controller.post_connection().wait().succeeded)
        resource=Resource()
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        tasker=Tasker(); tasker.bind(resource,controller)
        case=self; observed={}
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        flows=[DailyFlow(context),OnlineLiveFlow(context,
                            ChartOptions.parse({'mode':'coop','fire':0}),folder)]
                        for flow in flows:
                            flow.image=frame.copy()
                            flow.tap=Mock(side_effect=AssertionError('No device input during replay'))
                            flow.tap_hit=Mock()
                            flow.snap=Mock(side_effect=lambda:setattr(flow,'image',np.zeros_like(frame)))
                            with patch('notifications.time.sleep'):
                                case.assertTrue(flow.dismiss_notifications())
                            flow.tap_hit.assert_called_once()
                            chosen=flow.tap_hit.call_args.args[0]
                            case.assertEqual(chosen.text,'稍后再读')
                            x,y,w,h=chosen.box
                            case.assertTrue(390 < x+w/2 < 628 and 590 < y+h/2 < 660)
                            flow.snap.assert_called_once()
                            flow.require_clear_notification_overlay()
                            flow.tap.assert_not_called()
                        return True
                    except Exception as error:
                        observed['error']=repr(error)
                        return False
            resource.register_custom_action('NotificationReplay',Check())
            job=tasker.post_task('NotificationReplay',{'NotificationReplay':{
                'action':'Custom','custom_action':'NotificationReplay'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__': unittest.main()
