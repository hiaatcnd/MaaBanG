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
    def dialog_flow(self,folder,source='CP_Ready',opens_after=1,delay=0):
        flow=self.flow(folder)
        state={'page':source,'clock':0.,'clicks':0,'clicked_at':None}
        button=SimpleNamespace(box=[840,635,60,30] if source=='CP_Ready' else [1030,627,80,40])
        def snap():
            if (state['clicks']>=opens_after and state['clicked_at'] is not None
                    and state['clock']-state['clicked_at']>=delay):
                state['page']='CP_Dialog'
        def click(hit):
            self.assertIs(hit,button)
            state['clicks']+=1
            state['clicked_at']=state['clock']
        flow.snap=Mock(side_effect=snap)
        flow.reco=Mock(side_effect=lambda node: node==state['page'] or
                       (node=='CP_Header' and state['page'] in ('CP_Select','CP_Ready')))
        flow.hit_text=Mock(return_value=button)
        flow.tap_hit=Mock(side_effect=click)
        flow.tap=Mock(side_effect=AssertionError('No direct start or CP submission'))
        flow.pause=Mock(side_effect=lambda seconds:state.update(clock=state['clock']+seconds))
        return flow,state

    def test_cp_dialog_retries_ignored_navigation_from_select_and_ready(self):
        for source in ('CP_Select','CP_Ready'):
            with self.subTest(source=source),tempfile.TemporaryDirectory() as folder:
                flow,state=self.dialog_flow(folder,source,opens_after=2)
                with patch('cp_live.time.monotonic',side_effect=lambda:state['clock']):
                    flow.open_cp_dialog(from_ready=source=='CP_Ready')
                self.assertEqual(state['page'],'CP_Dialog')
                self.assertEqual(flow.tap_hit.call_count,2)
                flow.tap.assert_not_called()

    def test_cp_dialog_waits_for_delayed_open_without_second_click(self):
        with tempfile.TemporaryDirectory() as folder:
            flow,state=self.dialog_flow(folder,delay=3)
            with patch('cp_live.time.monotonic',side_effect=lambda:state['clock']):
                flow.open_cp_dialog(from_ready=True)
            flow.tap_hit.assert_called_once()
            self.assertEqual(state['page'],'CP_Dialog')

    def test_cp_dialog_already_open_is_not_clicked_again(self):
        with tempfile.TemporaryDirectory() as folder:
            flow,state=self.dialog_flow(folder,source='CP_Dialog')
            flow.open_cp_dialog()
            flow.tap_hit.assert_not_called()
            flow.hit_text.assert_not_called()

    def test_cp_dialog_stuck_source_stops_after_three_clicks(self):
        with tempfile.TemporaryDirectory() as folder:
            flow,state=self.dialog_flow(folder,opens_after=99)
            with patch('cp_live.time.monotonic',side_effect=lambda:state['clock']):
                with self.assertRaisesRegex(RuntimeError,'点击3次'):
                    flow.open_cp_dialog(from_ready=True)
            self.assertEqual(flow.tap_hit.call_count,3)
            flow.tap.assert_not_called()

    def test_cp_dialog_changed_page_or_overlay_prevents_second_click(self):
        from test_notifications import panel
        for changed in ('CP_Select','unknown','overlay'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as folder:
                flow,state=self.dialog_flow(folder,opens_after=99)
                click=flow.tap_hit.side_effect
                def change(hit):
                    click(hit)
                    if changed=='overlay': flow.image=panel()
                    else: state['page']=changed
                flow.tap_hit.side_effect=change
                with patch('cp_live.time.monotonic',side_effect=lambda:state['clock']):
                    with self.assertRaises(RuntimeError):
                        flow.open_cp_dialog(from_ready=True)
                flow.tap_hit.assert_called_once()
                flow.tap.assert_not_called()

    def test_cp_dialog_requires_button_and_honors_user_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            flow,state=self.dialog_flow(folder)
            flow.hit_text.return_value=None
            with self.assertRaisesRegex(RuntimeError,'按钮'):
                flow.open_cp_dialog(from_ready=True)
            flow.tap_hit.assert_not_called()
            flow,state=self.dialog_flow(folder,opens_after=99)
            flow.pause.side_effect=RuntimeError('任务已被用户停止')
            with self.assertRaisesRegex(RuntimeError,'用户停止'):
                flow.open_cp_dialog(from_ready=True)
            flow.tap_hit.assert_called_once()

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
                          ChartOptions.parse({'mode':'challenge','max_rounds':1,**values}),folder)

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
            flow.open_cp_dialog=Mock();flow.wait=Mock();flow.stable_integer=Mock(return_value=556)
            flow.tap=Mock();flow.read_integer=Mock()
            self.assertIsNone(flow.configure_cp())
            flow.tap.assert_called_once_with(510,620)
            flow.read_integer.assert_not_called()

    def test_all_cp_tiers_verified_before_dialog_acceptance(self):
        import numpy as np
        for balance in (2000,99999,100000,115272,1000000):
            for amount,y in ((200,212),(400,285),(800,358),(1600,431)):
                with self.subTest(balance=balance,amount=amount),tempfile.TemporaryDirectory() as folder:
                    flow=self.flow(folder,cp=amount)
                    flow.open_cp_dialog=Mock()
                    flow.wait=Mock();flow.wait_ready=Mock();flow.snap=Mock()
                    # Exercise both production number readers with large balances.
                    flow.text=Mock(side_effect=lambda roi: str(amount) if roi==[489,y-19,67,39]
                                   else f'{amount}消费' if roi==[1020,541,148,38] else str(balance))
                    flow.tap=Mock();flow.pink=Mock(return_value=True)
                    flow.image=np.zeros((720,1280,3),dtype=np.uint8)
                    with patch('daily_tasks.time.sleep'):
                        self.assertEqual(flow.configure_cp(),balance)
                    self.assertEqual([c.args for c in flow.tap.call_args_list],[(877,y),(770,620)])

    def test_cp_selection_does_not_recheck_unchanged_balance(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder,cp=400)
            flow.open_cp_dialog=Mock()
            flow.wait=Mock();flow.wait_ready=Mock();flow.snap=Mock()
            flow.text=Mock(side_effect=['115272','115272','400','400消费'])
            flow.tap=Mock();flow.pink=Mock(return_value=True)
            flow.image=np.zeros((720,1280,3),dtype=np.uint8)
            with patch('daily_tasks.time.sleep'):
                self.assertEqual(flow.configure_cp(),115272)
            self.assertEqual([c.args for c in flow.tap.call_args_list],[(877,285),(770,620)])

    def test_wrong_tier_does_not_accept_dialog(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.open_cp_dialog=Mock();flow.wait=Mock();flow.stable_integer=Mock(return_value=556)
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

    def test_settled_round_finishes_without_reading_cp_again_or_refilling(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            selection=ChartSelection('774','expert')
            flow.prepare_round=Mock(return_value=((selection,),[([],{})]))
            for name in ('play_chart','await_chart_result','settle_results','navigate_menu','open_page','home'):
                setattr(flow,name,Mock())
            flow.stable_integer=Mock(side_effect=AssertionError('No post-settlement CP read'))
            flow.refill_fire=Mock(side_effect=AssertionError('CP must never refill fire'))
            flow.run()
            self.assertEqual(flow.report['completed_rounds'],1)
            self.assertEqual(flow.report['status'],'max_rounds_reached')
            self.assertEqual(flow.report['rounds'][0]['status'],'finished')
            flow.play_chart.assert_called_once()
            flow.stable_integer.assert_not_called()
            flow.open_page.assert_not_called()
            flow.home.assert_called_once()
            flow.refill_fire.assert_not_called()

    def test_next_round_uses_current_balance_without_comparing_deduction(self):
        for balance,completed,status in ((355,2,'max_rounds_reached'),
                                         (756,2,'max_rounds_reached'),
                                         (199,1,'insufficient_cp')):
            with self.subTest(balance=balance),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder,max_rounds=2)
                for name in ('navigate_menu','open_page','choose_difficulty_exact','tap',
                             'play_chart','await_chart_result','settle_results','home'):
                    setattr(flow,name,Mock())
                flow.stable_integer=Mock(side_effect=[556,balance])
                flow.select_event_song=Mock(return_value=ChartSelection('774','expert').song)
                flow.store.get=Mock(return_value=([],{}))
                flow.configure_cp=Mock(side_effect=[556,balance])
                flow.run()
                self.assertEqual(flow.report['completed_rounds'],completed)
                self.assertEqual(flow.report['status'],status)
                self.assertEqual(flow.play_chart.call_count,completed)
                self.assertTrue(all(row['status']=='finished' for row in flow.report['rounds']))
                flow.home.assert_called_once()

    def test_unconfirmed_result_or_failed_settlement_does_not_count_round(self):
        for failed_step in ('await_chart_result','settle_results'):
            with self.subTest(failed_step=failed_step),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder)
                selection=ChartSelection('774','expert')
                flow.prepare_round=Mock(return_value=((selection,),[([],{})]))
                for name in ('play_chart','await_chart_result','settle_results','navigate_menu','home'):
                    setattr(flow,name,Mock())
                getattr(flow,failed_step).side_effect=RuntimeError('settlement incomplete')
                with self.assertRaisesRegex(RuntimeError,'settlement incomplete'):
                    flow.run()
                self.assertEqual(flow.report['completed_rounds'],0)
                self.assertEqual(flow.report['rounds'][0]['status'],'running')
                flow.play_chart.assert_called_once()
                flow.home.assert_not_called()

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
            flow.navigate_menu=Mock();flow.open_page=Mock();flow.configure_fever=Mock()
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
                for name in ('select','dialog','ready','menu','achievement_animation','achievement_receipt','achievement_summary',
                             'ready_cp_timeout','select_cp_timeout')}
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
                        for name,from_ready in (('ready_cp_timeout',True),('select_cp_timeout',False)):
                            current[0]=frames[name]
                            elapsed=[0.]
                            def click(hit):
                                x,y,w,h=hit.box
                                case.assertTrue(796<=x<x+w<=918 if from_ready else 950<=x<x+w<=1195,hit.box)
                                case.assertGreaterEqual(y,615)
                                # Reproduce a lost first click on the actual failed frame.
                                if flow.tap_hit.call_count==2: current[0]=frames['dialog']
                            flow.tap_hit=Mock(side_effect=click)
                            flow.pause=Mock(side_effect=lambda seconds:elapsed.__setitem__(0,elapsed[0]+seconds))
                            with patch('cp_live.time.monotonic',side_effect=lambda:elapsed[0]):
                                flow.open_cp_dialog(from_ready)
                            case.assertEqual(flow.tap_hit.call_count,2)
                            case.assertTrue(flow.reco('CP_Dialog'))
                        return True
                    except Exception as exc:
                        observed['error']=repr(exc);return False
            resource.register_custom_action('CPCheck',Check())
            job=tasker.post_task('CPCheck',{'CPCheck':{'action':'Custom','custom_action':'CPCheck'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__': unittest.main()
