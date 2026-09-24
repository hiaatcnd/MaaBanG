import json
from pathlib import Path
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions, ChartSelection, JITTER_PROFILES
from chart_live import ChartLiveFlow, title_key
from chart_store import ChartStore
from chart_timing import compile_chart
from song_catalog import BY_ID, available_difficulties


class ChartLiveTests(unittest.TestCase):
    def test_rewards_page_confirms_result_before_settlement_navigation(self):
        with tempfile.TemporaryDirectory() as folder:
            f=ChartLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                            ChartOptions.parse({}),folder)
            f.snap=Mock();f.pause=Mock();f.result_modals=Mock(return_value=False)
            f.reco=Mock(side_effect=lambda node:node=='LV_Rewards')
            f.hit_text=Mock(side_effect=lambda roi,pattern:object() if pattern=='^下一步$' else None)
            f.save_frame=Mock();f.tap_hit=Mock();row={}
            f.await_chart_result(1,row)
            self.assertEqual(row['status'],'result_confirmed')
            self.assertEqual(row['result_evidence'],'rewards_page')
            f.tap_hit.assert_not_called()

    def test_story_unlock_modal_requires_known_header_and_own_confirm(self):
        import numpy as np
        for header in ('解锁活动故事','解锁主线故事','无关弹窗'):
            with self.subTest(header=header),tempfile.TemporaryDirectory() as folder:
                flow=ChartLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                                   ChartOptions.parse({}),folder)
                button=SimpleNamespace(box=[521,550,236,64])
                flow.image=np.full((720,1280,3),90,dtype=np.uint8)
                flow.image[100:640,333:947]=255
                flow.image[175:178,365:915]=[140,50,255]
                button=SimpleNamespace(text='确定',box=[600,550,80,35])
                flow.ocr=Mock(return_value=[SimpleNamespace(text=header,box=[390,135,230,35]),button])
                flow.reco=Mock(return_value=False)
                flow.snap=Mock(side_effect=lambda:flow.image.fill(0))
                flow.tap_hit=Mock()
                with patch('notifications.time.sleep'):
                    self.assertEqual(flow.result_modals(),header!='无关弹窗')
                self.assertEqual(flow.tap_hit.call_count,int(header!='无关弹窗'))

    def test_nonzero_profiles_and_invalid_options(self):
        self.assertTrue(all(t>0 and p>0 for t,p in JITTER_PROFILES.values()))
        for data in ({'jitter':'none'},{'fire':4},{'max_rounds':0},{'shortage':'stars'},
                     {'song1':'EXIST','difficulty1':'special'}):
            with self.assertRaises(ValueError):ChartOptions.parse(data)

    def test_independent_tour_choices_and_round_limits(self):
        options=ChartOptions.parse({'mode':'tour_free','song1':'306','song2':'304','song3':'359',
                                   'difficulty1':'easy','difficulty2':'normal','difficulty3':'special',
                                   'max_rounds':2})
        self.assertEqual([(s.song_id,s.difficulty) for s in options.selections],
                         [('306','easy'),('304','normal'),('359','special')])
        self.assertEqual(options.songs_per_round,3)
        self.assertEqual(ChartOptions.parse({'mode':'tour_fixed'}).selections,())
        self.assertIsNone(ChartOptions.parse({'max_rounds':''}).max_rounds)

    def test_every_song_and_difficulty_is_exposed_in_each_slot(self):
        interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        for slot in range(1,4):
            cases=interface['option'][f'谱面第{slot}首歌曲']['cases']
            self.assertEqual(len(cases),len(BY_ID))
            ids=set()
            for case in cases:
                sid=case['pipeline_override'][f'CL_song{slot}']['attach']['value']
                ids.add(sid)
                diff=interface['option'][case['option'][0]]['cases']
                names={c['pipeline_override'][f'CL_difficulty{slot}']['attach']['value'] for c in diff}
                self.assertEqual(names,set(available_difficulties(BY_ID[sid])))
            self.assertEqual(ids,set(BY_ID))

    def test_large_jitter_preserves_contacts_and_moves_whole_hold(self):
        chart=[{'type':'BPM','beat':0,'bpm':200},
               {'type':'Long','connections':[{'beat':1,'lane':1},{'beat':8,'lane':1}]}]
        chart.extend({'type':'Single','beat':i*.2,'lane':5} for i in range(1,70))
        for profile,(jitter,position) in JITTER_PROFILES.items():
            for seed in range(5):
                active=set()
                events=compile_chart(chart,seed=seed,jitter_ms=jitter,position_jitter=position)
                for e in events:
                    if e.action=='down':
                        self.assertNotIn(e.contact,active);active.add(e.contact)
                    else:
                        self.assertIn(e.contact,active)
                        if e.action=='up':active.remove(e.contact)
                self.assertFalse(active,profile)
                down=next(e for e in events if e.lane==1 and e.action=='down')
                up=next(e for e in events if e.contact==down.contact and e.action=='up' and e.time>down.time)
                self.assertAlmostEqual(up.time-down.time,2.108)

    def test_cache_must_match_catalog_note_count(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'306_easy.json').write_text(json.dumps([
                {'type':'BPM','beat':0,'bpm':120},{'type':'Single','beat':1,'lane':1}]))
            with self.assertRaisesRegex(ValueError,'音符数'):
                ChartStore(folder).get(ChartSelection('306','easy'))

    def test_tour_ocr_small_kana_and_wave_dash(self):
        self.assertEqual(title_key('きゆ～まい*flower'),title_key('きゅ〜まい＊flower'))

    def make_flow(self, folder, **values):
        f=ChartLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                        ChartOptions.parse(values),folder)
        for name in ('configure_stage','wait_ready','configure_fire','settle_results','home','navigate_menu'):
            setattr(f,name,Mock())
        selections=f.settings.selections
        f.prepare_round=Mock(return_value=(selections,[(None,{'notes':1}) for _ in selections]))
        f.refill_fire=Mock(return_value=True)
        f.play_chart=Mock();f.await_chart_result=Mock()
        return f

    def test_completed_rounds_count_full_tours_and_do_not_read_auto_quota(self):
        with tempfile.TemporaryDirectory() as folder:
            f=self.make_flow(folder,mode='tour_free',max_rounds=2,fire=1)
            f.remaining=Mock(side_effect=AssertionError('must not use built-in auto quota'))
            f.run()
            self.assertEqual(f.play_chart.call_count,6)
            self.assertEqual(f.report['completed_rounds'],2)
            self.assertEqual([c.args[0] for c in f.play_chart.call_args_list],[1,2,3,1,2,3])
            f.remaining.assert_not_called()
            f.home.assert_called_once()
            f.configure_stage.assert_called_once()

    def test_insufficient_fire_does_not_start_or_change_fire_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            f=self.make_flow(folder,max_rounds=1)
            f.refill_fire.return_value=False
            f.run()
            self.assertEqual(f.report['status'],'insufficient_fire')
            f.play_chart.assert_not_called();f.configure_fire.assert_not_called()

    def test_uncertain_result_stops_without_restarting(self):
        with tempfile.TemporaryDirectory() as folder:
            f=self.make_flow(folder,max_rounds=2)
            f.await_chart_result.side_effect=RuntimeError('unknown result')
            with self.assertRaises(RuntimeError):f.run()
            f.play_chart.assert_called_once()
            self.assertEqual(f.report['completed_rounds'],0)

    def test_item_refill_confirms_once_and_closes_success_dialog(self):
        with tempfile.TemporaryDirectory() as folder:
            f=ChartLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                            ChartOptions.parse({'shortage':'items'}),folder)
            f.snap=Mock();f.tap=Mock();f.tap_hit=Mock();f.pause=Mock()
            f.fire_balance=Mock(side_effect=[14,15])
            f.hit_text=Mock(return_value=object())
            f.read_integer=Mock(side_effect=[14,14,5462,425,15,14])
            f.text=Mock(side_effect=['将要回复LIVE BOOST。 确认吗？','LIVE BOOST已回复1！'])
            self.assertTrue(f.refill_fire(15))
            self.assertEqual([c.args for c in f.tap.call_args_list],[(1150,39),(766,227),(770,602)])
            self.assertEqual(f.tap_hit.call_count,3)  # item entry, final confirm, result close
            row=f.report['refills'][0]
            self.assertEqual(row['actual_recovered'],1)
            self.assertEqual(row['items'][0]['inventory_before'],5462)
            self.assertEqual(row['status'],'confirmed')

    def test_refill_rejects_missing_final_confirmation_without_spending(self):
        with tempfile.TemporaryDirectory() as folder:
            f=ChartLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                            ChartOptions.parse({'shortage':'items'}),folder)
            f.snap=Mock();f.tap=Mock();f.tap_hit=Mock()
            f.fire_balance=Mock(return_value=14)
            f.hit_text=Mock(return_value=object())
            f.read_integer=Mock(side_effect=[14,14,5462,425,15,14])
            f.text=Mock(return_value='unexpected popup')
            with self.assertRaisesRegex(RuntimeError,'确认框'):
                f.refill_fire(15)
            self.assertEqual(f.tap_hit.call_count,1)
            self.assertEqual(f.report['refills'][0]['status'],'confirmation_pending')

    def test_tour_settlement_handles_three_scores_and_summary_before_home(self):
        with tempfile.TemporaryDirectory() as folder:
            f=self.make_flow(folder,mode='tour_free')
            f.settle_results=ChartLiveFlow.settle_results.__get__(f)
            scenes=iter(['score1','score2','score3','summary','reward_modal','reward','home'])
            state={}
            f.snap=Mock(side_effect=lambda:state.update(scene=next(scenes)))
            f.result_modals=Mock(side_effect=lambda:state['scene']=='reward_modal')
            f.reco=Mock(side_effect=lambda node:node in {
                'summary':{'LV_TourSummary'},'home':{'CU_HomeBand'},
            }.get(state['scene'],set()))
            button=object()
            def hit(roi,pattern):
                if roi==[680,314,170,160]:return button if state['scene'].startswith('score') else None
                if roi==[110,270,175,148]:return button if state['scene']=='reward' else None
                if roi==[940,602,274,100]:return button
            f.hit_text=Mock(side_effect=hit)
            f.tap_hit=Mock();f.save_frame=Mock();f.pause=Mock()
            f.settle_results()
            self.assertEqual(f.tap_hit.call_count,5)
            self.assertEqual(f.save_frame.call_count,3)

    def test_natural_recovery_does_not_reject_unchanged_selected_cost(self):
        with tempfile.TemporaryDirectory() as folder:
            f=self.make_flow(folder,fire=0)
            f.snap=Mock();f.pause=Mock()
            f.text=Mock(side_effect=['SAVIOR OF SONG','EXPERT'])
            f.reco=Mock(side_effect=lambda name:name=='LV_AutoOff')
            f.fire_preview=Mock(return_value=(12,12))
            f.fire_balance=Mock(return_value=13)
            self.assertEqual(f.verify_chart_start(1,ChartSelection('306','expert'),0),13)

    def test_lower_balance_or_wrong_cost_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            for balance,preview in [(11,(12,12)),(13,(12,11))]:
                f=self.make_flow(folder,fire=0)
                f.snap=Mock();f.pause=Mock()
                f.text=Mock(side_effect=['SAVIOR OF SONG','EXPERT'])
                f.reco=Mock(side_effect=lambda name:name=='LV_AutoOff')
                f.fire_preview=Mock(return_value=preview)
                f.fire_balance=Mock(return_value=balance)
                with self.assertRaisesRegex(RuntimeError,'火数预览'):
                    f.verify_chart_start(1,ChartSelection('306','expert'),0)

    def test_long_ready_title_with_real_ocr_preserves_song_and_difficulty_checks(self):
        if not all((ROOT/'assets/resource/model/ocr'/name).is_file() for name in ('det.onnx','rec.onnx','keys.txt')):
            self.skipTest('Local OCR models are required for the real-screenshot integration test')
        import numpy as np
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker

        fixture=ROOT/'tests/fixtures/mining/ready_long_title.png'
        image=np.zeros((720,1280,3),dtype=np.uint8)
        image[530:612,105:790]=np.asarray(Image.open(fixture))[:,:,::-1]
        resource=Resource()
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        class ScreenshotController(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'long-title-fixture'
            def screencap(self): return image
        controller=ScreenshotController()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker()
        tasker.bind(resource,controller)
        observed={}
        case=self
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        flow=case.make_flow(folder,fire=1)
                        flow.ctx=context
                        flow.image=image
                        flow.snap=Mock();flow.pause=Mock()
                        original_reco=flow.reco
                        flow.reco=lambda name,**kw: original_reco(name,**kw) if name=='CU_OCR' else name=='LV_AutoOff'
                        flow.fire_preview=Mock(return_value=(19,18))
                        flow.fire_balance=Mock(return_value=19)
                        # The old crop drops the final s; a prefix match must remain invalid.
                        truncated=flow.text([220,541,440,38])
                        case.assertFalse(flow.title_matches(truncated,BY_ID['25']))
                        case.assertEqual(flow.verify_chart_start(1,ChartSelection('25','easy'),1),19)
                        with case.assertRaisesRegex(RuntimeError,'歌曲与谱面不一致'):
                            flow.verify_chart_start(1,ChartSelection('24','easy'),1)
                        with case.assertRaisesRegex(RuntimeError,'难度与谱面不一致'):
                            flow.verify_chart_start(1,ChartSelection('25','expert'),1)
                        observed['title']=flow.text([220,541,570,38])
                        return True
                    except Exception as error:
                        observed['error']=repr(error)
                        return False
            resource.register_custom_action('LongTitleCheck',Check())
            job=tasker.post_task('LongTitleCheck',{'LongTitleCheck':{'action':'Custom','custom_action':'LongTitleCheck'}}).wait()
            self.assertTrue(job.succeeded,observed)
            self.assertIn('Girls',observed['title'])


if __name__=='__main__':unittest.main()
