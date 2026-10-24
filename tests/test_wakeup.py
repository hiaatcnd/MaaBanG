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
from wakeup import WakeupFlow, startup_decision, RULES, _ForegroundPending


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
        flow.game = SimpleNamespace(user=0, package='com.bilibili.star.bili',
                                    foreground=lambda: (10, 'com.bilibili.star.bili'))
        flow.check_stop = Mock()
        flow.record = Mock()
        with patch('wakeup.DailyFlow.tap') as click:
            with self.assertRaisesRegex(RuntimeError, '切换了应用用户'):
                flow.tap(600, 600)
        click.assert_not_called()
        self.assertNotIn('click', [call.args[0] for call in flow.record.call_args_list])


class ForegroundTests(unittest.TestCase):
    GAME = (0, 'com.bilibili.star.bili')
    PERMISSION = (0, 'com.android.permissioncontroller')

    def setUp(self):
        self.now = 100.
        clock = patch('wakeup.time.monotonic', side_effect=lambda: self.now)
        clock.start(); self.addCleanup(clock.stop)
        waiting = patch('wakeup.pause', side_effect=self.pause)
        self.pause_mock = waiting.start(); self.addCleanup(waiting.stop)
        self.flow = WakeupFlow.__new__(WakeupFlow)
        self.flow.game = AndroidGame(Connection('unused', 'fixture'))
        self.flow.game.foreground = Mock(return_value=self.GAME)
        self.flow.game.launch = Mock()
        self.flow.check_stop = Mock()
        self.flow.record = Mock()
        self.flow.foreground_wait_started = None
        self.flow.recorded = 1

    def pause(self, seconds, check_stop):
        check_stop()
        self.now += seconds

    def prepare_run(self, hits):
        flow = self.flow
        flow.timeout = 30
        flow.unknown_timeout = 5
        flow.allow_download = True
        flow.rules = json.loads(RULES.read_text(encoding='utf-8'))
        flow.report = dict(status='running', steps=[])
        flow.image = None
        flow.snap = Mock()
        flow.ocr = Mock(side_effect=hits)
        flow.clear_home = Mock(return_value=True)
        flow.save_frame = Mock()
        flow.save_report = Mock()
        return flow

    def test_recorded_permission_transition_after_start_reaches_home(self):
        # 2026-10-23: permission activity resumed at 00:05:45.970,
        # game resumed at 00:05:46.092; the old guard failed in between.
        flow = self.prepare_run([[hit('TAP TO START', (550, 622, 176, 27))], [], [], []])
        flow.game.foreground.side_effect = [self.GAME, self.GAME, self.PERMISSION,
                                            None, self.GAME, self.GAME, self.GAME, self.GAME]
        with patch('wakeup.DailyFlow.tap') as click:
            flow.run()
        click.assert_called_once_with(638, 635)
        self.assertEqual(flow.report['status'], 'finished')
        self.assertEqual(len(flow.report['steps']), 1)
        self.assertEqual(flow.snap.call_count, 4)
        self.assertIn('foreground_recovered', [c.args[0] for c in flow.record.call_args_list])
        flow.game.launch.assert_called_once()

    def test_transition_before_click_discards_stale_button(self):
        flow = self.prepare_run([[hit('TAP TO START', (550, 622, 176, 27))], [], [], []])
        flow.game.foreground.side_effect = [self.GAME, self.PERMISSION,
                                            self.GAME, self.GAME, self.GAME, self.GAME]
        with patch('wakeup.DailyFlow.tap') as click:
            flow.run()
        click.assert_not_called()
        self.assertEqual(flow.report, dict(status='finished', steps=[]))
        self.assertEqual(flow.snap.call_count, 4)

    def test_permission_at_home_confirmation_requires_three_fresh_frames(self):
        flow = self.prepare_run([[]]*6)
        flow.game.foreground.side_effect = [self.GAME]*3 + [self.PERMISSION] + [self.GAME]*4
        flow.run()
        self.assertEqual(flow.report['status'], 'finished')
        self.assertEqual(flow.clear_home.call_count, 6)

    def test_other_apps_and_users_stop_immediately_without_input(self):
        for foreground in ((0, 'com.android.launcher3'), (10, self.GAME[1]),
                           (10, self.PERMISSION[1]), (0, 'com.android.settings')):
            with self.subTest(foreground=foreground):
                self.flow.game.foreground.return_value = foreground
                with patch('wakeup.DailyFlow.tap') as click:
                    with self.assertRaisesRegex(RuntimeError, '切换了应用用户'):
                        self.flow.tap(600, 600)
                click.assert_not_called()
        self.pause_mock.assert_not_called()

    def test_permission_or_missing_foreground_has_bounded_wait(self):
        for foreground in (self.PERMISSION, None):
            with self.subTest(foreground=foreground):
                self.flow.game.foreground.return_value = foreground
                self.flow.foreground_wait_started = None
                start = self.now
                with patch('wakeup.DailyFlow.tap') as click:
                    for _ in range(20):
                        try:
                            self.flow.tap(600, 600)
                        except _ForegroundPending:
                            continue
                        except RuntimeError as exc:
                            self.assertIn('等待游戏前台恢复超时', str(exc))
                            break
                        self.fail('Transient foreground allowed a click')
                    else:
                        self.fail('Foreground wait did not time out')
                click.assert_not_called()
                self.assertLess(self.now-start, 3.3)

    def test_cancel_during_permission_wait_stops_without_input(self):
        self.flow.game.foreground.return_value = self.PERMISSION
        self.flow.check_stop.side_effect = [None, RuntimeError('任务已被用户停止')]
        with patch('wakeup.DailyFlow.tap') as click:
            with self.assertRaisesRegex(RuntimeError, '任务已被用户停止'):
                self.flow.tap(600, 600)
        click.assert_not_called()

    def test_notification_click_transition_returns_to_fresh_outer_frame(self):
        flow = self.prepare_run([[], [], [], []])
        flow.game.foreground.side_effect = [self.GAME, self.PERMISSION] + [self.GAME]*4
        flow.dismiss_notifications = Mock(side_effect=lambda: flow.tap(600, 600))
        with patch('wakeup.dialog_box', side_effect=[[300, 100, 680, 500], None, None, None]), \
                patch('wakeup.DailyFlow.tap') as click:
            flow.run()
        click.assert_not_called()
        self.assertEqual(flow.report, dict(status='finished', steps=[]))


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
