import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from chart_policy import ChartOptions, ChartSelection
from online_policy import RoomClock, RoomInterrupted, retry_delay, final_selection, final_song
from online_live import OnlineLiveFlow
from song_catalog import BY_ID
from chart_worker import start_authorized_stage
from chart_store import ChartStore


class OnlinePolicyTests(unittest.TestCase):
    def test_team_interface_has_difficulty_but_no_room_or_song_picker(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
        from update_chart_interface import update
        interface=update({'task':[],'option':{}})
        cases=interface['option']['谱面演出模式']['cases']
        team=next(c for c in cases if c['name']=='团队演出')
        self.assertEqual(team['option'],['谱面联网难度'])
        self.assertNotIn('协力演出',[c['name'] for c in cases])

    def test_prefetch_covers_expert_and_only_available_special(self):
        catalog={'1':{'id':'1','difficulties':{'expert':{'available':True},'special':{'available':True}}},
                 '2':{'id':'2','difficulties':{'expert':{'available':True},'special':{'available':False}}}}
        store=ChartStore()
        store.get=Mock(side_effect=lambda selection,check,timeout:([],{'path':selection.song_id}))
        progress=Mock()
        with patch('song_catalog.BY_ID',catalog):
            result=store.prepare_online('special',lambda:None,progress)
        self.assertEqual(set(result),{('1','expert'),('1','special'),('2','expert')})
        progress.assert_any_call(3,3)

    def test_online_worker_never_clicks_single_player_start(self):
        controller=Mock()
        start_authorized_stage(controller,'online')
        controller.post_click.assert_not_called()
        start_authorized_stage(controller,'click')
        controller.post_click.assert_called_once_with(1130,616)

    def test_team_ignores_stale_single_player_song_choices(self):
        options=ChartOptions.parse({'mode':'team','song1':'invalid old choice','difficulty1':'special'})
        self.assertEqual(options.selections,())
        self.assertEqual(options.difficulties,('special',))
        self.assertEqual(options.songs_per_round,1)
        self.assertEqual(options.fire,1)
        self.assertEqual(options.shortage,'stop')
        self.assertEqual(options.max_rounds,1)
        self.assertIsNone(ChartOptions.parse({'mode':'team','max_rounds':''}).max_rounds)

    def test_coop_is_pending_not_exposed_as_working_mode(self):
        with self.assertRaises(ValueError):
            ChartOptions.parse({'mode':'coop'})

    def test_sp_falls_back_only_when_not_visible(self):
        song=next(s for s in BY_ID.values() if s['difficulties'].get('special',{}).get('available'))
        self.assertEqual(final_selection(song,'special',False).difficulty,'expert')
        self.assertEqual(final_selection(song,'special',True).difficulty,'special')
        self.assertEqual(final_selection(song,'hard',False).difficulty,'hard')

    def test_final_song_uses_actual_title_and_rejects_unknown(self):
        self.assertEqual(final_song('－HEROIC ADVENT－')['title'],'-HEROIC ADVENT-')
        with self.assertRaises(ValueError):
            final_song('this is not a song')
        self.assertEqual(final_song('魔法少女とチョコレト')['id'],'564')
        with self.assertRaises(ValueError):
            final_song('魔法少女とチ')
        self.assertEqual(final_song('金色ヘのプレリュード')['title'],'金色へのプレリュード')

    def test_ambiguous_song_requires_disambiguation(self):
        songs={str(i):{'id':str(i),'title':'same title','aliases':[],
                      'band':band,'band_aliases':[]} for i,band in enumerate(('A','B'))}
        with patch('online_policy.BY_ID',songs):
            with self.assertRaises(ValueError): final_song('same title')
            self.assertEqual(final_song('same title','B')['id'],'1')

    def test_room_timeout_is_absolute_until_actual_stage(self):
        clock=RoomClock(10)
        self.assertFalse(clock.expired(189.999))
        self.assertTrue(clock.expired(190))
        clock.started=True
        self.assertFalse(clock.expired(1000))
        self.assertEqual([retry_delay(i) for i in range(1,8)],[3,6,12,24,30,30,30])


class OnlineFlowTests(unittest.TestCase):
    def test_confirmed_result_can_resume_cleanup_without_recounting(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.report['completed_rounds']=1
            flow.snap=Mock()
            flow.result_modals=Mock(return_value=False)
            flow.dismiss_talk=Mock(return_value=False)
            flow.reco=Mock(side_effect=lambda node: node=='OL_TeamHome')
            flow.hit_text=Mock(return_value=None)
            OnlineLiveFlow.settle_online(flow,{'counted':True})
            self.assertEqual(flow.report['completed_rounds'],1)

    def test_rank_modal_precedes_visible_background_next_button(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.snap=Mock()
            flow.result_modals=Mock(return_value=False)
            flow.dismiss_talk=Mock(return_value=False)
            flow.reco=Mock(side_effect=lambda node: node=='LV_RankUp')
            flow.hit_text=Mock(return_value=SimpleNamespace(box=[1000,630,150,50]))
            flow.tap_hit=Mock(side_effect=AssertionError('Clicked background control'))
            flow.tap=Mock(side_effect=RuntimeError('modal handled'))
            with self.assertRaisesRegex(RuntimeError,'modal handled'):
                OnlineLiveFlow.settle_online(flow,{})
            flow.tap.assert_called_once_with(640,526)
            flow.tap_hit.assert_not_called()
            flow.hit_text.assert_not_called()

    def test_title_waits_for_complete_repeated_match_without_clicking(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.snap=Mock()
            flow.reco=Mock(return_value=True)
            flow.text=Mock(side_effect=['魔法少女とチ','魔法少女とチョコレト','魔法少女とチョコレト'])
            flow.quick_tap=Mock()
            self.assertEqual(flow.read_final_song()['id'],'564')
            flow.quick_tap.assert_not_called()
            self.assertEqual(flow.snap.call_count,3)

    def test_title_read_stops_when_final_page_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.snap=Mock()
            flow.reco=Mock(side_effect=[True,False])
            flow.text=Mock(return_value='魔法少女とチ')
            with self.assertRaisesRegex(RuntimeError,'页面已离开'):
                flow.read_final_song()
            flow.text.assert_called_once()

    def make_flow(self,folder):
        flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                            ChartOptions.parse({'mode':'team','fire':0}),folder)
        selection=ChartSelection.parse('306','expert')
        flow.store.prepare_online=Mock(return_value={('306','expert'):{'duration':1}})
        for name in ('prepare_settings','await_final','save_frame','settle_online','home','recover_room','pause'):
            setattr(flow,name,Mock())
        flow.read_final_selection=Mock(return_value=selection)
        def join():
            flow.report['attempts']+=1
            flow.current_attempt={'songs':[]}
            flow.report['rounds'].append(flow.current_attempt)
            flow.room_active=True
            return True
        flow.join_room=Mock(side_effect=join)
        flow.play_chart=Mock()
        return flow

    def test_exit_from_each_phase_retries_without_counting(self):
        for phase in ('await_final','read_final_selection','play_chart','settle_online'):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory() as folder:
                flow=self.make_flow(folder)
                success=flow.read_final_selection.return_value if phase=='read_final_selection' else None
                getattr(flow,phase).side_effect=[RoomInterrupted('returned home'),success]
                flow.run()
                self.assertEqual(flow.report['attempts'],2)
                self.assertEqual(flow.report['completed_rounds'],1)
                self.assertEqual(flow.report['rounds'][0]['status'],'interrupted')
                flow.recover_room.assert_called_once()
                flow.pause.assert_called_once_with(3)

    def test_three_minute_room_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.await_final.side_effect=[RoomInterrupted('房间3分钟未开演'),None]
            flow.run()
            self.assertEqual(flow.report['completed_rounds'],1)
            self.assertEqual(flow.report['retries'][0]['reason'],'房间3分钟未开演')

    def test_result_is_counted_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            row={}
            flow.count_result(row)
            flow.count_result(row)
            self.assertEqual(flow.report['completed_rounds'],1)

    def test_disconnect_modal_precedes_background_home_and_confirms_interrupt(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.foreground_package=Mock(return_value='com.bilibili.star.bili')
            flow.snap=Mock()
            phase=[0]
            flow.reco=Mock(side_effect=lambda node: (
                node=='CU_HomeBand' or
                node=='OL_Disconnected' and phase[0]==0 or
                node=='OL_LeaveConfirm' and phase[0]==1))
            buttons=[object(),object()]
            def hit(roi,pattern):
                if phase[0]==0 and pattern=='^中断$': return buttons[0]
                if phase[0]==1 and '^中断$' in pattern: return buttons[1]
            flow.hit_text=Mock(side_effect=hit)
            flow.tap_hit=Mock(side_effect=lambda button: phase.__setitem__(0,phase[0]+1))
            flow.back=Mock()
            OnlineLiveFlow.recover_room(flow)
            self.assertEqual([call.args[0] for call in flow.tap_hit.call_args_list],buttons)
            flow.back.assert_not_called()

    def test_matching_with_disabled_back_uses_background_once(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.foreground_package=Mock(return_value='com.bilibili.star.bili')
            flow.snap=Mock()
            phase=[0]
            flow.reco=Mock(side_effect=lambda node: (
                node in ('OL_Matching','CU_Back') if phase[0]==0 else node=='CU_HomeBand'))
            flow.hit_text=Mock(return_value=None)
            flow.back=Mock()
            flow.background_room=Mock(side_effect=lambda:phase.__setitem__(0,1))
            OnlineLiveFlow.recover_room(flow)
            flow.back.assert_called_once()
            flow.background_room.assert_called_once()

    def test_real_errors_and_user_stop_are_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.play_chart.side_effect=RuntimeError('sync error or user stop')
            with self.assertRaises(RuntimeError): flow.run()
            flow.recover_room.assert_not_called()
            self.assertEqual(flow.report['completed_rounds'],0)

    def test_cache_failure_prevents_room_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.store.prepare_online.side_effect=ValueError('invalid chart')
            with self.assertRaises(ValueError): flow.run()
            flow.join_room.assert_not_called()

    def test_unrecognized_song_leaves_room_before_stopping(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.reco=Mock(return_value=None)
            flow.read_final_selection.side_effect=ValueError('unknown title')
            with self.assertRaisesRegex(ValueError,'unknown title'):
                flow.run()
            flow.recover_room.assert_called_once()
            flow.play_chart.assert_not_called()
            self.assertEqual(flow.report['completed_rounds'],0)

    def test_background_app_stops_room_before_any_ui_action(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.foreground_package=Mock(return_value='com.mumu.launcher')
            flow.reco=Mock()
            with self.assertRaisesRegex(RoomInterrupted,'后台'):
                flow.guard_room()
            flow.reco.assert_not_called()

    def test_guard_keeps_absolute_timeout_across_room_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.foreground_package=Mock(return_value='com.bilibili.star.bili')
            flow.reco=Mock(return_value=None)
            flow.room_clock=RoomClock(10)
            with patch('online_live.time.monotonic',return_value=190):
                with self.assertRaisesRegex(RoomInterrupted,'3分钟'):
                    flow.guard_room()
            flow.room_clock.started=True
            with patch('online_live.time.monotonic',return_value=600):
                flow.guard_room()


if __name__=='__main__':
    unittest.main()
