"""Set the optional event Fever stamp switch before entering a live."""
from task_logging import log
import re

from costume_unlock import FlowError, normalized
from live_policy import fever_option


class FeverSettingsMixin:
    def fever_state(self):
        if not self.reco('LV_FeverDialog'):
            raise FlowError('无法确认Fever印章设置页')
        states = [self.pink(self.image[324:340, x-8:x+8]) for x in (435, 547)]
        if sum(states) != 1:
            raise FlowError('无法确认Fever印章开关状态')
        return states[0]

    def fever_restriction(self):
        text = normalized(self.text([395, 375, 490, 75]))
        remaining = re.search(r'剩余使用次数[:：](\d+)', text)
        stamps = re.search(r'所持Fever印章[:：](\d+)', text, re.I)
        if remaining and int(remaining[1]) == 0:
            return '本期使用次数已用完'
        if stamps and int(stamps[1]) == 0:
            return 'Fever印章不足'
        return None

    def dismiss_fever_refusal(self):
        """Only dismiss an explicit refusal after trying to enable this setting."""
        self.snap()
        if self.reco('LV_FeverDialog'):
            return None
        text = normalized(self.text([335, 160, 610, 395]))
        if (re.search(r'Fever|印章|使用次数|活动.*(?:点数|报酬)', text, re.I)
                and re.search(r'不足|上限|无法|不能|不可|用完|已.*(?:获得|获取|领取)', text)):
            button = self.hit_text([390, 440, 500, 190], '^关闭$|^确定$|^确认$')
            if button:
                self.tap_hit(button)
                self.wait('LV_FeverDialog', 4)
                return text
        raise FlowError('Fever设置后出现未识别页面，停止以保留现场')

    def record_fever(self, requested, actual, reason=None):
        row = {'requested': requested, 'enabled': actual, 'reason': reason}
        self.report.setdefault('fever_settings', []).append(row)
        if reason:
            log(f'[Fever印章] {reason}，本次不使用印章', level='warn')
        else:
            log('[Fever印章] 当前设置：' + ('开启' if actual else '关闭'))
        return actual

    def configure_fever(self, requested):
        enabled = fever_option(requested) == 'on'
        self.snap()
        if not self.reco('LV_FeverDialog'):
            self.wait('LV_Menu')
            self.require_clear_notification_overlay()
            entry = self.reco('LV_FeverEntry')
            if not entry:
                return self.record_fever(requested, False, '当前演出菜单无Fever入口' if enabled else None)
            self.tap_hit(entry)
            try:
                self.wait('LV_FeverDialog', 4)
            except FlowError:
                self.check_stop()
                if not self.reco('LV_Menu'):
                    raise
                self.require_clear_notification_overlay()
                return self.record_fever(requested, False, '当前Fever入口不可用' if enabled else None)
        reason = self.fever_restriction() if enabled else None
        target = enabled and not reason
        current = self.fever_state()
        if current != target:
            self.tap(435 if target else 547, 332)
            refusal = self.dismiss_fever_refusal() if target else None
            self.snap()
            current = self.fever_state()
            reason = reason or refusal
        if not target and current:
            raise FlowError('未能关闭Fever印章，不开始演出')
        if target and not current:
            reason = reason or '游戏未允许开启Fever（可能受活动报酬或使用条件限制）'
        self.click('LV_FeverConfirm')
        self.wait('LV_Menu')
        # Reopen to verify the saved value, not just the radio button before saving.
        self.click('LV_FeverEntry')
        self.wait('LV_FeverDialog', 4)
        actual = self.fever_state()
        if not target and actual:
            raise FlowError('Fever关闭状态未保存，不开始演出')
        if target and not actual:
            reason = reason or '游戏未保存Fever开启状态，本次保持关闭'
        self.click('LV_FeverConfirm')
        self.wait('LV_Menu')
        return self.record_fever(requested, actual, None if actual else reason)
