"""Exercise the user-visible stdout path for every interface task."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from task_logging import log, finish, failure
from auto_live import AutoLive
from chart_live import ChartLive, ChartLiveFlow
from chart_policy import ChartOptions
from costume_unlock import FlowError
from direct_chart_live import DirectChartLive
from costume_unlock import UnlockDefault3DCostumes
from daily_tasks import ClaimHomeGifts, ClaimHomeMissions, ExchangeMichelle, DailyFreeRecruit
from live_presets import LivePresets
from mining import MineFullCombo, MineStories, MineChallenges
from update_songs import UpdateSongCatalog


class TaskLoggingTests(unittest.TestCase):
    def test_real_play_chart_logs_without_shadowing_worker_output(self):
        # Exercise the actual playback orchestration, not a mocked flow.run.
        # Only the game calls and external worker are replaced.
        for mode in ('free', 'team', 'coop', 'challenge'):
            for worker_ok in (True, False):
                with self.subTest(mode=mode, worker_ok=worker_ok), tempfile.TemporaryDirectory() as folder:
                    flow=SimpleNamespace(
                        settings=ChartOptions.parse({'mode':mode,'jitter':'small'}),
                        output=Path(folder), report={'completed_rounds':0,'attempts':1},
                        controller=SimpleNamespace(info={'test':True}))
                    for name in ('wait_ready','disable_mv','check_stop','save_frame','pause',
                                 'monitor_online_worker','monitor_chart_worker',
                                 'submit_online_ready','handle_online_playback_error'):
                        setattr(flow,name,Mock())
                    flow.verify_chart_start=Mock(return_value=0)
                    process=Mock(returncode=0 if worker_ok else 1)
                    process.poll.return_value=process.returncode
                    worker_streams=[]
                    def spawn(command, **kwargs):
                        destination=Path(command[-1]).parent
                        (destination/'armed').touch()
                        (destination/'playback.json').write_text(json.dumps({
                            'status':'input_complete' if worker_ok else 'error',
                            'error':'测试演奏进程失败'}),encoding='utf8')
                        kwargs['stdout'].write('worker output\n')
                        worker_streams.append(kwargs['stdout'])
                        return process
                    selection=SimpleNamespace(song={'title':'测试歌曲'},difficulty='easy')
                    row={}
                    with patch('chart_live.subprocess.Popen',side_effect=spawn), \
                            contextlib.redirect_stdout(io.StringIO()) as output:
                        if worker_ok:
                            ChartLiveFlow.play_chart(flow,1,selection,{'duration':1},0,row)
                        else:
                            with self.assertRaisesRegex(FlowError,'测试演奏进程失败'):
                                ChartLiveFlow.play_chart(flow,1,selection,{'duration':1},0,row)
                    self.assertIn('正在准备演奏进程',output.getvalue())
                    self.assertIn('已发送开演指令',output.getvalue())
                    self.assertEqual('谱面输入已完成' in output.getvalue(),worker_ok)
                    self.assertTrue(worker_streams[0].closed)
                    self.assertEqual(Path(worker_streams[0].name).read_text(encoding='utf8'),
                                     'worker output\n')
                    flow.verify_chart_start.assert_called_once_with(1,selection,0)
                    process.terminate.assert_not_called()

    def test_protocol_prefixes_each_line_and_flushes(self):
        with patch('builtins.print') as output:
            log('[任务] 第一行\n第二行\n', level='warn')
        self.assertEqual([call.args for call in output.call_args_list],
                         [('warn: [任务] 第一行',), ('warn: 第二行',)])
        self.assertTrue(all(call.kwargs == {'flush': True} for call in output.call_args_list))

    def test_shortage_and_skip_are_not_reported_as_success(self):
        for status, text in [('insufficient_fire','火不足'), ('insufficient_cp','CP 不足'),
                             ('insufficient_auto_lives','剩余次数不足'),
                             ('insufficient_kits','工具套装不足'),
                             ('no_stars_selected','未选择成员星级'),
                             ('no_categories_selected','未选择交换分类')]:
            with self.subTest(status=status), contextlib.redirect_stdout(io.StringIO()) as output:
                finish('任务', {'status':status})
                self.assertTrue(output.getvalue().startswith('warn:'))
                self.assertIn(text, output.getvalue())

    def test_direct_input_completion_does_not_claim_result_confirmation(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            finish('指定谱面直接演出', {'status':'finished', 'selection_source':'user',
                                      'completed_rounds':1})
        self.assertIn('输入完成', output.getvalue())
        self.assertIn('未检查结算', output.getvalue())
        self.assertNotIn('结果已确认', output.getvalue())

    def test_user_stop_is_a_warning(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            failure('任务', RuntimeError('已停止'), SimpleNamespace(tasker=SimpleNamespace(stopping=True)))
        self.assertTrue(output.getvalue().startswith('warn:'))
        self.assertIn('用户请求停止', output.getvalue())

    def run_action(self, action, *, fail=False, status='finished'):
        """Use real entrypoints/exception handlers; replace only game/network flows."""
        flow=SimpleNamespace(report={'status':status}, image=None)
        def execute(*args, **kwargs):
            log('[验证节点] 正在处理')
            if fail:
                raise RuntimeError('测试节点失败')
        for name in ('run','apply','gifts','missions','exchange','recruit'):
            setattr(flow,name,execute)
        ctx=SimpleNamespace(tasker=SimpleNamespace(stopping=False),
            get_node_data=lambda node: {'attach':{'value':'','kind':'all','inspect_only':True}})
        argv=SimpleNamespace(custom_action_param='{}')
        with contextlib.ExitStack() as stack:
            folder=stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(contextlib.chdir(folder))
            output=stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            if isinstance(action, ChartLive):
                stack.enter_context(patch.object(action,'create_flow',return_value=flow))
            elif isinstance(action, AutoLive):
                stack.enter_context(patch('auto_live.LiveOptions.parse'))
                stack.enter_context(patch('auto_live.LiveFlow',return_value=flow))
            elif isinstance(action, UnlockDefault3DCostumes):
                stack.enter_context(patch('costume_unlock.CostumeFlow',return_value=flow))
            elif isinstance(action, LivePresets):
                def preset_flow(context, destination):
                    destination.mkdir(parents=True,exist_ok=True)
                    return flow
                stack.enter_context(patch('live_presets.LivePresetFlow',side_effect=preset_flow))
            elif isinstance(action, (MineFullCombo, MineStories, MineChallenges)):
                stack.enter_context(patch('mining.MiningOptions.parse'))
                stack.enter_context(patch.object(action,'flow_type',return_value=flow))
            elif isinstance(action, UpdateSongCatalog):
                refresh=stack.enter_context(patch('update_songs.refresh_catalog'))
                refresh.side_effect=RuntimeError('测试节点失败') if fail else None
                refresh.return_value=({'songs':[]},{'songs':[]})
                stack.enter_context(patch('song_catalog.reload_catalog'))
            else:
                stack.enter_context(patch('daily_tasks.DailyFlow',return_value=flow))
            result=action.run(ctx,argv)
            return result,output.getvalue()

    def test_all_interface_tasks_start_finish_and_report_errors(self):
        actions={
            'AutoLive':AutoLive, 'ChartLive':ChartLive, 'DirectChartLive':DirectChartLive,
            'UnlockCostumes':UnlockDefault3DCostumes, 'LivePresets':LivePresets,
            'ClaimHomeGifts':ClaimHomeGifts, 'ClaimHomeMissions':ClaimHomeMissions,
            'ExchangeMichelle':ExchangeMichelle, 'DailyFreeRecruit':DailyFreeRecruit,
            'MineFullCombo':MineFullCombo, 'MineStories':MineStories,
            'MineChallenges':MineChallenges, 'UpdateSongCatalog':UpdateSongCatalog,
        }
        interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        self.assertEqual(set(actions), {task['entry'] for task in interface['task']})
        for name, factory in actions.items():
            for fail in (False,True):
                with self.subTest(task=name, fail=fail):
                    result,text=self.run_action(factory(),fail=fail)
                    self.assertEqual(result,not fail)
                    lines=text.splitlines()
                    self.assertTrue(lines[0].startswith('info: '),text)
                    self.assertTrue(lines[-1].startswith('error: ' if fail else 'success: '),text)
                    self.assertTrue(all(line.startswith(('info: ','success: ','warn: ','error: '))
                                        for line in lines),text)
                    if fail:
                        self.assertIn('测试节点失败',text)
                        self.assertNotIn('success:',text)

    def test_action_preserves_normal_shortage_return_but_logs_reason(self):
        result,text=self.run_action(ChartLive(),status='insufficient_fire')
        self.assertTrue(result)
        self.assertIn('warn: [Maa代打演出] 火不足',text)
        self.assertNotIn('success:',text)


if __name__ == '__main__':
    unittest.main()
