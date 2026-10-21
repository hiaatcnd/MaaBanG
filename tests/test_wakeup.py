"""Startup policy and exact-device/user regression tests, without game input."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from wakeup_host import AndroidGame, Connection, WakeupError, bootstrap, command
from wakeup import WakeupFlow, startup_decision, RULES


def hit(text, box=(900, 620, 140, 50)):
    return SimpleNamespace(text=text, box=list(box), score=.99)


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = json.loads(RULES.read_text(encoding='utf-8'))

    def decide(self, *hits, **kwargs):
        return startup_decision(hits, self.rules, **kwargs)

    def test_event_next_does_not_depend_on_event_title_or_reward_currency(self):
        for title in ('黄金周纪念特别礼物', '周年登录奖励', '全新联动活动'):
            decision = self.decide(hit(title), hit('获得星石 500'), hit('下一步'))
            self.assertEqual(decision[:2], ('click', '活动下一步'))

    def test_multiple_buttons_and_background_next_are_not_guessed(self):
        self.assertEqual(self.decide(hit('下一步'), hit('下一步', (700, 620, 100, 40)))[0], 'unknown')
        self.assertEqual(self.decide(hit('下一步'), modal=True)[0], 'unknown')

    def test_resource_update_is_scoped_and_can_be_disabled(self):
        hits = [hit('需要下载游戏数据 250MB', (300, 200, 500, 40)), hit('确定')]
        self.assertEqual(self.decide(*hits, modal=True)[:2], ('click', '下载游戏资源'))
        self.assertEqual(self.decide(*hits, modal=True, allow_download=False)[0], 'blocked')
        self.assertEqual(self.decide(*hits, modal=False)[0], 'unknown')

    def test_transactions_and_manual_auth_are_never_accepted(self):
        for prompt in ('是否消耗星石购买下载资源', '确认购买', '付费奖励'):
            self.assertNotEqual(self.decide(hit(prompt), hit('下一步'))[0], 'click')
        for prompt in ('请输入账号密码', '服务器维护中', '前往应用商店更新客户端', '实名认证'):
            self.assertEqual(self.decide(hit(prompt), hit('确定'), modal=True)[0], 'blocked')

    def test_loading_and_unknown_pages_do_not_click(self):
        self.assertEqual(self.decide(hit('正在下载 37%'))[0], 'wait')
        self.assertEqual(self.decide(hit('未适配页面'), hit('确定'))[0], 'unknown')

    def test_unfamiliar_promotion_closes_without_following_store_link(self):
        decision = self.decide(hit('全新收藏集开售'), hit('关闭', (730, 620, 70, 40)),
                               hit('跳转', (945, 620, 70, 40)))
        self.assertEqual(decision[:2], ('click', '关闭启动页面'))
        self.assertEqual(decision[2].text, '关闭')

    def test_click_rechecks_account_even_between_periodic_checks(self):
        flow = WakeupFlow.__new__(WakeupFlow)
        flow.game = SimpleNamespace(selected_foreground=lambda: False)
        flow.record = Mock()
        with patch('wakeup.DailyFlow.tap') as click:
            with self.assertRaisesRegex(RuntimeError, '前台应用已改变'):
                flow.tap(600, 600)
        click.assert_not_called()
        flow.record.assert_not_called()


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cli = Path(self.temp.name)/'mumu-cli.exe'
        self.adb = Path(self.temp.name)/'adb.exe'
        self.cli.touch(); self.adb.touch()
        self.values = dict(emulator='mumu', emulator_path=str(self.cli), instance='1', boot_timeout=10)

    def test_mumu_stopped_instance_launches_then_resolves_actual_port(self):
        started = dict(index='1', adb_port=16416, is_process_started=True, is_android_started=True)
        responses = [json.dumps(dict(index='1', is_process_started=False)), '',
                     json.dumps(started), 'connected', '1']
        with patch('wakeup_host.command', side_effect=responses) as run:
            result = bootstrap(self.values)
        self.assertEqual(result.address, '127.0.0.1:16416')
        self.assertEqual(run.call_args_list[1].args[0][1:], ['control', '--vmindex', '1', 'launch'])
        self.assertEqual(run.call_args_list[-1].args[0][1:3], ['-s', '127.0.0.1:16416'])

    def test_mumu_explicit_mismatched_port_fails_before_launch(self):
        info = json.dumps({'1': dict(index='1', adb_port=16416, is_process_started=False)})
        with patch('wakeup_host.command', return_value=info) as run:
            with self.assertRaisesRegex(WakeupError, '不符'):
                bootstrap(self.values | {'address': '127.0.0.1:16384'})
        self.assertEqual(run.call_count, 1)

    def test_existing_emulator_never_launches_a_process(self):
        with patch('wakeup_host.command', side_effect=['connected', '1']), patch('wakeup_host.subprocess.Popen') as launch:
            result = bootstrap(dict(emulator='none', adb_path=str(self.adb), address='127.0.0.1:16384'))
        self.assertEqual(result.address, '127.0.0.1:16384')
        launch.assert_not_called()

    def test_custom_arguments_are_not_shell_code(self):
        with patch('wakeup_host.command', side_effect=['offline', 'connected', '1']), patch('wakeup_host.subprocess.Popen') as launch:
            bootstrap(dict(emulator='custom', emulator_path=str(self.cli), adb_path=str(self.adb),
                           address='127.0.0.1:5555', arguments='["--index", "2", "$(literal)"]'))
        self.assertEqual(launch.call_args.args[0], [str(self.cli), '--index', '2', '$(literal)'])
        self.assertNotIn('shell', launch.call_args.kwargs)

    def test_invalid_config_and_cancel_prevent_launch(self):
        with patch('wakeup_host.subprocess.Popen') as launch:
            for values in ({'instance':'-1'}, {'boot_timeout':'0'}, {'emulator':'bogus'}):
                with self.assertRaises(WakeupError): bootstrap(self.values | values)
            stop = Mock(side_effect=RuntimeError('stopped'))
            with self.assertRaisesRegex(RuntimeError, 'stopped'):
                command([str(self.cli)], check_stop=stop)
        launch.assert_not_called()


class AndroidUserTests(unittest.TestCase):
    def game(self, user=10):
        return AndroidGame(Connection('adb.exe', '127.0.0.1:16384'), user=str(user))

    def test_clone_launcher_and_final_foreground_are_user_specific(self):
        game = self.game()
        game.shell = Mock(side_effect=['UserInfo{0:Owner:c13}\nUserInfo{10:clone:410} running',
            'package:com.bilibili.star.bili',
            'topResumedActivity=ActivityRecord{abc u0 com.bilibili.star.bili/.MainActivity}',
            'com.bilibili.star.bili/.PermissionActivity', 'Starting: Intent {}',
            'topResumedActivity=ActivityRecord{abc u10 com.bilibili.star.bili/.MainActivity}'])
        game.launch()
        self.assertIn(('am', 'start', '--user', '10', '-a', 'android.intent.action.MAIN',
                       '-c', 'android.intent.category.LAUNCHER', '-n', 'com.bilibili.star.bili/.PermissionActivity'),
                      [call.args for call in game.shell.call_args_list])

    def test_missing_clone_does_not_launch_primary(self):
        game = self.game()
        game.shell = Mock(return_value='UserInfo{0:Owner:c13}')
        with self.assertRaisesRegex(WakeupError, '不存在'): game.launch()
        game.shell.assert_called_once_with('pm', 'list', 'users')

    def test_already_open_is_not_restarted(self):
        game = self.game()
        game.shell = Mock(side_effect=['UserInfo{10:clone:410} running', 'package:com.bilibili.star.bili',
            'mResumedActivity: ActivityRecord{abc u10 com.bilibili.star.bili/.MainActivity}'])
        game.launch()
        self.assertEqual(game.shell.call_count, 3)

    def test_stopped_clone_profile_starts_before_launching_its_activity(self):
        game = self.game()
        game.selected_foreground = Mock(side_effect=[False, True])
        game.shell = Mock(side_effect=['UserInfo{10:clone:410}', 'package:com.bilibili.star.bili',
            'Success: user started', 'UserInfo{10:clone:410} running',
            'com.bilibili.star.bili/.PermissionActivity', 'Starting: Intent {}'])
        game.launch()
        calls = [call.args for call in game.shell.call_args_list]
        self.assertEqual(calls[2], ('am', 'start-user', '10'))
        self.assertEqual(calls[-1][0:4], ('am', 'start', '--user', '10'))

    def test_wrong_user_after_am_start_is_failure(self):
        game = self.game()
        game.shell = Mock(side_effect=['UserInfo{10:clone:410} running', 'package:com.bilibili.star.bili',
                                      'com.bilibili.star.bili/.PermissionActivity', 'Starting: Intent {}'])
        game.selected_foreground = Mock(return_value=False)
        with patch('wakeup_host.pause'), self.assertRaisesRegex(WakeupError, '未确认游戏属于用户'):
            game.launch()


if __name__ == '__main__': unittest.main()
