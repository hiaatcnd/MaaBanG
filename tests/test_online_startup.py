"""Online startup safety and shared error OCR; no device input is permitted."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions, ChartSelection
from costume_unlock import FlowError
from online_live import OnlineLiveFlow
from song_catalog import BY_ID


def error_nodes():
    pipeline=json.loads((ROOT/'assets/resource/pipeline/online_live.json').read_text(encoding='utf-8'))
    return {name:{'enabled':True,'inverse':False,'recognition':{
                'type':'OCR','param':{'index':0,**{key:value for key,value in pipeline[name].items()
                                                if key!='recognition'}}}}
            for name in ('OL_Fatal','OL_Disconnected')}


def recognized(*texts):
    hits=[SimpleNamespace(text=text) for text in texts]
    return SimpleNamespace(filtered_results=hits,best_result=hits[0])


class RoomErrorTests(unittest.TestCase):
    def make_flow(self,nodes=None):
        flow=OnlineLiveFlow.__new__(OnlineLiveFlow)
        flow.ctx=SimpleNamespace(get_node_data=Mock(side_effect=(nodes or error_nodes()).get))
        flow.reco=Mock(return_value=None)
        return flow

    def test_single_ocr_classifies_fatal_disconnect_and_fatal_behind_disconnect(self):
        for texts,expected in [
                (('正在维护',),('OL_Fatal','正在维护')),
                (('房间已解散',),('OL_Disconnected','房间已解散')),
                (('房间已解散','账号受到限制'),('OL_Fatal','账号受到限制'))]:
            with self.subTest(texts=texts):
                nodes=error_nodes()
                flow=self.make_flow(nodes)
                flow.reco.return_value=recognized(*texts)
                self.assertEqual(flow.room_error(),expected)
                patterns=[pattern for node in nodes.values()
                          for pattern in node['recognition']['param']['expected']]
                flow.reco.assert_called_once_with('OL_Fatal',expected=patterns)

    def test_no_match_finishes_after_one_ocr(self):
        flow=self.make_flow()
        self.assertIsNone(flow.room_error())
        self.assertEqual(flow.reco.call_count,1)

    def test_resource_overrides_supply_patterns_without_a_second_rule_list(self):
        nodes=error_nodes()
        nodes['OL_Fatal']['recognition']['param']['expected']=['临时.*维护']
        nodes['OL_Disconnected']['recognition']['param']['expected']=['房间.*失效']
        flow=self.make_flow(nodes)
        flow.reco.return_value=recognized('临时系统维护')
        self.assertEqual(flow.room_error(),('OL_Fatal','临时系统维护'))
        flow.reco.assert_called_once_with('OL_Fatal',expected=['临时.*维护','房间.*失效'])

    def test_incompatible_parameters_fall_back_to_original_checks(self):
        changes=[('param','threshold',.9),('param','roi',[0,0,100,100]),
                 ('param','expected',[]),
                 ('both_params','index',1),('node','enabled',False),
                 ('node','inverse',True),('recognition','type','TemplateMatch')]
        for scope,key,value in changes:
            with self.subTest(scope=scope,key=key):
                nodes=error_nodes()
                node=nodes['OL_Disconnected']
                if scope=='both_params':
                    for item in nodes.values():item['recognition']['param'][key]=value
                elif scope=='param':node['recognition']['param'][key]=value
                elif scope=='recognition':node['recognition'][key]=value
                else:node[key]=value
                flow=self.make_flow(nodes)
                flow.reco.side_effect=[None,recognized('房间已解散')]
                self.assertEqual(flow.room_error(),('OL_Disconnected','房间已解散'))
                self.assertEqual(flow.reco.call_args_list,[call('OL_Fatal'),call('OL_Disconnected')])

    def test_fallback_retains_fatal_priority(self):
        nodes=error_nodes()
        nodes['OL_Disconnected']['recognition']['param']['threshold']=.9
        flow=self.make_flow(nodes)
        flow.reco.return_value=recognized('正在维护')
        self.assertEqual(flow.room_error(),('OL_Fatal','正在维护'))
        flow.reco.assert_called_once_with('OL_Fatal')

    def test_each_frame_reads_fresh_results_and_pipeline_overrides(self):
        nodes=error_nodes()
        flow=self.make_flow(nodes)
        flow.image=np.zeros((720,1280,3),dtype=np.uint8)
        flow.reco.side_effect=[None,recognized('房间已解散')]
        self.assertIsNone(flow.room_error())
        flow.image=np.ones((720,1280,3),dtype=np.uint8)
        nodes['OL_Disconnected']['recognition']['param']['expected']=['房间已解散']
        self.assertEqual(flow.room_error(),('OL_Disconnected','房间已解散'))
        self.assertEqual(flow.reco.call_count,2)
        self.assertEqual(flow.ctx.get_node_data.call_count,4)
        self.assertEqual(flow.reco.call_args.kwargs['expected'][-1],'房间已解散')
        self.assertNotIn('连接失败',flow.reco.call_args.kwargs['expected'])


class FinalSelectionSafetyTests(unittest.TestCase):
    def make_flow(self):
        flow=OnlineLiveFlow.__new__(OnlineLiveFlow)
        flow.difficulty_y=594
        flow.image=np.zeros((720,1280,3),dtype=np.uint8)
        flow.image[574:606,805:835]=[255,0,0]
        flow.snap=Mock()
        flow.pause=Mock()
        flow.reco=Mock(return_value=True)
        flow.text=Mock(return_value='魔法少女とチョコレト')
        flow.ocr=Mock(return_value=[SimpleNamespace(text='EXPERT',box=[780,570,80,40])])
        flow.quick_tap=Mock(side_effect=AssertionError('Verification must not change the selection'))
        return flow

    def test_known_song_verification_accepts_one_matching_fresh_sample(self):
        flow=self.make_flow()
        flow.verify_final_selection(ChartSelection('564','expert'))
        self.assertEqual(flow.snap.call_count,1)
        self.assertEqual(flow.text.call_count,1)
        self.assertEqual(flow.reco.call_args_list,[call('OL_FinalConfirm')])
        flow.quick_tap.assert_not_called()

    def test_initial_song_selection_still_requires_two_stable_samples(self):
        flow=self.make_flow()
        self.assertEqual(flow.read_final_song()['id'],'564')
        self.assertEqual(flow.snap.call_count,2)
        self.assertEqual(flow.text.call_count,2)

    def test_changed_song_is_rejected_before_difficulty_read(self):
        flow=self.make_flow()
        with self.assertRaisesRegex(FlowError,'歌曲发生变化'):
            flow.verify_final_selection(ChartSelection('91','expert'))
        self.assertEqual(flow.snap.call_count,2)
        flow.ocr.assert_not_called()

    def test_initial_title_change_requires_two_new_matching_samples(self):
        flow=self.make_flow()
        flow.text.side_effect=[BY_ID['91']['title'],'魔法少女とチョコレト','魔法少女とチョコレト']
        self.assertEqual(flow.read_final_song()['id'],'564')
        self.assertEqual(flow.snap.call_count,3)
        self.assertEqual(flow.text.call_count,3)

    def test_known_song_can_recover_from_one_transient_wrong_title(self):
        flow=self.make_flow()
        flow.text.side_effect=[BY_ID['91']['title'],'魔法少女とチョコレト']
        flow.verify_final_selection(ChartSelection('564','expert'))
        self.assertEqual(flow.snap.call_count,2)
        self.assertEqual(flow.text.call_count,2)

    def test_page_departure_on_either_title_sample_is_rejected(self):
        for confirmations,title_reads in [([False],0),([True,False],1)]:
            with self.subTest(confirmations=confirmations):
                flow=self.make_flow()
                flow.reco.side_effect=confirmations
                flow.text.return_value=BY_ID['91']['title']
                with self.assertRaisesRegex(FlowError,'页面已离开'):
                    flow.verify_final_selection(ChartSelection('564','expert'))
                self.assertEqual(flow.text.call_count,title_reads)
                flow.ocr.assert_not_called()

    def test_unselected_difficulty_is_rejected(self):
        flow=self.make_flow()
        flow.image[:]=0
        with self.assertRaisesRegex(FlowError,'难度发生变化'):
            flow.verify_final_selection(ChartSelection('564','expert'))

    def test_missing_difficulty_is_rejected(self):
        flow=self.make_flow()
        flow.ocr.return_value=[]
        with self.assertRaisesRegex(FlowError,'难度按钮消失'):
            flow.verify_final_selection(ChartSelection('564','expert'))

    def test_enabled_cut_in_is_rejected(self):
        flow=self.make_flow()
        flow.image[637:663,487:513]=[100,0,255]
        with self.assertRaisesRegex(FlowError,'Cut in未关闭'):
            flow.verify_final_selection(ChartSelection('564','expert'))


class SelectionActionTests(unittest.TestCase):
    def make_flow(self,mode='coop'):
        flow=OnlineLiveFlow.__new__(OnlineLiveFlow)
        flow.settings=SimpleNamespace(mode=mode,difficulties=('expert',))
        flow.difficulty_y=594
        flow.image=np.zeros((720,1280,3),dtype=np.uint8)
        flow.image[574:606,805:835]=[255,0,0]
        flow.read_final_song=Mock(return_value=BY_ID['564'])
        flow.ocr=Mock(return_value=[SimpleNamespace(text='EXPERT',box=[780,570,80,40])])
        flow.snap=Mock()
        flow.reco=Mock(return_value=True)
        flow.quick_tap=Mock()
        flow.hit_text=Mock()
        flow.state=Mock()
        return flow

    def test_selected_difficulty_is_accepted_without_another_tap_or_frame(self):
        flow=self.make_flow()
        selection=flow.read_final_selection()
        self.assertEqual((selection.song_id,selection.difficulty),('564','expert'))
        self.assertIs(flow.selection,selection)
        flow.read_final_song.assert_called_once_with()
        flow.quick_tap.assert_not_called()
        flow.snap.assert_not_called()

    def test_unselected_difficulty_is_clicked_and_verified_on_a_new_frame(self):
        flow=self.make_flow()
        flow.image[:]=0
        flow.quick_tap.side_effect=lambda x,y:flow.image.__setitem__((slice(574,606),slice(805,835)),[255,0,0])
        self.assertEqual(flow.read_final_selection().difficulty,'expert')
        flow.quick_tap.assert_called_once_with(820,594)
        flow.snap.assert_called_once_with()
        flow.reco.assert_called_once_with('OL_FinalConfirm')

    def test_failed_difficulty_click_does_not_accept_selection(self):
        flow=self.make_flow()
        flow.image[:]=0
        with self.assertRaisesRegex(FlowError,'难度未选中'):
            flow.read_final_selection()
        flow.quick_tap.assert_called_once_with(820,594)
        self.assertFalse(hasattr(flow,'selection'))

    def test_page_departure_after_difficulty_click_is_rejected(self):
        flow=self.make_flow()
        flow.image[:]=0
        flow.reco.return_value=None
        with self.assertRaisesRegex(FlowError,'倒计时内确认难度'):
            flow.read_final_selection()
        flow.snap.assert_called_once_with()
        self.assertFalse(hasattr(flow,'selection'))

    def test_selected_difficulty_still_disables_and_rechecks_cut_in(self):
        flow=self.make_flow()
        flow.image[637:663,487:513]=[100,0,255]
        flow.quick_tap.side_effect=lambda x,y:flow.image.__setitem__((slice(637,663),slice(487,513)),0)
        flow.read_final_selection()
        flow.quick_tap.assert_called_once_with(500,650)
        flow.snap.assert_called_once_with()

    def test_cut_in_that_stays_enabled_is_rejected(self):
        flow=self.make_flow()
        flow.image[637:663,487:513]=[100,0,255]
        with self.assertRaisesRegex(FlowError,'Cut in未关闭'):
            flow.read_final_selection()
        flow.quick_tap.assert_called_once_with(500,650)
        self.assertFalse(hasattr(flow,'selection'))

    def test_missing_difficulty_is_not_clicked(self):
        flow=self.make_flow()
        flow.ocr.return_value=[]
        with self.assertRaisesRegex(FlowError,'未找到目标难度按钮'):
            flow.read_final_selection()
        flow.quick_tap.assert_not_called()

    def test_coop_reuses_ready_button_recognition(self):
        flow=self.make_flow()
        flow.reco.return_value=SimpleNamespace(box=[1010,580,220,100])
        flow.submit_online_ready(ChartSelection('564','expert'))
        flow.snap.assert_called_once_with()
        flow.reco.assert_called_once_with('OL_FinalConfirm')
        flow.hit_text.assert_not_called()
        flow.quick_tap.assert_called_once_with(1120,630)
        flow.state.assert_called_once_with('等待联网开演')

    def test_team_uses_ready_button_not_the_final_page_header(self):
        flow=self.make_flow('team')
        flow.reco.return_value=SimpleNamespace(box=[110,50,340,45])
        flow.hit_text.return_value=SimpleNamespace(box=[1010,580,220,100])
        flow.submit_online_ready(ChartSelection('564','expert'))
        flow.hit_text.assert_called_once_with([1010,580,225,105],'^准备完毕[！!]?$')
        flow.quick_tap.assert_called_once_with(1120,630)

    def test_ready_submission_does_not_click_after_page_departure(self):
        for mode in ('coop','team'):
            with self.subTest(mode=mode):
                flow=self.make_flow(mode)
                flow.reco.return_value=None
                with self.assertRaisesRegex(FlowError,'确认页面已离开'):
                    flow.submit_online_ready(ChartSelection('564','expert'))
                flow.hit_text.assert_not_called()
                flow.quick_tap.assert_not_called()
                flow.state.assert_not_called()

    def test_team_header_without_ready_button_is_not_submitted(self):
        flow=self.make_flow('team')
        flow.hit_text.return_value=None
        with self.assertRaisesRegex(FlowError,'未找到联网准备完毕按钮'):
            flow.submit_online_ready(ChartSelection('564','expert'))
        flow.quick_tap.assert_not_called()
        flow.state.assert_not_called()


class RoomErrorOCRReplayTests(unittest.TestCase):
    def test_real_ready_frame_has_no_error_and_preserves_context_rules(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        frame=np.asarray(Image.open(ROOT/'tests/fixtures/coop/ready.png').convert('RGB'))[:,:,::-1].copy()
        class Controller(CustomController):
            def connect(self):return True
            def request_uuid(self):return 'online-startup-fixture'
            def screencap(self):return frame
        controller=Controller()
        self.assertTrue(controller.post_connection().wait().succeeded)
        resource=Resource()
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        tasker=Tasker();tasker.bind(resource,controller)
        observed={};case=self
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        flow=OnlineLiveFlow(context,ChartOptions.parse({'mode':'coop','fire':0}),folder)
                        flow.image=frame
                        original=copy.deepcopy(context.get_node_data('OL_Fatal'))
                        flow.reco=Mock(wraps=flow.reco)
                        case.assertIsNone(flow.room_error())
                        case.assertEqual(flow.reco.call_count,1)
                        case.assertEqual(context.get_node_data('OL_Fatal'),original)
                        return True
                    except Exception as error:
                        observed['error']=repr(error)
                        return False
            resource.register_custom_action('OnlineStartupCheck',Check())
            job=tasker.post_task('OnlineStartupCheck',{'OnlineStartupCheck':{
                'action':'Custom','custom_action':'OnlineStartupCheck'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__':unittest.main()
