"""Observe startup screens until the selected game's unobstructed home is stable."""
import json
from pathlib import Path
import re
import time
from datetime import datetime
import numpy as np

from maa.custom_action import CustomAction
from daily_tasks import DailyFlow
from costume_unlock import FlowError, normalized
from notifications import dialog_box
from task_logging import log, finish, failure
from wakeup_host import AndroidGame, Connection, integer, pause


RULES = Path(__file__).with_name('data') / 'wakeup_rules.json'
FOREGROUND_GRACE_SECONDS = 3


class StartupFallback:
    """Bounded edge taps for quiet full-screen startup art, independent of OCR."""

    POINT = (48, 360)  # Outside the centered dialog and bottom action buttons.
    INTERVAL = 3
    PAGE_LIMIT = 3
    TOTAL_LIMIT = 12
    MANUAL = re.compile(r'登录|登陆|注册|账号|帐号|密码|验证|授权|同意|协议|隐私|绑定|'
                        r'下载|更新|安装|维护|选择|跳转|商店|'
                        r'LOGIN|SIGNIN|PASSWORD|DOWNLOAD|UPDATE', re.I)

    def __init__(self):
        self.total = 0
        self.last_click = None
        self.sample = None
        self.since = None
        self.attempts = 0

    def suspend(self):
        # OCR flicker or a foreground interruption must not replenish tap budgets.
        self.since = None

    def ready(self, image, hits, now):
        text = ' '.join(normalized(hit.text) for hit in hits)
        # Startup art can advertise recruitment or paid content. Those words
        # do not turn a full-screen introduction into a transaction page.
        if (not isinstance(image, np.ndarray) or image.shape[:2] != (720, 1280)
                or self.MANUAL.search(text)):
            self.suspend()
            return False
        sample = image[::12, ::12].astype(np.int16)
        # Blank loading frames are not actionable startup illustrations.
        if float(np.std(sample, axis=(0, 1)).mean()) < 8:
            self.suspend()
            return False
        if self.sample is None or float(np.mean(np.abs(sample-self.sample))) > 3:
            self.sample, self.since, self.attempts = sample, now, 0
            return False
        if self.since is None:
            self.since = now
            return False
        x, y = self.POINT
        if any(h.box[0]-24 <= x <= h.box[0]+h.box[2]+24
               and h.box[1]-24 <= y <= h.box[1]+h.box[3]+24 for h in hits):
            return False  # Do not tap an OCR-visible control at the edge.
        return (now-self.since >= self.INTERVAL
                and (self.last_click is None or now-self.last_click >= self.INTERVAL)
                and self.attempts < self.PAGE_LIMIT and self.total < self.TOTAL_LIMIT)

    def clicked(self, now):
        self.last_click = now
        self.attempts += 1
        self.total += 1


class _ForegroundPending(Exception):
    """Retry from a fresh frame after a transient Android foreground change."""


def startup_decision(hits, rules, *, modal=False, allow_download=True):
    """Pure policy: a recognized page AND one unambiguous in-region button.

    The caller supplies only modal OCR when a dialog is in front. Never click
    background buttons, price confirmations or arbitrary screen coordinates.
    """
    text = ' '.join(normalized(hit.text) for hit in hits)
    for pattern, reason in rules['blocked']:
        if re.search(pattern, text, re.I):
            return ('blocked', reason, None)
    for rule in rules['actions']:
        if rule.get('modal') is not None and rule['modal'] != modal:
            continue
        if not all(re.search(pattern, text, re.I) for pattern in rule['when']):
            continue
        if re.search(rule.get('exclude', r'(?!)'), text, re.I):
            continue
        if rule.get('download') and not allow_download:
            return ('blocked', '已关闭自动下载，请手动完成游戏资源更新', None)
        x, y, w, h = rule.get('roi', [0, 0, 1280, 720])
        buttons = [hit for hit in hits if re.fullmatch(rule['button'], normalized(hit.text), re.I)
                   and x <= hit.box[0] and y <= hit.box[1]
                   and hit.box[0]+hit.box[2] <= x+w and hit.box[1]+hit.box[3] <= y+h]
        if len(buttons) == 1:
            return ('click', rule['id'], buttons[0])
    if any(re.search(pattern, text, re.I) for pattern in rules['waiting']):
        return ('wait', '资源下载或加载中', None)
    return ('unknown', text, None)


