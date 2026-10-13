from interface_test_support import load_interface
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
sys.path.insert(0,str(ROOT/'tools'))
from direct_chart_live import DirectChartLive, DirectChartLiveFlow, OPTION_DEFAULTS, OPTION_NODES
from chart_policy import ChartOptions
from costume_unlock import FlowError
from update_chart_interface import update
from song_catalog import BY_ID, available_difficulties


class DirectChartLiveTests(unittest.TestCase):
    def prepare_flow(self, flow):
        flow.snap=Mock()
        flow.hit_text=Mock(return_value=object())
        for method in ('text','title_matches','select_song','navigate_menu','home',
                       'refill_fire','inherit_menu_fire','configure_stage','configure_fire','settle_results',
                       'await_chart_result'):
            setattr(flow,method,Mock(side_effect=AssertionError('direct task must not call '+method)))
        return flow

    def flow(self, folder):
        context=SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False))
        flow=DirectChartLiveFlow(context,ChartOptions.parse(
            dict(song1='773',difficulty1='easy',max_rounds=9,shortage='items')),folder)
        return self.prepare_flow(flow)

    def test_preparation_loads_exact_chart_without_song_ocr_or_navigation(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            metadata={'path':'chart','duration':100}
            flow.store.get=Mock(return_value=([],metadata))
            selections,charts=flow.prepare_round()
            self.assertEqual([(s.song_id,s.difficulty) for s in selections],[('773','easy')])
            self.assertEqual(charts,(([],metadata),))
            flow.store.get.assert_called_once_with(selections[0],check_stop=flow.check_stop)
            flow.hit_text.assert_called_once_with([1010,595,235,75],'^演出开始$')
            self.assertIsNone(flow.verify_chart_start(1,selections[0],None))
            flow.disable_mv()
            flow.text.assert_not_called()

    def test_missing_ready_button_never_loads_or_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.hit_text.return_value=None
            flow.store.get=Mock();flow.play_chart=Mock()
            with self.assertRaisesRegex(FlowError,'最后准备页'):flow.run()
            flow.store.get.assert_not_called();flow.play_chart.assert_not_called()

    def test_download_failure_and_stop_never_start(self):
        for error in (ValueError('谱面音符数与目录不符'),FlowError('任务已被用户停止')):
            with self.subTest(error=error),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder)
                flow.store.get=Mock(side_effect=error);flow.play_chart=Mock()
                with self.assertRaises(type(error)):flow.run()
                flow.play_chart.assert_not_called()
                self.assertEqual(flow.report['completed_rounds'],0)

    def test_exactly_one_song_no_repeat_refill_or_home(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=self.flow(folder)
            flow.store.get=Mock(return_value=([],{'duration':100}))
            flow.play_chart=Mock()
            flow.await_chart_result=Mock(side_effect=AssertionError('must not wait for results'))
            flow.run()
            flow.play_chart.assert_called_once()
            self.assertIsNone(flow.play_chart.call_args.args[3])
            flow.await_chart_result.assert_not_called()
            self.assertEqual(flow.report['status'],'finished')
            self.assertEqual(flow.report['completed_rounds'],1)
            self.assertEqual(flow.report['selection_source'],'user')
            self.assertEqual(flow.report['rounds'][0]['songs'][0]['status'],'input_complete')

    def test_playback_failure_or_stop_never_completes_or_retries(self):
        for error in ('演奏进程失败','任务已被用户停止'):
            with self.subTest(error=error),tempfile.TemporaryDirectory() as folder:
                flow=self.flow(folder)
                flow.store.get=Mock(return_value=([],{'duration':100}))
                flow.play_chart=Mock(side_effect=FlowError(error))
                flow.await_chart_result=Mock()
                with self.assertRaises(FlowError):flow.run()
                flow.play_chart.assert_called_once()
                flow.await_chart_result.assert_not_called()
                self.assertEqual(flow.report['completed_rounds'],0)
                self.assertNotEqual(flow.report['status'],'finished')

    def test_task_uses_independent_options_and_ignores_repeat_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            values=dict(OPTION_DEFAULTS,song1='773',difficulty1='easy',mode='team',max_rounds=99)
            flow=DirectChartLive().create_flow(SimpleNamespace(
                tasker=SimpleNamespace(controller=None,stopping=False)),values,folder)
            self.assertIsInstance(flow,DirectChartLiveFlow)
            self.assertEqual(flow.settings.mode,'free')
            self.assertEqual(flow.settings.max_rounds,1)
            self.assertEqual(flow.settings.shortage,'stop')
            self.assertEqual(flow.settings.selections[0].song_id,'773')
            with self.assertRaises(ValueError):
                DirectChartLive().create_flow(flow.ctx,dict(values,song1='not a song'),folder)

    def test_avoid_full_combo_defaults_and_strict_boolean(self):
        self.assertIs(OPTION_DEFAULTS['avoid_full_combo'],False)
        self.assertEqual(OPTION_NODES['avoid_full_combo'],'DL_avoid_full_combo')
        context=SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False))
        with tempfile.TemporaryDirectory() as folder:
            for enabled in (False,True):
                flow=DirectChartLive().create_flow(context,dict(OPTION_DEFAULTS,avoid_full_combo=enabled),folder)
                self.assertIs(flow.settings.avoid_full_combo,enabled)
                self.assertIs(flow.report['options']['avoid_full_combo'],enabled)
            for value in ('false','true',0,1,None):
                with self.subTest(value=value),self.assertRaisesRegex(ValueError,'避免 Full Combo'):
                    DirectChartLive().create_flow(context,dict(OPTION_DEFAULTS,avoid_full_combo=value),folder)

    def test_ui_avoid_full_combo_reaches_worker_without_changing_direct_task_boundaries(self):
        option=load_interface()['option']['直接演出避免FullCombo']
        self.assertEqual(option['default_case'],'关')
        self.assertEqual([c['pipeline_override']['DL_avoid_full_combo']['attach']['value']
                          for c in option['cases']],[False,True])
        pipeline=json.loads((ROOT/'assets/resource/pipeline/direct_chart_live.json').read_text(encoding='utf8'))
        for choice in ('default','legacy','关','开'):
            with self.subTest(choice=choice),tempfile.TemporaryDirectory() as folder:
                enabled=choice=='开'
                nodes=copy.deepcopy(pipeline)
                if choice=='legacy':nodes.pop('DL_avoid_full_combo')
                for case in option['cases']:
                    if case['name']==choice:nodes.update(copy.deepcopy(case['pipeline_override']))
                # Other tasks' switches must never influence the direct task.
                for prefix in ('CL','MN'):
                    nodes[prefix+'_avoid_full_combo']={'attach':{'value':not enabled}}
                context=SimpleNamespace(get_node_data=nodes.get,tasker=SimpleNamespace(
                    controller=SimpleNamespace(info={}),stopping=False))
                action=DirectChartLive()
                create_flow=action.create_flow
                flows=[];configs=[]
                def capture_flow(ctx,values,destination):
                    flow=self.prepare_flow(create_flow(ctx,values,destination))
                    flow.store.get=Mock(return_value=([],{'duration':120}))
                    flow.save_frame=Mock()
                    flows.append(flow)
                    return flow
                def worker(args,**kwargs):
                    config_path=Path(args[-1])
                    configs.append(json.loads(config_path.read_text(encoding='utf8')))
                    (config_path.parent/'armed').write_text('ready')
                    (config_path.parent/'playback.json').write_text('{"status":"input_complete"}')
                    return SimpleNamespace(poll=lambda:0,returncode=0)
                with patch('chart_live.Path',side_effect=lambda path:Path(folder) if path=='debug' else Path(path)), \
                        patch.object(action,'create_flow',side_effect=capture_flow), \
                        patch('chart_live.subprocess.Popen',side_effect=worker):
                    self.assertTrue(action.run(context,None))
                self.assertEqual(len(configs),1)
                self.assertIs(configs[0]['avoid_full_combo'],enabled)
                self.assertEqual(configs[0]['chart']['duration'],120)
                self.assertEqual(configs[0]['start_mode'],'click')
                self.assertEqual(len(flows),1)
                self.assertIs(flows[0].report['options']['avoid_full_combo'],enabled)
                self.assertEqual(flows[0].report['completed_rounds'],1)
                self.assertEqual(flows[0].report['status'],'finished')
                self.assertEqual(flows[0].report['rounds'][0]['songs'][0]['status'],'input_complete')
                flows[0].await_chart_result.assert_not_called()

    def test_direct_cli_passes_avoid_full_combo_to_independent_node(self):
        from run_chart_live import main
        for enabled in (False,True):
            with self.subTest(enabled=enabled),tempfile.TemporaryDirectory() as folder:
                resource=Mock()
                tasker=Mock()
                tasker.post_task.return_value=SimpleNamespace(done=True,succeeded=True)
                args=['run_chart_live.py','--adb','test-adb','--address','test-device','--direct']
                if enabled:args.append('--avoid-full-combo')
                with patch.object(sys,'argv',args),patch.dict('os.environ',{}), \
                        patch('run_chart_live.Path.home',return_value=Path(folder)), \
                        patch('maa.controller.AdbController'),patch('maa.resource.Resource',return_value=resource), \
                        patch('maa.tasker.Tasker',return_value=tasker),patch('maa.toolkit.Toolkit.init_option'):
                    self.assertEqual(main(),0)
                self.assertIsInstance(resource.register_custom_action.call_args.args[1],DirectChartLive)
                name,overrides=tasker.post_task.call_args.args
                self.assertEqual(name,'DirectChartLive')
                self.assertIs(overrides['DL_avoid_full_combo']['attach']['value'],enabled)
                self.assertNotIn('CL_avoid_full_combo',overrides)

    def test_generated_ui_is_idempotent_and_exposes_exact_cn_difficulties(self):
        interface=load_interface()
        from song_interface import compact_interface
        compact = compact_interface(interface)
        self.assertEqual(compact, update(json.loads(json.dumps(compact))))
        task=next(t for t in interface['task'] if t['entry']=='DirectChartLive')
        self.assertFalse(task['default_check'])
        self.assertEqual(task['option'],['直接演出歌曲','直接演出随机偏差','直接演出避免FullCombo'])
        options=interface['option']
        cases=options['直接演出歌曲']['cases']
        self.assertEqual(len(cases),len(BY_ID))
        ids=set()
        for case in cases:
            sid=case['pipeline_override']['DL_song1']['attach']['value'];ids.add(sid)
            diffs=options[case['option'][0]]['cases']
            self.assertEqual({d['pipeline_override']['DL_difficulty1']['attach']['value'] for d in diffs},
                             set(available_difficulties(BY_ID[sid])))
        self.assertEqual(ids,set(BY_ID))
        nodes=json.loads((ROOT/'assets/resource/pipeline/direct_chart_live.json').read_text(encoding='utf8'))
        self.assertEqual({key:nodes[node]['attach']['value'] for key,node in OPTION_NODES.items()},OPTION_DEFAULTS)


if __name__=='__main__':unittest.main()
