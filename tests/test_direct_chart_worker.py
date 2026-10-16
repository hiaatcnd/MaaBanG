"""Direct cooperative startup through mock controllers, with no device access."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_worker import play, start_authorized_stage
from playback_controller import PlaybackOptions


class DirectChartWorkerTests(unittest.TestCase):
    def test_direct_online_clicks_once_and_existing_online_does_not_click(self):
        for mode,clicks in [('click',1),('online_click',1),('online',0)]:
            with self.subTest(mode=mode):
                controller=Mock()
                controller.post_click.return_value.wait.return_value.succeeded=True
                start_authorized_stage(controller,mode)
                self.assertEqual(controller.post_click.call_count,clicks)
                if clicks:
                    controller.post_click.assert_called_once_with(1130,616)

    def test_failed_direct_online_click_is_not_retried(self):
        controller=Mock()
        controller.post_click.return_value.wait.return_value.succeeded=False
        with self.assertRaisesRegex(RuntimeError,'开演输入未确认，不重试'):
            start_authorized_stage(controller,'online_click')
        controller.post_click.assert_called_once_with(1130,616)

    def run_startup(self,folder,*,click_succeeds=True,click_raises=False,start_mode='online_click'):
        output=Path(folder)
        chart=[{'type':'BPM','beat':0,'bpm':120},
               {'type':'Single','beat':2,'lane':3,'skill':True}]
        raw=json.dumps(chart).encode('utf8')
        (output/'chart.json').write_bytes(raw)
        config={'controller':{},'chart':{'path':str(output/'chart.json'),
                'sha256':hashlib.sha256(raw).hexdigest(),'duration':2},
                'jitter':'small','seed':1,'start_mode':start_mode}
        config_path=output/'config.json'
        config_path.write_text(json.dumps(config),encoding='utf8')
        (output/'start').write_text('start')

        now=[0.]
        success=SimpleNamespace(wait=lambda:SimpleNamespace(succeeded=True))
        controller=Mock()
        observer=Mock()
        for item in (controller,observer):
            item.cached_image=np.zeros((720,1280,3),dtype=np.uint8)
            item.post_connection.return_value=success
            item.post_screencap.return_value=success
        controller.post_click.return_value=SimpleNamespace(
            wait=lambda:SimpleNamespace(succeeded=click_succeeds))
        if click_raises:
            controller.post_click.side_effect=RuntimeError('uncertain start submission')
        frames=[]
        for marker,elapsed in [(0,60.),(2,120.),(1,121.)]:
            frame=np.zeros((720,1280,3),dtype=np.uint8)
            frame[0,0,0]=marker
            if marker==2:
                frame[425:525,385:895]=255
            frames.append((elapsed,frame))
        captures=[0]
        def screencap():
            captures[0]+=1
            if captures[0]>1:  # The first capture is the connection smoke check.
                elapsed,frame=frames[captures[0]-2]
                # A stage-looking frame behind "waiting for members" must not
                # start the chart clock before the following real stage frame.
                self.assertFalse((output/'stage_started').exists())
                now[0]=elapsed
                controller.cached_image=frame
            return success
        controller.post_screencap.side_effect=screencap

        resource=Mock()
        resource.post_bundle.return_value=success
        recognized=[]
        def recognition(name,frame,*args):
            recognized.append(name)
            return SimpleNamespace(hit=True) if name=='OL_WaitingMembers' and frame[0,0,0]==2 else None
        context=SimpleNamespace(run_recognition=recognition,get_node_data=lambda name:None)
        tasker=Mock()
        def post_task(*args):
            action=resource.register_custom_action.call_args.args[1]
            return SimpleNamespace(wait=lambda:SimpleNamespace(succeeded=action.run(context,None)))
        tasker.post_task.side_effect=post_task
        lock=Mock(points=[])
        lock.observe.return_value={'crossing':1.}
        locate=Mock(return_value=500.)
        with patch('maa.controller.AdbController',side_effect=[controller,observer]), \
                patch('maa.resource.Resource',return_value=resource), \
                patch('maa.tasker.Tasker',return_value=tasker), \
                patch('maa.toolkit.Toolkit.init_option'), \
                patch('chart_worker.playback_options',return_value=PlaybackOptions(
                    'fake-adb','mock-device',{},4,'connection_explicit',4)), \
                patch('chart_worker.compile_chart',return_value=[]), \
                patch('chart_worker.time.perf_counter',side_effect=lambda:now[0]), \
                patch('chart_worker.threading.Thread'), \
                patch('chart_sync.FirstNoteLock',return_value=lock), \
                patch('chart_sync.stage_state',side_effect=lambda frame:None if frame[0,0,0]==0 else 'stage'), \
                patch('chart_sync.locate_note_y',locate):
            result=play(config_path)
        report=json.loads((output/'playback.json').read_text(encoding='utf8'))
        return result,report,controller,lock,locate,recognized

    def test_direct_online_waits_for_members_and_uses_online_skill_anchor(self):
        with tempfile.TemporaryDirectory() as folder:
            result,report,controller,lock,locate,recognized=self.run_startup(folder)
            self.assertEqual(result,0,report.get('error'))
            self.assertEqual(report['status'],'input_complete')
            # Skill timing differs online: retain the online-compatible tap
            # color even when the selected solo chart marks a skill note.
            self.assertEqual(report['anchor'],[1.,3,'skill'])
            self.assertEqual(locate.call_args.args[1:],(3,'skill'))
            locate.assert_called_once()
            lock.observe.assert_called_once_with(120.5,500.)
            self.assertEqual(len(report['start_trace']),1)
            self.assertEqual(report['start_trace'][0]['time'],120.5)
            self.assertIn('OL_WaitingMembers',recognized)
            self.assertTrue((Path(folder)/'stage_started').exists())
            controller.post_click.assert_called_once_with(1130,616)
            controller.post_touch_down.assert_not_called()

    def test_failed_direct_online_start_never_adds_solo_pause_click(self):
        with tempfile.TemporaryDirectory() as folder:
            result,report,controller,lock,locate,_=self.run_startup(folder,click_succeeds=False)
            self.assertEqual(result,1)
            self.assertEqual(report['status'],'error')
            self.assertIn('开演输入未确认，不重试',report['error'])
            controller.post_click.assert_called_once_with(1130,616)
            lock.observe.assert_not_called()
            locate.assert_not_called()

    def test_solo_start_failure_or_uncertain_submission_never_blindly_pauses(self):
        for raises in (False,True):
            with self.subTest(raises=raises),tempfile.TemporaryDirectory() as folder:
                result,report,controller,lock,locate,_=self.run_startup(
                    folder,click_succeeds=False,click_raises=raises,start_mode='click')
                self.assertEqual(result,1)
                self.assertTrue(report['input_failed'])
                controller.post_click.assert_called_once_with(1130,616)
                lock.observe.assert_not_called()
                locate.assert_not_called()


if __name__=='__main__':
    unittest.main()
