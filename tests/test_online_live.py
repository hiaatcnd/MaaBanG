import sys
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from threading import Event

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from chart_policy import ChartOptions, ChartSelection
from online_policy import RoomClock, RoomInterrupted, retry_delay, final_selection, final_song
from online_live import OnlineLiveFlow
from song_catalog import BY_ID
from chart_worker import start_authorized_stage
from chart_store import ChartStore


class OnlinePolicyTests(unittest.TestCase):
    def test_missing_chart_downloads_once_then_uses_disk_cache(self):
        chart=[{'type':'BPM','beat':0,'bpm':120},{'type':'Single','beat':1,'lane':1}]
        selection=SimpleNamespace(song_id='test',difficulty='expert',
                                  song={'difficulties':{'expert':{'notes':1}}})
        response=Mock()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps(chart).encode()
        with tempfile.TemporaryDirectory() as folder,patch('chart_store.urlopen',return_value=response) as fetch:
            store=ChartStore(folder)
            first,downloaded=store.get(selection)
            second,cached=store.get(selection)
            self.assertEqual(first,second)
            self.assertFalse(downloaded['cache_hit'])
            self.assertTrue(cached['cache_hit'])
            self.assertEqual(downloaded['sha256'],cached['sha256'])
            fetch.assert_called_once()
            self.assertTrue(fetch.call_args.args[0].full_url.endswith('/test/expert.json'))
            self.assertEqual([p.name for p in Path(folder).iterdir()],['test_expert.json'])

    def test_cancelled_chart_is_not_written_to_cache(self):
        chart=[{'type':'BPM','beat':0,'bpm':120},{'type':'Single','beat':1,'lane':1}]
        selection=SimpleNamespace(song_id='test',difficulty='expert',
                                  song={'difficulties':{'expert':{'notes':1}}})
        response=Mock()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps(chart).encode()
        check=Mock(side_effect=[None,None,None,RuntimeError('cancelled after validation')])
        with tempfile.TemporaryDirectory() as folder,patch('chart_store.urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'cancelled after validation'):
                ChartStore(folder).get(selection,check)
            self.assertEqual(list(Path(folder).iterdir()),[])

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
        flow.store.prepare_online=Mock(side_effect=AssertionError('must not prefetch the song pool'))
        flow.prepare_final_chart=Mock(return_value={'duration':1,'cache_hit':True})
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

    def test_chart_is_requested_only_after_final_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            calls=Mock()
            for name in ('join_room','await_final','read_final_selection','prepare_final_chart','play_chart'):
                calls.attach_mock(getattr(flow,name),name)
            flow.run()
            self.assertEqual([c[0] for c in calls.mock_calls],
                ['join_room','await_final','read_final_selection','prepare_final_chart','play_chart'])
            flow.prepare_final_chart.assert_called_once_with(flow.read_final_selection.return_value)
            flow.store.prepare_online.assert_not_called()

    def test_chart_failure_leaves_room_without_starting_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.reco=Mock(return_value=None)
            flow.prepare_final_chart.side_effect=ValueError('invalid chart')
            with self.assertRaises(ValueError): flow.run()
            flow.join_room.assert_called_once()
            flow.recover_room.assert_called_once()
            flow.play_chart.assert_not_called()
            self.assertEqual(flow.report['rounds'][0]['status'],'failed')
            self.assertEqual(flow.report['completed_rounds'],0)

    def test_on_demand_gets_exact_selected_chart_and_rechecks_page(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.snap=Mock()
            flow.reco=Mock(return_value=True)
            flow.store.get=Mock(return_value=([],{'path':'selected.json','cache_hit':True}))
            selection=flow.read_final_selection.return_value
            metadata=OnlineLiveFlow.prepare_final_chart(flow,selection)
            self.assertEqual(metadata['path'],'selected.json')
            self.assertEqual(flow.store.get.call_count,1)
            self.assertEqual(flow.store.get.call_args.args[0],selection)
            self.assertGreaterEqual(flow.snap.call_count,2)
            self.assertEqual(set(flow.charts),{(selection.song_id,selection.difficulty)})
            flow.store.prepare_online.assert_not_called()

    def test_on_demand_wraps_network_and_validation_errors(self):
        for error in (TimeoutError('read timed out'),ValueError('bad notes'),OSError('disk full')):
            with self.subTest(error=error),tempfile.TemporaryDirectory() as folder:
                flow=self.make_flow(folder)
                flow.snap=Mock()
                flow.reco=Mock(return_value=True)
                flow.store.get=Mock(side_effect=error)
                with self.assertRaisesRegex(RuntimeError,'最终歌曲谱面准备失败'):
                    OnlineLiveFlow.prepare_final_chart(flow,flow.read_final_selection.return_value)
                self.assertFalse(flow.charts)

    def test_on_demand_does_not_accept_chart_after_page_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.snap=Mock()
            flow.reco=Mock(side_effect=[True,False])
            flow.store.get=Mock(return_value=([],{'path':'selected.json'}))
            with self.assertRaisesRegex(RuntimeError,'页面已离开'):
                OnlineLiveFlow.prepare_final_chart(flow,flow.read_final_selection.return_value)
            self.assertFalse(flow.charts)

    def test_slow_download_does_not_block_room_exit_or_user_stop(self):
        for error in (RoomInterrupted('disconnected'),RuntimeError('user stopped')):
            with self.subTest(error=error),tempfile.TemporaryDirectory() as folder:
                flow=self.make_flow(folder)
                flow.reco=Mock(return_value=True)
                entered,release,finished=Event(),Event(),Event()
                def get(selection,check,timeout):
                    entered.set()
                    try:
                        release.wait(3)
                        check()
                        raise AssertionError('cancelled download was accepted')
                    finally:
                        finished.set()
                flow.store.get=Mock(side_effect=get)
                def snap():
                    if flow.snap.call_count>1:
                        self.assertTrue(entered.wait(1))
                        raise error
                flow.snap=Mock(side_effect=snap)
                try:
                    with self.assertRaisesRegex(type(error),str(error)):
                        OnlineLiveFlow.prepare_final_chart(flow,flow.read_final_selection.return_value)
                    self.assertFalse(finished.is_set())
                    self.assertFalse(flow.charts)
                finally:
                    release.set()
                    self.assertTrue(finished.wait(1))

    def test_download_deadline_does_not_wait_for_network_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.make_flow(folder)
            flow.snap=Mock()
            flow.reco=Mock(return_value=True)
            with patch('online_live.Thread'),patch('online_live.time.monotonic',side_effect=[0,0,13]):
                with self.assertRaisesRegex(RuntimeError,'超过12秒'):
                    OnlineLiveFlow.prepare_final_chart(flow,flow.read_final_selection.return_value)
            self.assertFalse(flow.charts)

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
