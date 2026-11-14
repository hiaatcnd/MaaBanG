"""Bounded fast-forwarding of confirmed member introductions; no device input."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from costume_unlock import FlowError
from wakeup import RULES, StartupMemberIntro, WakeupFlow
from wakeup_host import AndroidGame, Connection


def hit(text, box=(700, 500, 200, 40)):
    return SimpleNamespace(text=text, box=list(box), score=.99)


def art(page=0):
    # Distinct illustrations with texture; pure loading/fade frames are separate.
    row = np.arange(1280, dtype=np.uint16).astype(np.uint8)
    return np.roll(np.tile(row[None, :, None], (720, 1, 3)), page*31, axis=1)


def screen(page=0, texts=(), *, home=False, modal=None, image=None):
    return SimpleNamespace(image=art(page) if image is None else image,
                           hits=[text if hasattr(text, 'box') else hit(text) for text in texts],
                           home=home, modal=modal)


class MemberIntroPolicyTests(unittest.TestCase):
    def start(self):
        intro = StartupMemberIntro()
        self.assertTrue(intro.start([hit('DreamFes限定'), hit('新成员登场！！')], 100))
        return intro

    def test_generic_banner_starts_without_event_or_character_name(self):
        for texts in ([hit('新成员登场！')], [hit('新成員'), hit('登場！！')],
                      [hit('DreamFes 限定'), hit('新成员 登场!!')]):
            with self.subTest(texts=texts):
                intro = StartupMemberIntro()
                self.assertTrue(intro.start(texts, 100))
                self.assertTrue(intro.active)
        for text in ('新成员', '登场', '招募', 'DreamFestival', '新衣装登场'):
            with self.subTest(text=text):
                intro = StartupMemberIntro()
                self.assertFalse(intro.start([hit(text)], 100))
                self.assertFalse(intro.active)

    def test_unconfirmed_illustration_cannot_use_fast_taps(self):
        intro = StartupMemberIntro()
        self.assertFalse(intro.ready(art(), [], 100))

    def test_first_tap_is_immediate_then_rate_limited(self):
        intro = self.start()
        self.assertTrue(intro.ready(art(), [], 100))
        intro.clicked(100)
        self.assertFalse(intro.ready(art(), [], 100.5))
        self.assertTrue(intro.ready(art(), [], 101))

    def test_missed_taps_stop_on_same_picture_but_new_picture_can_continue(self):
        intro = self.start()
        for now in (100, 101, 102):
            self.assertTrue(intro.ready(art(), [], now))
            intro.clicked(now)
        self.assertFalse(intro.ready(art(), [], 110))
        self.assertTrue(intro.ready(art(1), [], 111))
        self.assertEqual(intro.total, 3)

    def test_ocr_flicker_does_not_replenish_same_picture_budget(self):
        intro = self.start()
        for now in (100, 101, 102):
            self.assertTrue(intro.ready(art(), [], now))
            intro.clicked(now)
        self.assertFalse(intro.ready(art(), [hit('登录')], 103))
        self.assertFalse(intro.ready(art(), [hit('演员名字')], 104))
        self.assertFalse(intro.ready(art(), [], 105))
        self.assertEqual(intro.total, 3)

    def test_blank_manual_transaction_and_edge_controls_are_not_clicked(self):
        cases = [([], np.zeros((720, 1280, 3), dtype=np.uint8)),
                 ([], np.full((720, 1280, 3), 255, dtype=np.uint8)),
                 ([], np.zeros((360, 640, 3), dtype=np.uint8)),
                 ([hit('未知按钮', (30, 340, 80, 40))], art())]
        for text in ('登录', '请选择', '正在下载', '确认购买', '是否消耗星石招募',
                     '支付', '充值', '确定', '返回', '菜单', '取消'):
            cases.append(([hit(text)], art()))
        for hits, image in cases:
            with self.subTest(texts=[h.text for h in hits], shape=image.shape):
                intro = self.start()
                self.assertFalse(intro.ready(image, hits, 100))
                self.assertEqual(intro.total, 0)

    def test_changing_animation_cannot_replenish_total_budget(self):
        intro = self.start()
        for index in range(intro.TOTAL_LIMIT):
            self.assertTrue(intro.ready(art(index), [], 100+index))
            intro.clicked(100+index)
        self.assertFalse(intro.ready(art(50), [], 150))
        self.assertEqual(intro.total, intro.TOTAL_LIMIT)

    def test_title_repetition_and_clicks_do_not_extend_deadline(self):
        intro = self.start()
        self.assertFalse(intro.expired(100+intro.TIMEOUT-1))
        intro.start([hit('新成员登场！')], 100+intro.TIMEOUT-2)
        intro.clicked(100+intro.TIMEOUT-1)
        self.assertTrue(intro.expired(100+intro.TIMEOUT))
        self.assertFalse(intro.ready(art(), [], 100+intro.TIMEOUT))

    def test_closed_intro_cannot_restart_from_repeated_title(self):
        intro = self.start()
        intro.close()
        self.assertFalse(intro.active)
        self.assertFalse(intro.start([hit('新成员登场！')], 110))
        self.assertFalse(intro.ready(art(), [], 110))


class MemberIntroFlowTests(unittest.TestCase):
    GAME = (0, 'com.bilibili.star.bili')

    def setUp(self):
        self.now = 100.
        self.clicks = []
        self.page = screen(texts=['新成员登场！！'])
        self.selector = lambda: self.page
        self.on_click = lambda x, y: None
        clock = patch('wakeup.time.monotonic', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        waiting = patch('wakeup.pause', side_effect=self.pause)
        waiting.start()
        self.addCleanup(waiting.stop)
        modal = patch('wakeup.dialog_box', side_effect=lambda image: self.page.modal)
        modal.start()
        self.addCleanup(modal.stop)
        click = patch('wakeup.DailyFlow.tap', side_effect=self.click)
        click.start()
        self.addCleanup(click.stop)

        flow = self.flow = WakeupFlow.__new__(WakeupFlow)
        flow.game = AndroidGame(Connection('unused', 'fixture'))
        flow.game.foreground = Mock(return_value=self.GAME)
        flow.game.launch = Mock()
        flow.check_stop = Mock()
        flow.record = Mock()
        flow.foreground_wait_started = None
        flow.recorded = 1
        flow.timeout = 300
        flow.unknown_timeout = 60
        flow.allow_download = True
        flow.rules = json.loads(RULES.read_text(encoding='utf-8'))
        flow.report = dict(status='running', steps=[])
        flow.image = self.page.image
        flow.snap = Mock(side_effect=self.snap)
        flow.ocr = Mock(side_effect=lambda *args: self.page.hits)
        flow.clear_home = Mock(side_effect=lambda: self.page.home)
        flow.reco = Mock(return_value=None)
        flow.save_frame = Mock()
        flow.save_report = Mock()
        flow.dismiss_notifications = Mock(return_value=False)

    def pause(self, seconds, check_stop):
        check_stop()
        self.now += seconds

    def snap(self):
        self.page = self.selector()
        self.flow.image = self.page.image
        return self.page.image

    def click(self, x, y):
        self.clicks.append((self.now, x, y, self.page))
        self.on_click(x, y)

    def go_home(self):
        self.page = screen(99, home=True)

    def assert_only_edge_taps(self):
        self.assertTrue(self.clicks)
        self.assertTrue(all((x, y) == StartupMemberIntro.POINT for _, x, y, _ in self.clicks))

    def test_several_members_skip_with_fresh_frames_without_three_second_wait(self):
        pages = [screen(index, ['新成员登场！！'] if index == 0 else ['角色展示'])
                 for index in range(5)]
        pages.append(screen(99, home=True))
        self.selector = lambda: pages[min(len(self.clicks), len(pages)-1)]
        self.flow.run()
        self.assertEqual(self.flow.report['status'], 'finished')
        self.assertEqual(len(self.clicks), 5)
        self.assert_only_edge_taps()
        self.assertEqual(len({id(page) for _, _, _, page in self.clicks}), 5)
        self.assertLess(self.clicks[-1][0]-self.clicks[0][0], 3*4)
        self.assertGreaterEqual(self.flow.clear_home.call_count, 3)

    def test_auto_advancing_members_longer_than_unknown_timeout_can_finish(self):
        pages = [screen(index, ['新成员登场！！'] if index == 0 else ['角色展示'])
                 for index in range(8)]
        home = screen(99, home=True)
        self.selector = lambda: pages[int((self.now-100)//10)] if self.now < 180 else home
        self.flow.run()
        self.assertEqual(self.flow.report['status'], 'finished')
        self.assertGreater(self.now-100, self.flow.unknown_timeout)
        self.assertGreater(len(self.clicks), 8)
        self.assertLessEqual(len(self.clicks), 8*StartupMemberIntro.PAGE_LIMIT)
        self.assert_only_edge_taps()

    def test_missed_first_tap_is_retried_then_stops_at_home(self):
        self.on_click = lambda x, y: self.go_home() if len(self.clicks) == 2 else None
        self.flow.run()
        self.assertEqual(self.flow.report['status'], 'finished')
        self.assertEqual(len(self.clicks), 2)
        self.assertTrue(all(not page.home for _, _, _, page in self.clicks))

    def test_delayed_first_tap_is_observed_before_another_tap(self):
        initial = self.page
        home = screen(99, home=True)
        self.selector = lambda: home if self.clicks and self.now >= self.clicks[0][0]+1 else initial
        self.flow.run()
        self.assertEqual(self.flow.report['status'], 'finished')
        self.assertEqual(len(self.clicks), 1)

    def test_stuck_intro_keeps_same_page_limit_and_never_claims_success(self):
        with self.assertRaisesRegex(FlowError, '超时|未变化|次数'):
            self.flow.run()
        self.assertEqual(len(self.clicks), StartupMemberIntro.PAGE_LIMIT)
        self.assertNotEqual(self.flow.report['status'], 'finished')
        self.assertLessEqual(self.now-100, StartupMemberIntro.TIMEOUT+1)

    def test_known_reward_button_takes_over_and_home_receives_no_edge_taps(self):
        reward = screen(1, [hit('下一步', (990, 620, 120, 40))])
        def advance(x, y):
            if len(self.clicks) == 1:
                self.page = reward
            else:
                self.go_home()
        self.on_click = advance
        self.flow.run()
        self.assertEqual([(x, y) for _, x, y, _ in self.clicks],
                         [StartupMemberIntro.POINT, (1050, 640)])
        self.assertEqual(self.flow.report['status'], 'finished')

    def test_transaction_modal_is_not_clicked_or_replayed(self):
        modal = screen(1, ['是否消耗星石招募', '确定'], modal=[250, 100, 780, 550])
        self.on_click = lambda x, y: setattr(self, 'page', modal)
        self.flow.unknown_timeout = 5
        with self.assertRaisesRegex(FlowError, '未知启动页面|超时'):
            self.flow.run()
        self.assertEqual(len(self.clicks), 1)
        self.assertIs(self.page, modal)

    def test_banner_behind_unknown_modal_does_not_start_fast_forwarding(self):
        self.page = screen(texts=['新成员登场！！'], modal=[250, 100, 780, 550])
        self.flow.unknown_timeout = 5
        with self.assertRaisesRegex(FlowError, '未知启动页面'):
            self.flow.run()
        self.assertEqual(self.clicks, [])
        self.assertEqual(self.now, 105)

    def test_unknown_page_after_reward_uses_normal_timeout(self):
        reward = screen(1, [hit('下一步', (990, 620, 120, 40))])
        unknown = screen(2, ['登录提示'])
        def advance(x, y):
            self.page = reward if len(self.clicks) == 1 else unknown
        self.on_click = advance
        self.flow.unknown_timeout = 5
        with self.assertRaisesRegex(FlowError, '未知启动页面'):
            self.flow.run()
        self.assertEqual([(x, y) for _, x, y, _ in self.clicks],
                         [StartupMemberIntro.POINT, (1050, 640)])
        self.assertLess(self.now-100, 10)

    def test_manual_page_stops_without_replaying_intro_click(self):
        manual = screen(1, ['请输入账号密码'])
        self.on_click = lambda x, y: setattr(self, 'page', manual)
        with self.assertRaisesRegex(FlowError, '手动'):
            self.flow.run()
        self.assertEqual(len(self.clicks), 1)

    def test_loading_transition_has_no_click_and_resumes_with_a_fresh_frame(self):
        initial = self.page
        loading = screen(1, ['正在加载'])
        following = screen(2, ['角色展示'])
        home = screen(99, home=True)
        def select():
            if not self.clicks:
                return initial
            if self.now < 103:
                return loading
            return following if len(self.clicks) == 1 else home
        self.selector = select
        self.flow.run()
        self.assertEqual(len(self.clicks), 2)
        self.assertIs(self.clicks[1][3], following)

    def test_changing_frames_cannot_bypass_total_tap_budget(self):
        pages = [screen(index, ['新成员登场！！'] if index == 0 else ['角色展示'])
                 for index in range(70)]
        home = screen(99, home=True)
        self.selector = lambda: pages[int(self.now-100)] if self.now < 170 else home
        try:
            self.flow.run()
        except FlowError as exc:
            self.assertRegex(str(exc), '次数|上限|耗尽')
        self.assertEqual(len(self.clicks), StartupMemberIntro.TOTAL_LIMIT)
        self.assert_only_edge_taps()

    def test_last_allowed_tap_can_finish_without_false_budget_failure(self):
        pages = [screen(index, ['新成员登场！！'] if index == 0 else ['角色展示'])
                 for index in range(StartupMemberIntro.TOTAL_LIMIT)]
        home = screen(99, home=True)
        self.selector = lambda: pages[len(self.clicks)] if len(self.clicks) < len(pages) else home
        self.flow.run()
        self.assertEqual(self.flow.report['status'], 'finished')
        self.assertEqual(len(self.clicks), StartupMemberIntro.TOTAL_LIMIT)
        self.assertTrue(all(not page.home for _, _, _, page in self.clicks))

    def test_last_allowed_tap_waits_for_delayed_home_without_replaying(self):
        pages = [screen(index, ['新成员登场！！'] if index == 0 else ['角色展示'])
                 for index in range(StartupMemberIntro.TOTAL_LIMIT)]
        home = screen(99, home=True)
        def select():
            if len(self.clicks) < len(pages):
                return pages[len(self.clicks)]
            return home if self.now-self.clicks[-1][0] >= 2 else pages[-1]
        self.selector = select
        self.flow.run()
        self.assertEqual(self.flow.report['status'], 'finished')
        self.assertEqual(len(self.clicks), StartupMemberIntro.TOTAL_LIMIT)
        self.assertGreaterEqual(self.now-self.clicks[-1][0], 2)
        self.assertTrue(all(not page.home for _, _, _, page in self.clicks))

    def test_last_allowed_tap_waits_three_seconds_then_fails_without_replaying(self):
        pages = [screen(index, ['新成员登场！！'] if index == 0 else ['角色展示'])
                 for index in range(StartupMemberIntro.TOTAL_LIMIT)]
        self.selector = lambda: pages[min(len(self.clicks), len(pages)-1)]
        with self.assertRaisesRegex(FlowError, '新成员展示点击次数达到上限'):
            self.flow.run()
        self.assertEqual(len(self.clicks), StartupMemberIntro.TOTAL_LIMIT)
        self.assertEqual(self.now-self.clicks[-1][0], 3)
        self.assertNotEqual(self.flow.report['status'], 'finished')

    def test_repeated_banner_and_loading_cannot_extend_intro_deadline(self):
        initial = self.page
        loading = screen(1, ['正在加载'], image=np.zeros((720, 1280, 3), dtype=np.uint8))
        self.selector = lambda: initial if int(self.now-100) % 20 < 2 else loading
        with self.assertRaisesRegex(FlowError, '新成员展示超时'):
            self.flow.run()
        self.assertEqual(self.now, 100+StartupMemberIntro.TIMEOUT)
        self.assertEqual(len(self.clicks), StartupMemberIntro.PAGE_LIMIT)
        self.assertTrue(all(page is initial for _, _, _, page in self.clicks))

    def test_game_deadline_still_bounds_member_intro(self):
        self.flow.timeout = 30
        with self.assertRaisesRegex(FlowError, '游戏唤醒超时'):
            self.flow.run()
        self.assertEqual(self.now, 130)
        self.assertEqual(len(self.clicks), StartupMemberIntro.PAGE_LIMIT)

    def test_stop_request_is_checked_between_animation_clicks(self):
        def stop():
            if len(self.clicks) >= 2:
                raise FlowError('用户停止')
        self.flow.check_stop = Mock(side_effect=stop)
        with self.assertRaisesRegex(FlowError, '用户停止'):
            self.flow.run()
        self.assertEqual(len(self.clicks), 2)

    def test_foreground_is_rechecked_before_first_animation_tap(self):
        self.flow.game.foreground.side_effect = [self.GAME, (10, self.GAME[1])]
        with self.assertRaisesRegex(FlowError, '切换了应用用户'):
            self.flow.run()
        self.assertEqual(self.clicks, [])

    def test_unrecognized_art_keeps_original_unknown_timeout(self):
        self.page = screen(texts=['普通未适配画面'])
        self.flow.unknown_timeout = 5
        with self.assertRaisesRegex(FlowError, '未知启动页面'):
            self.flow.run()
        self.assertEqual(self.now, 105)
        self.assertLessEqual(len(self.clicks), 1)


if __name__ == '__main__':
    unittest.main()
