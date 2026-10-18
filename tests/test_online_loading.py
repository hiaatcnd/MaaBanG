"""Covered ready buttons and bounded room exit; no real device input."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions, ChartSelection
from costume_unlock import FlowError
from online_live import OnlineLiveFlow
from online_policy import RoomClock, RoomInterrupted


class OnlineLoadingTests(unittest.TestCase):
    def flow(self,folder,mode='versus'):
        return OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                              ChartOptions.parse({'mode':mode,'versus_room':'free','fire':0}),folder)

    def test_unconfirmed_loading_exits_even_when_background_says_matching(self):
        for mode in ('coop','versus'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder,mode)
                flow.current_attempt={};flow.room_clock=RoomClock(0)
                now=[-4.]
                flow.snap=Mock(side_effect=lambda:now.__setitem__(0,now[0]+4))
                flow.pause=Mock();flow.save_frame=Mock()
                flow.is_final_page=Mock(return_value=False)
                flow.reco=Mock(side_effect=lambda node:node=='OL_Matching')
                flow.ready_loading=Mock(return_value=True)
                flow.hit_text=Mock(return_value=None);flow.tap_hit=Mock()
                with patch('online_live.time.monotonic',side_effect=lambda:now[0]):
                    with self.assertRaisesRegex(RoomInterrupted,'加载遮挡页面超过8秒'):
                        flow.await_final()
                self.assertEqual(flow.snap.call_count,3)
                flow.tap_hit.assert_not_called()

    def test_unknown_page_exits_after_short_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.current_attempt={};flow.room_clock=RoomClock(0)
            now=[-4.]
            flow.snap=Mock(side_effect=lambda:now.__setitem__(0,now[0]+4))
            flow.pause=Mock();flow.save_frame=Mock()
            flow.reco=Mock(return_value=None)
            flow.hit_text=Mock(return_value=None);flow.tap_hit=Mock()
            with patch('online_live.time.monotonic',side_effect=lambda:now[0]):
                with self.assertRaisesRegex(RoomInterrupted,'页面8秒无法识别'):
                    flow.await_final()
            self.assertEqual(flow.snap.call_count,3)
            flow.tap_hit.assert_not_called()

    def test_loading_clear_resets_only_its_own_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.current_attempt={};flow.room_clock=RoomClock(0)
            now=[-4.]
            flow.snap=Mock(side_effect=lambda:now.__setitem__(0,now[0]+4))
            flow.pause=Mock();flow.save_frame=Mock()
            flow.is_final_page=Mock(side_effect=lambda:now[0]==16)
            flow.ready_loading=Mock(side_effect=lambda:now[0]!=8)
            flow.reco=Mock(side_effect=lambda node:node=='OL_Matching')
            flow.hit_text=Mock(return_value=None)
            with patch('online_live.time.monotonic',side_effect=lambda:now[0]):
                flow.await_final()
            self.assertEqual(flow.snap.call_count,5)

    def test_unknown_or_loading_room_uses_focus_exit_and_confirms_home(self):
        for node in ('OL_CoopSongPage','OL_RandomSong','loading',None):
            with self.subTest(node=node),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder)
                flow.current_attempt={};flow.room_active=True
                flow.foreground_package=Mock(return_value='com.bilibili.star.bili')
                phase=[0];now=[0.]
                flow.snap=Mock();flow.pause=Mock(side_effect=lambda _:now.__setitem__(0,now[0]+1))
                flow.reco=Mock(side_effect=lambda name:name==(node if phase[0]==0 else 'CU_HomeBand'))
                flow.hit_text=Mock(return_value=None)
                flow.ready_loading=Mock(return_value=node=='loading')
                flow.background_room=Mock(side_effect=lambda:phase.__setitem__(0,1))
                flow.tap=Mock();flow.back=Mock()
                with patch('online_live.time.monotonic',side_effect=lambda:now[0]):
                    flow.recover_room()
                flow.background_room.assert_called_once()
                flow.tap.assert_not_called();flow.back.assert_not_called()
                self.assertLessEqual(now[0],3)
                self.assertFalse(flow.room_active)

    def test_authorized_worker_is_not_stopped_if_room_started_before_ready_click(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.image=np.asarray(Image.open(ROOT/'tests/fixtures/versus/unattended_stage.png'))[:,:,::-1]
            flow.snap=Mock();flow.reco=Mock(return_value=None)
            flow.loading_final_page=Mock(return_value=False)
            flow.quick_tap=Mock()
            flow.submit_online_ready(ChartSelection('785','expert'))
            flow.quick_tap.assert_not_called()
            self.assertEqual(flow.report['state'],'等待联网自动开演')

    def test_captured_loading_and_covered_ready_use_production_recognition(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        def frame(name):
            return np.asarray(Image.open(ROOT/'tests/fixtures'/name).convert('RGB'))[:,:,::-1].copy()
        loading=frame('versus/song_loading.png')
        # The final frame was not saved in the incident. Replay its logged
        # occlusion using the actual loading overlay on captured ready pages.
        frames={mode:frame(name) for mode,name in (
            ('coop','coop/ready.png'),('versus','versus/ready_effects_off.png'))}
        for ready in frames.values():
            ready[600:700,870:1255]=loading[600:700,870:1255]
        current=[loading]
        class Controller(CustomController):
            def connect(self):return True
            def request_uuid(self):return 'loading-replay'
            def screencap(self):return current[0]
        resource=Resource();self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        controller=Controller();self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker();tasker.bind(resource,controller)
        observed={};case=self
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        for mode,song,balance in (('coop','91',18),('versus','785',45)):
                            flow=OnlineLiveFlow(context,ChartOptions.parse({'mode':mode,'fire':0}),folder)
                            flow.pause=Mock();flow.save_frame=Mock();flow.quick_tap=Mock()
                            current[0]=loading;flow.snap()
                            case.assertTrue(flow.ready_loading())
                            case.assertFalse(flow.is_final_page())
                            flow.current_attempt={}
                            current[0]=frames[mode];flow.snap()
                            case.assertFalse(flow.reco('OL_FinalConfirm'))
                            case.assertTrue(flow.is_final_page(),(mode,
                                flow.text([110,580,130,35]),flow.text([540,550,452,65]),
                                flow.text([865,595,415,115])))
                            flow.await_final()
                            selection=flow.read_final_selection()
                            case.assertEqual(selection,ChartSelection(song,'expert'))
                            case.assertEqual(flow.verify_chart_start(1,selection,0),balance)
                            if mode=='versus':
                                case.assertIsNone(flow.disable_mv(fresh=False).background)
                            flow.submit_online_ready(selection)
                            flow.quick_tap.assert_not_called()
                            if mode=='versus':
                                # Exercise the whole parent startup with real
                                # OCR, replacing only the external touch worker.
                                flow.report['attempts']=1
                                destination=flow.output/'attempt1_playback'
                                process=Mock()
                                def poll():
                                    if (destination/'start').exists():
                                        (destination/'playback.json').write_text(json.dumps({'status':'input_complete'}))
                                        return 0
                                    return None
                                process.poll.side_effect=poll
                                process.returncode=0
                                def spawn(*args,**kwargs):
                                    (destination/'armed').write_text('ready')
                                    return process
                                row={}
                                with patch('chart_live.subprocess.Popen',side_effect=spawn):
                                    flow.play_chart(1,selection,{'duration':1},0,row)
                                case.assertTrue((destination/'start').exists())
                                case.assertFalse((destination/'stop').exists())
                                case.assertEqual(row['fire_before'],balance)
                                flow.quick_tap.assert_not_called()
                            # Neither cost nor identity may be waived by loading.
                            with case.assertRaisesRegex(FlowError,'火数预览不符'):
                                flow.verify_chart_start(1,selection,1)
                            with case.assertRaisesRegex(FlowError,'歌曲发生变化'):
                                flow.verify_final_selection(ChartSelection('306','expert'))
                            with case.assertRaisesRegex(FlowError,'难度发生变化'):
                                flow.verify_final_selection(ChartSelection(song,'hard'))
                            current[0]=frame('versus/room_free.png');flow.snap()
                            case.assertFalse(flow.is_final_page())
                            current[0]=frame('versus/unattended_stage.png');flow.snap()
                            case.assertFalse(flow.is_final_page())
                        return True
                    except Exception as error:
                        import traceback
                        observed['error']=traceback.format_exc()
                        return False
            resource.register_custom_action('LoadingReplay',Check())
            job=tasker.post_task('LoadingReplay',{'LoadingReplay':{
                'action':'Custom','custom_action':'LoadingReplay'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__':
    unittest.main()
