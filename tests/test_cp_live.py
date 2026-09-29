import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions, ChartSelection
from cp_live import CPLiveFlow


class CPTests(unittest.TestCase):
    def test_reward_animation_waits_without_clicking_background(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.image=object();flow.pause=Mock();flow.snap=Mock();flow.reco=Mock(return_value=False)
            with patch('cp_live.ChartLiveFlow.result_modals',side_effect=[False,True]),patch('cp_live.dialog_box',return_value=[1,1,500,400]):
                self.assertTrue(flow.result_modals())
            flow.pause.assert_called_once_with(.2)
            flow.snap.assert_called_once()

    def test_unknown_reward_overlay_still_stops_after_animation_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.image=object();flow.pause=Mock();flow.snap=Mock();flow.reco=Mock(return_value=False)
            with patch('cp_live.ChartLiveFlow.result_modals',return_value=False),patch('cp_live.dialog_box',return_value=[1,1,500,400]),patch('notifications.dialog_box',return_value=[1,1,500,400]),patch('cp_live.time.monotonic',side_effect=[0,4]):
                with self.assertRaisesRegex(RuntimeError,'未识别的弹窗'):
                    flow.result_modals()
            flow.snap.assert_not_called()

    def test_story_skip_is_left_to_settlement_handler(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.reco=Mock(side_effect=lambda node: node=='LV_TalkSkipConfirm')
            flow.snap=Mock()
            with patch('cp_live.ChartLiveFlow.result_modals',return_value=False):
                self.assertFalse(flow.result_modals())
            flow.snap.assert_not_called()

    def flow(self,folder,**values):
        return CPLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                          ChartOptions.parse(dict(mode='challenge',max_rounds=1,**values)),folder)

    def test_cp_configuration_ignores_stale_song_and_fire_options(self):
        options=ChartOptions.parse(dict(mode='challenge',song1='not in catalog',fire=99,
                                       shortage='items',cp=400))
        self.assertEqual(options.selections,())
        self.assertEqual(options.difficulties,('expert',))
        self.assertEqual((options.cp,options.fire,options.shortage,options.songs_per_round),(400,0,'stop',1))
        for cp in (0,1,201,True,1601):
            with self.subTest(cp=cp),self.assertRaises(ValueError):
                ChartOptions.parse(dict(mode='challenge',cp=cp))

    def test_cp_ui_has_no_fire_or_refill_options(self):
        interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        cases=interface['option']['谱面演出模式']['cases']
        case=next(c for c in cases if c['pipeline_override']['CL_mode']['attach']['value']=='challenge')
        self.assertIn('谱面每首CP',case['option'])
        self.assertNotIn('谱面每首火数',case['option'])
        task=next(t for t in interface['task'] if t['entry']=='ChartLive')
        self.assertNotIn('谱面每首火数',task['option'])
        self.assertNotIn('谱面火不足策略',task['option'])

    def test_insufficient_cp_cancels_dialog_without_accepting(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder,cp=800)
            flow.wait=Mock();flow.stable_integer=Mock(return_value=556)
            flow.tap=Mock();flow.read_integer=Mock()
            self.assertIsNone(flow.configure_cp())
            flow.tap.assert_called_once_with(510,620)
            flow.read_integer.assert_not_called()

    def test_all_cp_tiers_verified_before_dialog_acceptance(self):
        import numpy as np
        for amount,y in ((200,212),(400,285),(800,358),(1600,431)):
            with self.subTest(amount=amount),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder,cp=amount)
                flow.wait=Mock();flow.wait_ready=Mock();flow.verify_cp_cost=Mock()
                flow.stable_integer=Mock(return_value=2000)
                flow.read_integer=Mock(side_effect=[amount,2000])
                flow.tap=Mock();flow.pink=Mock(return_value=True)
                flow.image=np.zeros((720,1280,3),dtype=np.uint8)
                self.assertEqual(flow.configure_cp(),2000)
                self.assertEqual([c.args for c in flow.tap.call_args_list],[(877,y),(770,620)])

    def test_wrong_tier_does_not_accept_dialog(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.wait=Mock();flow.stable_integer=Mock(return_value=556)
            flow.read_integer=Mock(return_value=400);flow.tap=Mock()
            with self.assertRaisesRegex(RuntimeError,'档位数值'):
                flow.configure_cp()
            flow.tap.assert_not_called()

    def test_prestart_rejects_wrong_song_difficulty_cost_and_balance(self):
        selection=ChartSelection('774','expert')
        for title,difficulty,balance,cost in [('other','expert',556,'200消费'),
                ('MATSURI BAYASHI','easy',556,'200消费'),
                ('MATSURI BAYASHI','expert',None,'200消费'),
                ('MATSURI BAYASHI','expert',556,'400消费')]:
            with self.subTest(title=title,difficulty=difficulty,balance=balance,cost=cost),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder)
                flow.configure_cp=Mock(return_value=balance)
                flow.text=Mock(side_effect=[title,difficulty,cost])
                with self.assertRaises(RuntimeError):
                    flow.verify_chart_start(1,selection,200)

    def test_round_counts_only_confirmed_cp_deduction_and_never_refills(self):
        for after in (356,355):
            with self.subTest(after=after),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder)
                selection=ChartSelection('774','expert')
                flow.prepare_round=Mock(return_value=((selection,),[([],{})]))
                for name in ('configure_stage','await_chart_result','settle_results','navigate_menu','open_page','home'):
                    setattr(flow,name,Mock())
                flow.play_chart=Mock(side_effect=lambda i,s,m,c,row: row.update(cp_before=556))
                flow.stable_integer=Mock(return_value=after)
                flow.refill_fire=Mock(side_effect=AssertionError('CP must never refill fire'))
                if after==356:
                    flow.run()
                    self.assertEqual(flow.report['completed_rounds'],1)
                    self.assertEqual(flow.report['status'],'max_rounds_reached')
                else:
                    with self.assertRaisesRegex(RuntimeError,'CP扣除数'):
                        flow.run()
                    self.assertEqual(flow.report['completed_rounds'],0)
                flow.play_chart.assert_called_once()
                flow.refill_fire.assert_not_called()

    def test_cp_shortage_stops_before_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.prepare_round=Mock(return_value=((),[]))
            flow.navigate_menu=Mock();flow.home=Mock();flow.play_chart=Mock()
            flow.run()
            flow.play_chart.assert_not_called()
            self.assertTrue(flow.report['returned_home'])

    def test_cp_shortage_is_checked_before_song_download(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder,cp=800)
            flow.navigate_menu=Mock();flow.open_page=Mock()
            flow.stable_integer=Mock(return_value=556)
            flow.select_event_song=Mock();flow.store.get=Mock()
            self.assertEqual(flow.prepare_round(),((),[]))
            self.assertEqual(flow.report['status'],'insufficient_cp')
            flow.select_event_song.assert_not_called()
            flow.store.get.assert_not_called()

    def test_real_cp_screens_and_numbers_with_production_ocr(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        import numpy as np
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        frames={name:np.asarray(Image.open(ROOT/f'tests/fixtures/cp/{name}.png').convert('RGB'))[:,:,::-1].copy()
                for name in ('select','dialog','ready','menu','achievement_animation','achievement_receipt','achievement_summary')}
        current=[frames['select']]
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'cp-fixture'
            def screencap(self): return current[0]
        resource=Resource();self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        controller=Controller();self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker();tasker.bind(resource,controller)
        observed={};case=self
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        flow=CPLiveFlow(context,ChartOptions.parse({'mode':'challenge'}),folder)
                        for name,node in [('menu','CP_Entry'),('select','CP_Select'),('ready','CP_Ready'),('dialog','CP_Dialog')]:
                            current[0]=frames[name];flow.snap()
                            case.assertTrue(flow.reco(node),(name,flow.text([680,170,520,380]),flow.text([110,10,350,90])))
                        case.assertEqual(flow.read_integer([772,125,67,34]),556)
                        for amount,y in ((200,212),(400,285),(800,358),(1600,431)):
                            case.assertEqual(flow.read_integer([489,y-19,67,39]),amount)
                        current[0]=frames['select'];flow.snap()
                        case.assertEqual(flow.selected_song()['id'],'774')
                        case.assertEqual(flow.read_integer([1200,133,67,34]),556)
                        current[0]=frames['ready'];flow.snap()
                        flow.verify_cp_cost()
                        case.assertTrue(flow.ready())
                        case.assertEqual(flow.text([113,561,104,33]).strip().lower(),'expert')
                        # The animation frame cannot authorize a background click.
                        current[0]=frames['achievement_animation'];flow.snap()
                        flow.tap_hit=Mock(side_effect=lambda hit: current.__setitem__(0,frames['ready']))
                        case.assertFalse(flow.dismiss_notifications())
                        flow.tap_hit.assert_not_called()
                        for name in ('achievement_receipt','achievement_summary'):
                            current[0]=frames[name];flow.snap()
                            flow.tap_hit.reset_mock()
                            case.assertTrue(flow.dismiss_notifications(),name)
                            flow.tap_hit.assert_called_once()
                        return True
                    except Exception as exc:
                        observed['error']=repr(exc);return False
            resource.register_custom_action('CPCheck',Check())
            job=tasker.post_task('CPCheck',{'CPCheck':{'action':'Custom','custom_action':'CPCheck'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__': unittest.main()
