"""Replay the production story-prompt failure with real Maa OCR and no device."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))


class NotificationOCRTests(unittest.TestCase):
    def test_story_prompts_defer_and_resume_online_settlement(self):
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

        def frame(name):
            return np.array(Image.open(ROOT/'tests/fixtures'/name).convert('RGB'))[:,:,::-1].copy()
        stories=[frame(f'notifications/{name}.png') for name in ('song_story','band_story')]
        result=frame('notifications/event_result.png')
        room=frame('versus/room_event.png')
        current=[stories[0]]
        class Screens(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'story-notification-replay'
            def screencap(self): return current[0]

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
                        for story in stories:
                            flows=[DailyFlow(context)]+[OnlineLiveFlow(context,
                                ChartOptions.parse({'mode':mode,'fire':0}),folder)
                                for mode in ('coop','versus')]
                            for flow in flows:
                                flow.image=story.copy()
                                flow.tap=Mock(side_effect=AssertionError('No device input during replay'))
                                flow.tap_hit=Mock()
                                flow.snap=Mock(side_effect=lambda:setattr(flow,'image',np.zeros_like(story)))
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
                        # Replay the outer settlement loop: dismiss the actual
                        # failed dialog, confirm the result, then stop at rooms.
                        for story in stories:
                            current[0]=story
                            flow=OnlineLiveFlow(context,ChartOptions.parse({'mode':'versus','fire':0}),folder)
                            flow.report['completed_rounds']=1
                            row={'counted':True,'status':'result_confirmed'}
                            taps=[]
                            def advance(x,y):
                                if not taps:
                                    case.assertTrue(390<x<628 and 590<y<660,(x,y))
                                    current[0]=result
                                else:
                                    case.assertEqual(len(taps),1,'Must stop at room selection')
                                    case.assertTrue(940<x<1230 and 600<y<705,(x,y))
                                    current[0]=room
                                taps.append((x,y))
                            flow.tap=Mock(side_effect=advance)
                            flow.pause=Mock(side_effect=AssertionError('Settlement did not advance'))
                            with patch('notifications.time.sleep'):
                                flow.settle_online(row)
                            case.assertEqual(len(taps),2)
                            case.assertEqual(flow.report['completed_rounds'],1)
                            case.assertEqual(row,{'counted':True,'status':'result_confirmed'})
                        return True
                    except Exception as error:
                        observed['error']=repr(error)
                        return False
            resource.register_custom_action('NotificationReplay',Check())
            job=tasker.post_task('NotificationReplay',{'NotificationReplay':{
                'action':'Custom','custom_action':'NotificationReplay'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__': unittest.main()
