"""Cooperative room regression checks against captured game screens."""
import tempfile
import json
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions, ChartSelection, COOP_ROOMS
from online_live import OnlineLiveFlow
from song_catalog import BY_ID


class CoopTests(unittest.TestCase):
    def flow(self,folder,**values):
        return OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                              ChartOptions.parse({'mode':'coop',**values}),folder)

    def test_coop_choices_reject_unknown_room_and_non_cn_song(self):
        for values in ({'coop_room':'unknown'},{'coop_room_group':'unknown'},{'coop_song':'999999'}):
            with self.subTest(values=values),self.assertRaises(ValueError):
                ChartOptions.parse({'mode':'coop',**values})
        settings=ChartOptions.parse({'mode':'coop','coop_song':'KING','coop_room':'legend','coop_room_group':'special'})
        self.assertEqual((settings.coop_song,settings.coop_room,settings.coop_room_group),('361','legend','special'))

    def test_coop_and_cp_song_dropdowns_include_cn_catalog_and_write_distinct_parameters(self):
        interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        for name,key in [('谱面协力歌曲','coop_song'),('谱面挑战歌曲','cp_song')]:
            option=interface['option'][name]
            self.assertEqual(option['type'],'select')
            values=[case['pipeline_override']['CL_'+key]['attach']['value'] for case in option['cases']]
            self.assertEqual(len(values),len(BY_ID)+1)
            self.assertEqual(set(values),{'',*BY_ID})
            king=next(case for case in option['cases'] if case['name']=='KING')
            settings=ChartOptions.parse({'mode':'coop' if key=='coop_song' else 'challenge',key:king['pipeline_override']['CL_'+key]['attach']['value']})
            self.assertEqual(getattr(settings,key),'361')

    def test_room_power_failure_does_not_start_matching(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder,coop_room='legend')
            flow.navigate_menu=Mock();flow.configure_menu_fire=Mock(return_value=True);flow.configure_fever=Mock()
            flow.open_page=Mock();flow.wait=Mock();flow.tap=Mock();flow.tap_hit=Mock()
            flow.hit_text=Mock(return_value=object());flow.selected_coop_room=Mock(return_value='legend')
            flow.text=Mock(side_effect=['290000','100000'])
            flow.image=np.full((720,1280,3),255,dtype=np.uint8)
            with self.assertRaisesRegex(RuntimeError,'需要综合能力290000'):
                flow.join_room()
            flow.tap.assert_not_called()
            self.assertFalse(flow.room_active)
            self.assertEqual(flow.report['attempts'],0)

    def test_room_title_uses_screen_order_for_wrapped_ocr(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.ocr=Mock(return_value=[SimpleNamespace(text='房间',box=[610,280,90,35]),
                                        SimpleNamespace(text='新手',box=[610,236,90,35])])
            self.assertEqual(flow.selected_coop_room(),'beginner')

    def test_specific_song_is_verified_and_submitted_only_once(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder,coop_song='361')
            flow.current_attempt={};flow.snap=Mock();flow.save_frame=Mock()
            flow.all_songs=Mock()
            flow.find_filtered_song=Mock();flow.selected_song_matches=Mock(return_value=True)
            flow.reco=Mock(return_value=True);flow.pink=Mock(return_value=True)
            flow.image=np.zeros((720,1280,3),dtype=np.uint8)
            button=object();flow.hit_text=Mock(side_effect=lambda roi,pattern:button if pattern in ('^确定$','^不指定歌曲$') else None);flow.tap_hit=Mock()
            flow.submit_coop_song();flow.submit_coop_song()
            flow.find_filtered_song.assert_called_once_with(BY_ID['361'],max_steps=8,forward_first=True,quick=True)
            flow.all_songs.assert_called_once_with(BY_ID['361'],quick=True)
            flow.tap_hit.assert_called_once_with(button)
            self.assertEqual(flow.current_attempt['submitted_song'],'361')

    def test_mismatched_song_is_never_submitted(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder,coop_song='361')
            flow.current_attempt={};flow.snap=Mock();flow.save_frame=Mock();flow.find_filtered_song=Mock()
            flow.all_songs=Mock()
            flow.hit_text=Mock(side_effect=lambda roi,pattern:object() if pattern=='^不指定歌曲$' else None)
            flow.reco=Mock(return_value=True);flow.selected_song_matches=Mock(return_value=False)
            flow.tap_hit=Mock()
            with self.assertRaisesRegex(RuntimeError,'未确认所选歌曲'):
                flow.submit_coop_song()
            flow.tap_hit.assert_not_called()
            self.assertFalse(flow.coop_song_submitted)

    def test_song_loading_does_not_mark_submission_or_click(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder,coop_song='361')
            flow.hit_text=Mock(return_value=object());flow.tap_hit=Mock();flow.find_filtered_song=Mock()
            flow.submit_coop_song()
            self.assertFalse(flow.coop_song_submitted)
            flow.tap_hit.assert_not_called();flow.find_filtered_song.assert_not_called()

    def test_song_transition_waits_for_both_random_and_specific_selection(self):
        for song in ('','361'):
            with self.subTest(song=song),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder,coop_song=song)
                flow.hit_text=Mock(return_value=None);flow.tap_hit=Mock()
                flow.all_songs=Mock();flow.save_frame=Mock()
                self.assertFalse(flow.submit_coop_song())
                self.assertFalse(flow.coop_song_submitted)
                flow.tap_hit.assert_not_called();flow.all_songs.assert_not_called()

    def test_song_loading_has_a_bounded_timeout_without_clicks(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder);flow.current_attempt={}
            flow.snap=Mock();flow.pause=Mock();flow.save_frame=Mock()
            flow.reco=Mock(side_effect=lambda name:name=='OL_CoopSongPage')
            flow.hit_text=Mock(return_value=None);flow.tap_hit=Mock()
            with patch('online_live.time.monotonic',side_effect=[0,10,30]):
                with self.assertRaisesRegex(RuntimeError,'30秒未加载'):
                    flow.await_final()
            self.assertEqual(flow.snap.call_count,3)
            flow.tap_hit.assert_not_called()
            self.assertFalse(flow.coop_song_submitted)

    def test_disconnect_is_not_hidden_while_waiting_for_song_page(self):
        from online_policy import RoomInterrupted
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder);flow.current_attempt={}
            flow.snap=Mock(side_effect=[None,RoomInterrupted('连接中断')])
            flow.pause=Mock();flow.save_frame=Mock()
            flow.reco=Mock(side_effect=lambda name:name=='OL_CoopSongPage')
            flow.hit_text=Mock(return_value=None);flow.tap_hit=Mock()
            with self.assertRaisesRegex(RoomInterrupted,'连接中断'):
                flow.await_final()
            flow.tap_hit.assert_not_called()

    def test_unknown_modal_never_clicks_background_result(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                                ChartOptions.parse({'mode':'coop'}),folder)
            flow.snap=Mock();flow.result_modals=Mock(return_value=False)
            flow.dismiss_talk=Mock(return_value=False);flow.reco=Mock(return_value=False)
            flow.pause=Mock();flow.hit_text=Mock();flow.tap_hit=Mock()
            flow.require_clear_notification_overlay=Mock(side_effect=RuntimeError('unknown modal'))
            with patch('online_live.dialog_box',return_value=[100,100,1000,500]),patch('online_live.time.monotonic',side_effect=[0,1,1,1,5,5]):
                with self.assertRaisesRegex(RuntimeError,'unknown modal'):
                    flow.settle_online({})
            flow.hit_text.assert_not_called()
            flow.tap_hit.assert_not_called()

    def test_group_result_advances_without_counting_song_yet(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                                ChartOptions.parse({'mode':'coop','fire':0}),folder)
            flow.snap=Mock();flow.result_modals=Mock(return_value=False)
            flow.dismiss_talk=Mock(return_value=False);flow.save_frame=Mock()
            flow.reco=Mock(side_effect=lambda node:node=='OL_CoopGroupResult')
            button=object()
            flow.hit_text=Mock(side_effect=lambda roi,pattern:button if '^下一步$' in pattern else None)
            flow.tap_hit=Mock(side_effect=RuntimeError('advanced group page'))
            with self.assertRaisesRegex(RuntimeError,'advanced group page'):
                flow.settle_online({})
            flow.tap_hit.assert_called_once_with(button)
            self.assertEqual(flow.report['completed_rounds'],0)

    def test_cost_requires_two_matching_previews_without_top_currency_bar(self):
        for second,accepted in [((18,18),True),((18,17),False)]:
            with self.subTest(second=second),tempfile.TemporaryDirectory() as folder:
                flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                                    ChartOptions.parse({'mode':'coop','fire':0}),folder)
                flow.verify_final_selection=Mock();flow.snap=Mock();flow.pause=Mock()
                flow.reco=Mock(return_value=True)
                flow.fire_balance=Mock(side_effect=AssertionError('No top counter in coop'))
                flow.fire_preview=Mock(side_effect=[(18,18),second,(18,18),second])
                if accepted:
                    self.assertEqual(flow.verify_chart_start(1,ChartSelection('91','expert'),0),18)
                else:
                    with self.assertRaisesRegex(RuntimeError,'读数不稳定'):
                        flow.verify_chart_start(1,ChartSelection('91','expert'),0)

    def test_real_coop_pages_song_difficulty_and_zero_fire(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        import numpy as np
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        frames={name:np.asarray(Image.open(ROOT/f'tests/fixtures/coop/{name}.png').convert('RGB'))[:,:,::-1].copy()
                for name in ('ready','song','shuffle','group_result','achievement','title_punctuation','song_transition','loading')}
        current=[frames['ready']]
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'coop-fixture'
            def screencap(self): return current[0]
        resource=Resource();self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        controller=Controller();self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker();tasker.bind(resource,controller)
        observed={};case=self
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        flow=OnlineLiveFlow(context,ChartOptions.parse({'mode':'coop','fire':0}),folder)
                        for name,node in [('song','OL_CoopSongPage'),('shuffle','OL_RandomSong'),('group_result','OL_CoopGroupResult'),('ready','OL_FinalConfirm')]:
                            current[0]=frames[name];flow.snap()
                            case.assertTrue(flow.reco(node),(name,node))
                        case.assertEqual(flow.read_final_song()['id'],'91')
                        current[0]=frames['title_punctuation'];flow.snap()
                        case.assertEqual(flow.read_final_song()['id'],'595')
                        flow.verify_final_selection(ChartSelection('595','expert'))
                        current[0]=frames['ready'];flow.snap()
                        flow.verify_final_selection(ChartSelection('91','expert'))
                        case.assertEqual(flow.verify_chart_start(1,ChartSelection('91','expert'),0),18)
                        case.assertTrue(flow.hit_text([1010,580,225,105],'^准备完毕[！!]?$'))
                        current[0]=frames['song'];flow.snap()
                        case.assertTrue(flow.hit_text([685,605,200,80],'^不指定歌曲$'))
                        # Real failed frame -> loading -> visible controls -> final.
                        # Exercise the outer loop so it keeps taking fresh frames.
                        loading_flow=OnlineLiveFlow(context,ChartOptions.parse({'mode':'coop','fire':0}),folder)
                        loading_flow.current_attempt={};loading_flow.pause=Mock()
                        from online_policy import RoomClock
                        import time
                        loading_flow.room_clock=RoomClock(time.monotonic())
                        sequence=iter(['song_transition','loading','song','song','ready'])
                        def next_frame():
                            current[0]=frames[next(sequence)]
                            return OnlineLiveFlow.snap(loading_flow)
                        loading_flow.snap=Mock(side_effect=next_frame)
                        loading_flow.tap_hit=Mock()
                        loading_flow.await_final()
                        case.assertEqual(loading_flow.snap.call_count,5)
                        loading_flow.tap_hit.assert_called_once()
                        case.assertTrue(loading_flow.coop_song_submitted)
                        case.assertIsNone(loading_flow.current_attempt['submitted_song'])
                        current[0]=frames['song'];flow.snap()
                        # The second-row All only covers Favorites. Leave that
                        # category by scrolling back to the main All entry.
                        flow.tap=Mock(side_effect=AssertionError('already in sidebar view'))
                        flow.tap_hit=Mock(side_effect=AssertionError('must not select all favorites'))
                        flow.swipe=Mock(side_effect=RuntimeError('scroll categories'))
                        with case.assertRaisesRegex(RuntimeError,'scroll categories'):
                            flow.all_songs_category()
                        flow.tap_hit.assert_not_called()
                        current[0]=frames['achievement'];flow.snap()
                        flow.tap_hit=Mock(side_effect=lambda hit:current.__setitem__(0,frames['ready']))
                        case.assertTrue(flow.dismiss_notifications())
                        flow.tap_hit.assert_called_once()
                        return True
                    except Exception as exc:
                        observed['error']=repr(exc);return False
            resource.register_custom_action('CoopCheck',Check())
            job=tasker.post_task('CoopCheck',{'CoopCheck':{'action':'Custom','custom_action':'CoopCheck'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__': unittest.main()
