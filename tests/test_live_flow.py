import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from auto_live import LiveFlow
from live_policy import LiveOptions
from costume_unlock import FlowError


class LiveFlowTests(unittest.TestCase):
    def flow(self, **options):
        f=LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                   LiveOptions.parse(options))
        for name in ('snap','home','navigate_menu','wait_ready','configure_fire','tap','settle_results','wait_song'):
            setattr(f,name,Mock())
        f.prepare_round=Mock(return_value='expert')
        f.remaining=Mock(return_value=10)
        f.fire_balance=Mock(return_value=25)
        f.verify_start=Mock(return_value=(10,25))
        return f

    def test_maximum_stops_after_one_free_live(self):
        f=self.flow(max_rounds='1')
        f.run()
        f.tap.assert_called_once_with(1127,626)
        f.prepare_round.assert_called_once()
        f.home.assert_called_once()
        self.assertEqual(f.report['completed_rounds'],1)
        self.assertEqual(f.report['status'],'max_rounds_reached')

    def test_tour_checks_each_song_and_counts_one_round(self):
        f=self.flow(mode='tour',max_rounds='1',fire=1)
        f.remaining=Mock(side_effect=[3,2,1])
        f.verify_start=Mock(side_effect=[(3,9),(2,8),(1,7)])
        f.run()
        self.assertEqual(f.tap.call_count,3)
        self.assertEqual([c.args[3] for c in f.verify_start.call_args_list],[3,2,1])
        self.assertEqual(f.report['completed_rounds'],1)
        self.assertEqual(len(f.report['rounds'][0]['songs']),3)

    def test_unlimited_stops_on_exhausted_auto_quota(self):
        f=self.flow(fire=0)
        f.remaining=Mock(side_effect=[1,0])
        f.verify_start=Mock(return_value=(1,0))
        f.fire_balance=Mock(return_value=0)
        f.run()
        f.tap.assert_called_once()
        self.assertEqual(f.report['status'],'insufficient_auto_lives')

    def test_tour_does_not_begin_if_quota_or_fire_insufficient(self):
        for quota,fire,status in [(2,25,'insufficient_auto_lives'),(3,8,'insufficient_fire')]:
            f=self.flow(mode='tour',fire=3)
            f.remaining=Mock(return_value=quota); f.fire_balance=Mock(return_value=fire)
            f.run()
            f.tap.assert_not_called(); f.configure_fire.assert_not_called()
            self.assertEqual(f.report['status'],status)

    def test_lower_fire_uses_available_amount_including_zero(self):
        for available in (0,1,2):
            f=self.flow(fire=3,shortage='lower',max_rounds='1')
            f.fire_balance=Mock(return_value=available)
            f.run()
            f.configure_fire.assert_called_once_with(available)
            self.assertEqual(f.report['rounds'][0]['songs'][0]['fire'],available)

    def test_uncertain_result_never_repeats_start(self):
        f=self.flow()
        f.wait_song=Mock(side_effect=FlowError('unknown result'))
        with self.assertRaises(FlowError): f.run()
        f.tap.assert_called_once()
        f.home.assert_not_called()
        self.assertEqual(f.report['rounds'][0]['songs'][0]['status'],'submitted')
        self.assertEqual(f.report['completed_rounds'],0)

    def test_tour_counter_mismatch_prevents_second_start(self):
        f=self.flow(mode='tour')
        f.remaining=Mock(side_effect=[10,10])
        with self.assertRaises(FlowError): f.run()
        f.tap.assert_called_once()
        f.settle_results.assert_not_called()

    def test_preflight_failure_does_not_click_start(self):
        f=self.flow()
        f.verify_start=Mock(side_effect=FlowError('auto off or mismatched song'))
        with self.assertRaises(FlowError): f.run()
        f.tap.assert_not_called()

    def test_rank_popup_is_closed_before_underlying_experience_page(self):
        f=self.flow()
        f.settle_results=LiveFlow.settle_results.__get__(f)
        scenes=iter(['rank','experience','home'])
        state={}
        f.snap=Mock(side_effect=lambda:state.update(scene=next(scenes)))
        f.pause=Mock()
        f.reco=Mock(side_effect=lambda node:node in {
            'rank':{'LV_RankUp','LV_Experience'},
            'experience':{'LV_Experience'},
            'home':{'CU_HomeBand'},
        }[state['scene']])
        button=object(); f.hit_text=Mock(return_value=button); f.tap_hit=Mock()
        f.settle_results()
        f.tap.assert_called_once_with(640,526)
        f.tap_hit.assert_called_once_with(button)

    def test_daily_reward_is_closed_before_underlying_rewards_page(self):
        f=self.flow()
        f.settle_results=LiveFlow.settle_results.__get__(f)
        scenes=iter(['daily','home']); state={}
        f.snap=Mock(side_effect=lambda:state.update(scene=next(scenes)))
        f.pause=Mock()
        f.reco=Mock(side_effect=lambda node:node in {
            'daily':{'LV_DailyReward','LV_Rewards'}, 'home':{'CU_HomeBand'},
        }[state['scene']])
        f.settle_results()
        f.tap.assert_called_once_with(640,544)

    def test_wait_song_polls_without_clicking_and_requires_next_tour_index(self):
        f=self.flow(mode='tour')
        f.wait_song=LiveFlow.wait_song.__get__(f)
        f.pause=Mock(); f.reco=Mock(return_value=True)
        f.tour_index=Mock(side_effect=[1,1,2])
        f.wait_song(1)
        self.assertEqual([c.args for c in f.pause.call_args_list],[(10,),(10,),(10,)])
        f.tap.assert_not_called()

    def test_stop_interrupts_wait_before_next_poll(self):
        f=self.flow(); f.ctx.tasker.stopping=True
        with self.assertRaises(FlowError): f.pause(10)
        f.snap.assert_not_called()

    def test_song_search_clears_special_filter_before_reading_favorites(self):
        f=self.flow(song='EXIST',difficulty='special')
        f.wait=Mock(); f.tap_hit=Mock()
        f.favorites=Mock(side_effect=lambda:f.tap.assert_called_once_with(1051,540))
        f.ocr=Mock(return_value=[SimpleNamespace(text='EXIST')])
        f.text=Mock(return_value='EXIST')
        f.choose_difficulty=Mock(return_value='expert')
        self.assertEqual(f.choose_song(),'expert')
        f.favorites.assert_called_once()
        self.assertEqual([c.args for c in f.tap.call_args_list],[(1051,540),(1070,648)])

    def test_ui_options_merge_independently_and_allow_blank_limit(self):
        import itertools
        import json
        import re
        from copy import deepcopy
        from auto_live import OPTION_NODES
        root=Path(__file__).resolve().parents[1]
        interface=json.loads((root/'assets/interface.json').read_text(encoding='utf-8'))
        pipeline=json.loads((root/'assets/resource/pipeline/live.json').read_text(encoding='utf-8'))
        names=('演出模式','演出歌曲','演出难度','每首消耗火数','火不足策略')
        limit=interface['option']['最大演出次数']
        self.assertEqual(limit['inputs'][0]['default'],'')
        pattern=limit['inputs'][0]['verify']
        for value in ('','1','999'): self.assertIsNotNone(re.fullmatch(pattern,value))
        for value in ('0','-1','1.5','1000'): self.assertIsNone(re.fullmatch(pattern,value))
        for cases in itertools.product(*(interface['option'][name]['cases'] for name in names)):
            nodes=deepcopy(pipeline)
            expected={}
            for case in cases:
                for name,override in case['pipeline_override'].items():
                    nodes[name].update(override)
                    expected[name]=override['attach']['value']
            for maximum in ('','2'):
                nodes['LV_MaxRounds']['attach']['value']=maximum
                values={key:nodes[node]['attach']['value'] for key,node in OPTION_NODES.items()}
                parsed=LiveOptions.parse(values)
                self.assertEqual(parsed.max_rounds,None if maximum=='' else 2)
                for key,node in OPTION_NODES.items():
                    if node in expected: self.assertEqual(getattr(parsed,key),expected[node])


if __name__=='__main__': unittest.main()