class WakeupFlow(DailyFlow):
    def __init__(self, context, values, output):
        super().__init__(context)
        info = self.controller.info
        if info.get('type') != 'adb':
            raise FlowError('开始唤醒需要 ADB 控制器')
        self.game = AndroidGame(Connection(info['adb_path'], info['adb_serial']),
                                values.get('package', 'com.bilibili.star.bili'),
                                values.get('user', '0'), self.check_stop)
        self.timeout = integer(values.get('game_timeout', 1200), '游戏唤醒超时', 30, 7200)
        self.unknown_timeout = integer(values.get('unknown_timeout', 60), '未知页面等待', 5, 300)
        self.allow_download = values.get('download', True)
        if not isinstance(self.allow_download, bool):
            raise FlowError('自动下载设置无效')
        self.rules = json.loads(RULES.read_text(encoding='utf-8'))
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.report = dict(status='running', user=self.game.user, package=self.game.package,
                           address=info['adb_serial'], started_at=datetime.now().astimezone().isoformat(),
                           steps=[], rules_version=self.rules['version'])
        self.started = time.monotonic()
        self.recorded = 0
        self.last_recorded = None
        self.foreground_wait_started = None
        self.game.observe = self.snap

    def snap(self):
        image = super().snap()
        # Keep once-a-day screens, loading and animation transitions, including
        # frames consumed inside the shared notification handler.
        sample = image[::12, ::12].astype(np.int16)
        if self.last_recorded is None or np.mean(np.abs(sample-self.last_recorded)) > 2:
            name = f'frame-{self.recorded:05d}.png'
            self.save_frame(name)
            self.recorded += 1
            self.last_recorded = sample
            self.record('frame', file=name)
        return image

    def record(self, event, **details):
        with (self.output / 'trace.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event=event, elapsed=round(time.monotonic()-self.started, 3),
                                         **details), ensure_ascii=False)+'\n')

    def require_game_foreground(self):
        self.check_stop()
        foreground = self.game.foreground()
        now = time.monotonic()
        if foreground == (self.game.user, self.game.package):
            if self.foreground_wait_started is not None:
                self.record('foreground_recovered', foreground=foreground,
                            waited=round(now-self.foreground_wait_started, 3))
                self.foreground_wait_started = None
            return
        # Permission requests briefly own the resumed activity during login.
        # No input is allowed until the exact game/user is foreground again.
        if foreground is not None and foreground != (self.game.user, 'com.android.permissioncontroller'):
            self.record('foreground_mismatch', foreground=foreground)
            raise FlowError('游戏离开前台或切换了应用用户，停止唤醒并保留现场')
        if self.foreground_wait_started is None:
            self.foreground_wait_started = now
            self.record('foreground_wait', foreground=foreground, timeout=FOREGROUND_GRACE_SECONDS)
        if now-self.foreground_wait_started >= FOREGROUND_GRACE_SECONDS:
            self.record('foreground_timeout', foreground=foreground)
            raise FlowError('等待游戏前台恢复超时，请处理系统权限页面或检查应用状态，保留现场')
        pause(.2, self.check_stop)
        raise _ForegroundPending

    def tap(self, x, y):
        self.require_game_foreground()
        self.record('click', x=int(x), y=int(y), frame=self.recorded-1)
        return super().tap(x, y)

    def ocr(self, roi, expected='.+', only_rec=False):
        hits = super().ocr(roi, expected, only_rec)
        self.record('ocr', roi=roi, hits=[dict(text=h.text, box=list(h.box), score=float(h.score)) for h in hits])
        return hits

    def clear_home(self):
        for name in ('CU_HomeBand', 'LV_HomeEntry'):
            match = self.reco(name)
            if not match:
                return False
            x, y, w, h = match.box
            # Template correlation can match dimmed buttons behind a promotion.
            # Both actual home buttons must retain their white borders/panels.
            area = self.image[y:y+h, x:x+w]
            if not area.size or float(np.mean(area.min(axis=2) > 225)) < .12:
                return False
        return True

    def save_frame(self, name):
        if self.image is not None:
            from PIL import Image
            Image.fromarray(self.image[:, :, ::-1]).save(self.output / name)

    def save_report(self):
        (self.output / 'report.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')

    def run(self):
        self.game.launch()
        deadline = time.monotonic() + self.timeout
        unknown_since = None
        previous = None
        repeats = home_frames = 0
        fallback = StartupFallback()
        while time.monotonic() < deadline:
            try:
                self.require_game_foreground()
                self.snap()
                box = dialog_box(self.image)
                hits = self.ocr(box or [0, 0, 1280, 720])
                mode, label, button = startup_decision(hits, self.rules, modal=box is not None,
                                                      allow_download=self.allow_download)
                self.record('recognition', mode=mode, label=label, modal=box,
                            hits=[dict(text=hit.text, box=list(hit.box), score=float(hit.score)) for hit in hits])
                if mode == 'blocked':
                    raise FlowError(label)
                if mode != 'unknown' or box is not None:
                    fallback.suspend()
                if mode == 'click':
                    fingerprint = (label, normalized(button.text),
                                   tuple(sorted(normalized(h.text) for h in hits if h.score >= .85))
                                   if label == '活动下一步' else ())
                    next_repeats = repeats+1 if fingerprint == previous else 1
                    if next_repeats > (8 if label == '活动下一步' else 5):
                        raise FlowError(f'点击后页面未变化：{label}')
                    if len(self.report['steps']) >= 100:
                        raise FlowError('启动页面处理超过 100 步，请检查循环弹窗')
                    self.save_frame(f'{len(self.report["steps"]):03d}-{label}.png')
                    self.tap_hit(button)
                    repeats = next_repeats
                    self.report['steps'].append(dict(action=label, texts=[h.text for h in hits]))
                    self.save_report()
                    log(f'[开始唤醒] {label}')
                    previous, unknown_since, home_frames = fingerprint, None, 0
                    pause(4 if label == '进入游戏' else 1, self.check_stop)
                    continue
                if box is not None:
                    if self.dismiss_notifications():
                        self.report['steps'].append(dict(action='通知弹窗'))
                        self.save_report()
                        previous, unknown_since, home_frames = None, None, 0
                        continue
                elif self.clear_home() and mode != 'wait':
                    fallback.suspend()
                    home_frames += 1
                    if home_frames >= 3:
                        self.require_game_foreground()
                        self.save_frame('home.png')
                        self.report['status'] = 'finished'
                        return
                    pause(1, self.check_stop)
                    continue
                home_frames = 0
                if mode == 'wait':
                    unknown_since = None
                else:
                    unknown_since = unknown_since or time.monotonic()
                    if time.monotonic()-unknown_since >= self.unknown_timeout:
                        self.report['unknown_texts'] = [hit.text for hit in hits]
                        raise FlowError('未知启动页面，已保存截图和识别文字，等待补充适配')
                    if box is None and fallback.ready(self.image, hits, time.monotonic()):
                        if len(self.report['steps']) >= 100:
                            raise FlowError('启动页面处理超过 100 步，请检查循环弹窗')
                        self.save_frame(f'{len(self.report["steps"]):03d}-启动页保底点击.png')
                        self.tap(*fallback.POINT)
                        fallback.clicked(time.monotonic())
                        self.record('fallback', point=list(fallback.POINT),
                                    page_attempt=fallback.attempts, total=fallback.total)
                        self.report['steps'].append(dict(action='启动页保底点击',
                            point=list(fallback.POINT), texts=[h.text for h in hits]))
                        self.save_report()
                        log(f'[开始唤醒] 启动页保底点击（当前画面 {fallback.attempts}/{fallback.PAGE_LIMIT}）')
                        previous, repeats = None, 0
                pause(1, self.check_stop)
            except _ForegroundPending:
                # Discard any OCR/button from before the transition, including
                # clicks attempted by the shared notification handler.
                home_frames = 0
                unknown_since = None
                fallback.suspend()
        raise FlowError('游戏唤醒超时，请检查下载进度或服务器状态')


class StartWakeup(CustomAction):
    def run(self, context, argv):
        output = Path('debug/wakeup') / (time.strftime('%Y%m%d-%H%M%S') + f'-{time.time_ns()%1000000:06d}')
        output.mkdir(parents=True, exist_ok=True)
        flow = None
        try:
            values = (context.get_node_data('SW_Game') or {}).get('attach', {})
            flow = WakeupFlow(context, values, output)
            flow.run()
            finish('开始唤醒', flow.report)
            return True
        except Exception as exc:
            failure('开始唤醒', exc, context)
            if flow:
                flow.report.update(status='error', error=str(exc))
                flow.save_frame('error.png')
            else:
                (output/'report.json').write_text(json.dumps(dict(status='error', error=str(exc)),
                    ensure_ascii=False, indent=2), encoding='utf-8')
            return False
        finally:
            if flow:
                flow.save_report()
