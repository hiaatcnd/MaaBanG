"""Versus room choices, guarded submission and captured-screen regression."""
import tempfile
from dataclasses import replace
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from interface_test_support import load_interface
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_live import ChartLive
from chart_policy import ChartOptions, ChartSelection
from online_live import OnlineLiveFlow
from costume_unlock import FlowError
from song_catalog import BY_ID


class VersusTests(unittest.TestCase):
    def flow(self,folder,**values):
        return ChartLive().create_flow(
            SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
            {'mode':'versus',**values},folder)

    def test_versus_routes_to_online_playback_without_fixed_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            self.assertIsInstance(flow,OnlineLiveFlow)
            self.assertEqual(flow.playback_start_mode(),'online')
            self.assertEqual(flow.settings.selections,())
            self.assertEqual(flow.settings.songs_per_round,1)
            self.assertEqual(flow.entry_node,'OL_VersusEntry')

    def test_room_choices_are_independent_of_coop_and_ignore_hidden_event_song(self):
        options=ChartOptions.parse({'mode':'versus','versus_room':'free',
                                   'versus_song':'KING','coop_song':'306'})
        self.assertEqual(options.versus_song,'361')
        event=ChartOptions.parse({'mode':'versus','versus_room':'event','versus_song':'invalid'})
        self.assertEqual(event.versus_song,'')
        for values in ({'versus_room':'unknown'},{'versus_room_group':'unknown'},
                       {'versus_room':'free','versus_song':'999999'}):
            with self.subTest(values=values),self.assertRaises(ValueError):
                ChartOptions.parse({'mode':'versus',**values})

    def test_ui_exposes_free_song_only_in_free_room_and_uses_shared_catalog(self):
        interface=load_interface()
        modes=interface['option']['谱面演出模式']['cases']
        self.assertEqual([case['pipeline_override']['CL_mode']['attach']['value'] for case in modes],
                         ['free','tour_free','tour_fixed','team','coop','challenge','versus'])
        cases=interface['option']['谱面竞演房间']['cases']
        self.assertNotIn('option',cases[0])
        self.assertIn('谱面竞演歌曲',cases[1]['option'])
        songs=interface['option']['谱面竞演歌曲']['cases']
        values={case['pipeline_override']['CL_versus_song']['attach']['value'] for case in songs}
        self.assertEqual(values,{'',*BY_ID})

    def test_event_room_never_submits_stale_free_or_coop_song(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder,versus_song='361',coop_song='306')
            flow.navigate_menu=Mock()
            self.assertFalse(flow.chooses_song)
            self.assertEqual(flow.requested_song,'')
            flow.prepare_settings()
            flow.navigate_menu.assert_not_called()

    def test_missing_requested_submission_cannot_authorize_final_page(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder,versus_room='free',versus_song='361')
            flow.snap=Mock()
            flow.reco=Mock(side_effect=lambda node:node=='OL_FinalConfirm')
            with self.assertRaisesRegex(FlowError,'未确认提交指定歌曲'):
                flow.await_final()

    def test_final_cost_uses_visible_preview_and_rejects_wrong_consumption(self):
        for cost,accepted in ((0,True),(1,False)):
            with self.subTest(cost=cost),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder)
                flow.verify_final_selection=Mock()
                flow.fire_preview=Mock(return_value=(10,10-cost))
                flow.fire_balance=Mock(side_effect=AssertionError('No top counter on final page'))
                flow.pause=Mock()
                if accepted:
                    self.assertEqual(flow.verify_chart_start(1,ChartSelection('361','expert'),0),10)
                else:
                    with self.assertRaisesRegex(FlowError,'火数预览不符'):
                        flow.verify_chart_start(1,ChartSelection('361','expert'),0)

    def test_captured_room_and_final_page_use_production_recognition(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        import numpy as np
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        frames={name:np.asarray(Image.open(ROOT/f'tests/fixtures/versus/{name}.png').convert('RGB'))[:,:,::-1].copy()
                for name in ('room_event','room_free','song','ready','ready_effects_off','daily_reward_choice','judgment')}
        current=[frames['room_event']]
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'versus-fixture'
            def screencap(self): return current[0]
        resource=Resource();self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        controller=Controller();self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker();tasker.bind(resource,controller)
        observed={};case=self
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        flow=OnlineLiveFlow(context,ChartOptions.parse({'mode':'versus','fire':0}),folder)
                        flow.snap()
                        case.assertTrue(flow.reco(flow.home_node))
                        case.assertEqual(flow.selected_versus_room(),'event')
                        flow.tap_hit=Mock();flow.save_frame=Mock()
                        flow.choose_versus_room()
                        case.assertEqual(flow.report['versus_room_verified'],{'type':'event','group':'normal'})
                        current[0]=frames['room_free'];flow.snap()
                        case.assertEqual(flow.selected_versus_room(),'free')
                        flow.settings=replace(flow.settings,versus_room='free',versus_song='24')
                        current[0]=frames['song'];flow.snap()
                        flow.current_attempt={};flow.tap_hit.reset_mock()
                        case.assertTrue(flow.submit_coop_song())
                        case.assertTrue(flow.submit_coop_song())
                        case.assertEqual(flow.current_attempt['submitted_song'],'24')
                        flow.tap_hit.assert_called_once()
                        case.assertEqual(flow.tap_hit.call_args.args[0].text,'确定')
                        current[0]=frames['ready'];flow.snap()
                        case.assertTrue(flow.reco('OL_FinalConfirm'))
                        case.assertEqual(flow.read_final_selection(),ChartSelection('785','expert'))
                        case.assertEqual(flow.verify_chart_start(1,ChartSelection('785','expert'),0),45)
                        current[0]=frames['ready_effects_off'];flow.snap()
                        flow.tap=Mock(side_effect=AssertionError('effects already disabled'))
                        flow.disable_mv(fresh=False)
                        flow.tap.assert_not_called()
                        current[0]=frames['daily_reward_choice'];flow.snap()
                        flow.tap_hit.reset_mock()
                        case.assertFalse(flow.dismiss_notifications())
                        with case.assertRaisesRegex(FlowError,'选择每日演出报酬'):
                            flow.result_modals()
                        flow.tap_hit.assert_not_called()
                        current[0]=frames['judgment']
                        flow.report['attempts']=1
                        flow.tap_hit=Mock(side_effect=lambda hit:current.__setitem__(0,frames['room_event']))
                        row={}
                        flow.settle_online(row)
                        case.assertEqual(flow.report['completed_rounds'],1)
                        case.assertTrue(row['counted'])
                        flow.tap_hit.assert_called_once()
                        flow.settle_online(row)
                        case.assertEqual(flow.report['completed_rounds'],1)
                        flow.tap_hit.assert_called_once()
                        return True
                    except Exception as error:
                        observed['error']=repr(error)
                        return False
            resource.register_custom_action('VersusCheck',Check())
            job=tasker.post_task('VersusCheck',{'VersusCheck':{'action':'Custom','custom_action':'VersusCheck'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__':
    unittest.main()
