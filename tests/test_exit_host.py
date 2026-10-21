"""Exit scope/target regressions. No real processes or emulators are closed."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'agent'))
from exit_host import ExitFlow
from wakeup_host import WakeupError


class ExitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cli, self.adb = self.root/'mumu-cli.exe', self.root/'adb.exe'
        self.cli.touch()
        self.adb.touch()
        self.values = dict(emulator='mumu', emulator_path=str(self.cli), instance='0', scope='instance')

    def vm(self, index='0', running=True):
        return dict(index=index, is_process_started=running, is_android_started=running,
                    adb_host_ip='127.0.0.1', adb_port=16384+32*int(index))

    def flow(self, **kwargs):
        return ExitFlow(self.values | kwargs, self.root/'records')

    def test_instance_closes_exact_index_and_leaves_other_instance(self):
        flow = self.flow(instance='1')
        answers = [json.dumps(self.vm('1')), '', json.dumps({'0': self.vm(), '1': self.vm('1', False)})]
        with patch('exit_host.command', side_effect=answers) as run:
            flow.run()
        self.assertEqual(run.call_args_list[1].args[0], [self.cli, 'control', '--vmindex', '1', 'shutdown'])
        self.assertEqual(flow.report['status'], 'finished')
        self.assertFalse(any('launch' in call.args[0] or 'all' in call.args[0] and 'control' in call.args[0] for call in run.call_args_list))
        self.assertTrue((flow.output/'trace.jsonl').is_file())

    def test_already_closed_is_success_without_adb_or_launch(self):
        for scope in ('app', 'instance'):
            flow = self.flow(scope=scope)
            with patch('exit_host.command', return_value=json.dumps(self.vm(running=False))) as run:
                flow.run()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(flow.report['status'], 'finished')
            self.assertTrue(flow.report['already_stopped'])

    def test_address_mismatch_is_error_before_close(self):
        flow = self.flow(address='127.0.0.1:16416')
        with patch('exit_host.command', return_value=json.dumps(self.vm())) as run:
            with self.assertRaisesRegex(WakeupError, '不符'):
                flow.run()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads((flow.output/'report.json').read_text(encoding='utf-8'))['status'], 'error')

    def test_current_device_is_matched_to_actual_index(self):
        states = {'0': self.vm(), '1': self.vm('1')}
        stopped = {'0': self.vm(), '1': self.vm('1', False)}
        flow = self.flow(emulator='none', address='127.0.0.1:16416')
        with patch('exit_host.command', side_effect=[json.dumps(states), '', json.dumps(stopped)]) as run:
            flow.run()
        self.assertEqual(run.call_args_list[1].args[0][-3:], ['--vmindex', '1', 'shutdown'])

    def test_unknown_or_ambiguous_current_device_does_not_close(self):
        for states in ({'0': self.vm()}, {'0': self.vm(), '1': self.vm()}):
            flow = self.flow(emulator='none', address='127.0.0.1:16416' if len(states)==1 else '127.0.0.1:16384')
            with patch('exit_host.command', return_value=json.dumps(states)) as run:
                with self.assertRaisesRegex(WakeupError, '唯一'):
                    flow.run()
            self.assertEqual(run.call_count, 1)

    def test_program_scope_stops_running_instances_then_closes_main(self):
        states = {'0': self.vm(), '1': self.vm('1'), '2': self.vm('2', False)}
        off = {i: self.vm(i, False) for i in states}
        flow = self.flow(scope='program')
        with patch('exit_host.command', side_effect=[json.dumps(self.vm()), json.dumps(states), '', '', json.dumps(off), '']) as run:
            flow.run()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[2:4], [[self.cli, 'control', '--vmindex', i, 'shutdown'] for i in ('0', '1')])
        self.assertEqual(commands[-1], [self.cli, 'main', 'close'])
        self.assertEqual(flow.report['instances'], ['0', '1'])

    def test_app_force_stop_is_user_specific_and_verified(self):
        flow = self.flow(scope='app', user='10')
        answers = [json.dumps(self.vm()), 'connected', 'device', 'UserInfo{0:Owner:13} running\nUserInfo{10:Clone:10} running',
                   'package:com.bilibili.star.bili', '', 'User 0: stopped=false\nUser 10: installed=true stopped=true']
        with patch('exit_host.command', side_effect=answers) as run:
            flow.run()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[-2], [str(self.adb), '-s', '127.0.0.1:16384', 'shell', 'am force-stop --user 10 com.bilibili.star.bili'])
        self.assertEqual(flow.report['user'], 10)
        self.assertFalse(any('start-user' in str(cmd) or 'shutdown' in cmd for cmd in commands))

    def test_absent_clone_does_not_stop_original(self):
        flow = self.flow(scope='app', user='10')
        with patch('exit_host.command', side_effect=[json.dumps(self.vm()), '', 'device', 'UserInfo{0:Owner:13} running']) as run:
            with self.assertRaisesRegex(WakeupError, '用户 10 不存在'):
                flow.run()
        self.assertFalse(any('force-stop' in str(call.args[0]) for call in run.call_args_list))

    def test_generic_adb_application_needs_no_mumu(self):
        flow = self.flow(scope='app', emulator='none', emulator_path='', adb_path=str(self.adb), address='emulator-5554')
        answers = ['device', 'UserInfo{0:Owner:13} running', 'package:com.bilibili.star.bili', '', 'User 0: stopped=true']
        with patch('exit_host.command', side_effect=answers) as run:
            flow.run()
        self.assertTrue(all(call.args[0][:3] == [str(self.adb), '-s', 'emulator-5554'] for call in run.call_args_list))
        self.assertEqual(flow.report['status'], 'finished')

    def test_program_does_not_exit_main_if_instance_shutdown_fails(self):
        states = {'0': self.vm(), '1': self.vm('1')}
        flow = self.flow(scope='program')
        with patch('exit_host.command', side_effect=[json.dumps(self.vm()), json.dumps(states), WakeupError('shutdown failed')]) as run:
            with self.assertRaisesRegex(WakeupError, 'shutdown failed'):
                flow.run()
        self.assertFalse(any('main' in call.args[0] for call in run.call_args_list))
        self.assertEqual(flow.report['status'], 'error')

    def test_unconfirmed_app_close_is_not_reported_as_success(self):
        flow = self.flow(scope='app')
        answers = [json.dumps(self.vm()), '', 'device', 'UserInfo{0:Owner:13} running',
                   'package:com.bilibili.star.bili', '', 'User 0: stopped=false']
        with patch('exit_host.command', side_effect=answers):
            with self.assertRaisesRegex(WakeupError, '未确认'):
                flow.run()
        self.assertEqual(flow.report['status'], 'error')

    def test_invalid_or_missing_targets_never_close(self):
        for options in (dict(scope='everything'), dict(emulator='custom'), dict(instance='all'), dict(address='-s bad')):
            with patch('exit_host.command') as run:
                with self.assertRaises(WakeupError):
                    self.flow(**options).run()
            run.assert_not_called()

    def test_cancellation_before_command_preserves_error_record(self):
        flow = self.flow()
        with patch('exit_host.command', side_effect=InterruptedError('cancelled')) as run:
            with self.assertRaises(InterruptedError):
                flow.run()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(flow.report['status'], 'error')


if __name__ == '__main__':
    unittest.main()
