"""Batch skill practice using only explicitly marked same-member lounge cards."""
import json
import re
import time
from pathlib import Path

import numpy as np
from maa.custom_action import CustomAction

from costume_unlock import FlowError, normalized
from daily_tasks import DailyFlow
from mining_policy import member_cards, member_signature, same_member_portrait
from mining_stories import member_filter_box
from notifications import dialog_box
from task_logging import failure, finish, log


def skill_level(text):
    match = re.fullmatch(r'(?:Lv[.]?)?([1-5])/5', normalized(text), re.I)
    if not match:
        raise FlowError(f'无法确认技能等级：{text!r}')
    return int(match[1])


def pink_checked(image, x, y):
    area = image[y-7:y+8, x-7:x+8].astype(np.int16)
    if area.shape[:2] != (15, 15):
        raise FlowError('技能练习复选框超出画面')
    b, g, r = (area[:, :, i] for i in range(3))
    return float(np.mean((r > 190) & (r-g > 65) & (r-b > 15))) > .3


def same_member_slots(hits):
    """Bind bonus labels to complete material cards, excluding clipped rows."""
    slots = []
    for hit in hits:
        if normalized(hit.text) != '相同成员加成':
            continue
        x, y, w, h = map(int, hit.box)
        cx, cy = x+w//2, y+h//2
        # Four material columns, independent of the seven-column target grid.
        column = min((715, 841, 968, 1094), key=lambda value: abs(value-cx))
        if abs(column-cx) > 12 or not 185 <= cy <= 448:
            continue
        point = (column, cy+62)
        if all(abs(point[0]-p[0]) > 15 or abs(point[1]-p[1]) > 15 for p in slots):
            slots.append(point)
    return sorted(slots, key=lambda p: (p[1]//30, p[0]))


def verify_confirmation(text):
    value = normalized(text)
    if ('已选择的休息室成员将会消失' not in value
            or '确定进行技能练习吗' not in value
            or re.search(r'练习券|星石|金币|购买|交换', value)):
        raise FlowError('未确认仅消耗休息室成员的技能练习弹窗')


class SkillPracticeFlow(DailyFlow):
    def __init__(self, context, output):
        super().__init__(context)
        self.output = Path(output)
        self.report = dict(status='running', members=[], practiced_members=0, consumed_cards=0)
        self.material_filter_ready = False

    def save_frame(self, name):
        from PIL import Image
        Image.fromarray(self.image[:, :, ::-1]).save(self.output/name)

    def save_report(self):
        (self.output/'report.json').write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')

    def require_material_page(self):
        self.require_clear_notification_overlay()
        if not self.reco('SP_Page') or not self.reco('SP_Lounge'):
            raise FlowError('未确认技能练习休息室页面')
        # The selected lounge tab has a white interior; the ticket tab is pink.
        if self.image[130:136, 680:720].min(axis=2).mean() < 235:
            raise FlowError('未确认选中休息室，不使用技能练习券')

    def open_list(self):
        self.snap()
        self.require_clear_notification_overlay()
        if self.reco('SP_Page'):
            self.back()
        elif not self.reco('MN_Members'):
            if not self.reco('MN_TrainingEntry'):
                self.home()
                self.click('CU_HomeBand')
            self.click('MN_TrainingEntry')
        self.wait('MN_Members')
        self.click('SP_Tab')
        self.select_all_filters(materials=False)
        self.ensure_filter()

    def select_all_filters(self, *, materials):
        """Reset the target and lounge filters once each via the game's global toggle."""
        self.tap(1015, 141 if materials else 130)
        self.wait('MN_MemberFilter')
        left, top, width, height = member_filter_box(self.image)
        toggle = self.hit_text([left, top, width, 100], '^全选[/／]取消全选$')
        if not toggle:
            raise FlowError('未找到成员筛选的全选按钮')
        self.tap_hit(toggle)
        for _ in range(30):
            self.snap()
            label = self.hit_text([left+20, top+100, 180, height-230], '^种类$|^属性$')
            if label and label.box[1]+label.box[3]//2+54 < top+height-140:
                y = label.box[1]+label.box[3]//2+54
                xs = [left+offset for offset in (55, 254, 453, 652)]
                # If everything was already selected, the toggle clears it.
                # Click it again only after observing that cleared state.
                if not any(pink_checked(self.image, x, y) for x in xs):
                    self.tap_hit(toggle)
                    self.snap()
                if not all(pink_checked(self.image, x, y) for x in xs):
                    raise FlowError('未确认成员筛选已全选')
                confirm = self.hit_text([left, top+height-110, width, 100], '^确定$')
                if not confirm:
                    raise FlowError('未找到成员筛选确定按钮')
                self.tap_hit(confirm)
                self.wait('SP_Page' if materials else 'SP_Filter')
                log('[同名成员技能升级] 已将'+('休息室材料' if materials else '培养成员')+'筛选设为全选')
                return
            self.swipe(left+width-25, top+160, top+450)
        raise FlowError('无法确认成员全选筛选')

    def ensure_filter(self):
        self.wait('SP_Filter')
        self.require_clear_notification_overlay()
        if not pink_checked(self.image, 294, 130):
            self.tap(294, 130)
        self.wait('SP_Filter')
        if not pink_checked(self.image, 294, 130):
            raise FlowError('未确认同名成员筛选已勾选')

    def preview(self):
        self.require_material_page()
        current = skill_level(self.text([230, 425, 95, 40]))
        proposed = self.text([375, 425, 100, 40])
        arrow = self.image[428:458, 345:367].astype(np.int16)
        arrow_visible = np.mean((arrow[:, :, 2] > 200) &
                                (arrow[:, :, 2]-arrow[:, :, 1] > 80)) > .12
        after = skill_level(proposed) if proposed.strip() or arrow_visible else current
        amount = normalized(self.text([235, 475, 110, 30]))
        if not re.fullmatch(r'\d{1,6}', amount) or after < current:
            raise FlowError('技能经验预览无法确认')
        return current, after, int(amount)

    def material_slots(self):
        self.bonus_hits = self.ocr([650, 185, 510, 375], '相同成员加成')
        return same_member_slots(self.bonus_hits)

    def clipped_same_members(self):
        centers = [(hit.box[0]+hit.box[2]//2, hit.box[1]+hit.box[3]//2)
                   for hit in self.bonus_hits if normalized(hit.text) == '相同成员加成']
        if any(448 < y < 550 for _, y in centers):
            return True
        # After scrolling, a complete four-card bonus row can exactly fill the
        # bottom edge, hiding the next row entirely. It still needs continuation.
        bottom = max((y for _, y in centers), default=0)
        return bottom >= 430 and len({round(x/126) for x, y in centers if abs(y-bottom) < 12}) == 4

    def selected(self, point):
        x, y = point
        return pink_checked(self.image, x-34, y-33)

    def prepare_materials(self):
        self.wait('SP_Page')
        self.require_material_page()
        if not self.material_filter_ready:
            self.select_all_filters(materials=True)
            self.material_filter_ready = True
            self.snap()
            self.require_material_page()
        # Every member is freshly opened from the target list. The game starts
        # at the top of its lounge pool with no selected materials.

    def select_material(self, point, previous):
        """Select one more card within the batch, waiting only for its preview."""
        self.check_stop()
        if self.selected(point):
            raise FlowError('材料已选中，不重复点击取消选择')
        if not self.controller.post_click(*point).wait().succeeded:
            raise FlowError('选择同名材料失败')
        deadline = time.monotonic()+8
        last = None
        while time.monotonic() < deadline:
            time.sleep(.15)
            self.snap()
            value = self.preview()
            if self.selected(point) and value[0] == previous[0] and value[2] > previous[2]:
                if value == last:
                    return value
                last = value
        raise FlowError('未确认同名卡选中且经验增加，不提交练习')

    def select_batch(self, row):
        self.prepare_materials()
        value = self.preview()
        if value[2] != 0:
            raise FlowError('新进入的技能练习页已有经验预览，停止以避免提交未知材料')
        row.update(level_before=value[0], selected_cards=0)
        if value[0] == 5:
            row['status'] = 'already_max'
            return False
        pages = 0
        while pages < 100:
            self.snap()
            self.require_material_page()
            slots = self.material_slots()
            pending = [point for point in slots if not self.selected(point)]
            if pending:
                value = self.select_material(pending[0], value)
                row.update(selected_cards=row['selected_cards']+1,
                           level_preview=value[1], exp_preview=value[2])
                if row['selected_cards'] > 1000:
                    raise FlowError('同名材料选择数量异常')
                if value[1] == 5:
                    row['selection_end'] = 'maximum_preview'
                    break
                continue
            # Same-member cards are first. No exploratory scroll after selecting
            # the last one: scroll only for a positively recognized clipped card.
            if not self.clipped_same_members():
                row['selection_end'] = 'no_same_members'
                break
            before = self.image[185:565, 650:1160].copy()
            self.swipe(1155, 520, 295)
            self.snap()
            self.require_material_page()
            pages += 1
            if np.mean(np.abs(before.astype(float)-self.image[185:565, 650:1160])) < .5:
                row['selection_end'] = 'material_list_end'
                break
        else:
            raise FlowError('同名材料列表未收敛')
        if not row['selected_cards']:
            row['status'] = 'no_same_members'
            return False
        self.snap()
        final = self.preview()
        if final != value or final[2] <= 0:
            raise FlowError('提交前技能经验预览发生变化')
        row['status'] = 'batch_ready'
        self.save_frame(f'batch-{len(self.report["members"]):03d}.png')
        log(f'[同名成员技能升级] 已选 {row["selected_cards"]} 张同名卡，'
            f'技能 Lv.{final[0]} → Lv.{final[1]}，获得经验 {final[2]}')
        return True

    def submit_batch(self, row):
        self.require_material_page()
        row['status'] = 'opening_confirmation'
        self.save_report()
        self.click('SP_Submit')
        self.wait('SP_Confirm')
        # The text can be recognized during the modal's entrance animation,
        # before its button accepts input. Observe the settled dialog first.
        time.sleep(.6)
        self.snap()
        if not self.reco('SP_Confirm'):
            raise FlowError('技能练习确认弹窗尚未稳定')
        if dialog_box(self.image) is None:
            raise FlowError('技能练习确认弹窗未定位')
        verify_confirmation(self.text([370, 305, 535, 95]))
        self.save_frame(f'confirm-{len(self.report["members"]):03d}.png')
        button = self.reco('SP_ConfirmOK')
        if not button:
            raise FlowError('技能练习确认按钮无法读取')
        # Persist before the consuming click. Never replay it on a timeout.
        row['status'] = 'submitted'
        self.save_report()
        self.tap_hit(button)
        self.await_result(row)

    def await_result(self, row):
        deadline = time.monotonic()+45
        confirmed = False
        close_attempts = 0
        while time.monotonic() < deadline:
            self.snap()
            if self.reco('SP_Result') and self.reco('SP_ResultOK'):
                if not confirmed:
                    confirmed = True
                    row['status'] = 'success_confirmed'
                    self.report['practiced_members'] += 1
                    self.report['consumed_cards'] += row['selected_cards']
                    self.save_frame(f'result-{len(self.report["members"]):03d}.png')
                    self.save_report()
                    log(f'[同名成员技能升级] 已完成：{row["member"]}，使用 {row["selected_cards"]} 张同名卡')
                if close_attempts >= 3:
                    raise FlowError('技能练习已成功，但结果页未关闭')
                self.tap_hit(self.reco('SP_ResultOK'))
                close_attempts += 1
            elif confirmed and dialog_box(self.image) is None and self.reco('SP_Page'):
                return
            time.sleep(.3)
        raise FlowError('技能练习结果未确认，不重复提交')

    def run(self):
        log('[同名成员技能升级] 筛选同名成员，批量选择至材料用完或技能可升满')
        self.open_list()
        seen = []
        stationary = 0
        for _ in range(2000):
            self.ensure_filter()
            cards = member_cards(self.image)
            candidates = [(point, member_signature(self.image, point)) for point in cards]
            pending = [(point, signature) for point, signature in candidates
                       if not any(same_member_portrait(signature, old) for old in seen)]
            if pending:
                point, signature = pending[0]
                self.tap(*point)
                self.wait('SP_Page')
                self.require_clear_notification_overlay()
                name = normalized(self.text([290, 155, 295, 75]))
                if not name:
                    raise FlowError('技能练习成员名称无法读取')
                row = dict(member=name, status='selecting')
                self.report['members'].append(row)
                if self.select_batch(row):
                    self.submit_batch(row)
                seen.append(signature)
                self.save_report()
                self.back()
                self.wait('SP_Filter')
                stationary = 0
                continue
            before = candidates
            self.swipe(1135, 598, 338)
            self.ensure_filter()
            after = [(point, member_signature(self.image, point)) for point in member_cards(self.image)]
            unchanged = len(before) == len(after) and all(
                abs(p[0]-q[0]) < 5 and abs(p[1]-q[1]) < 8 and same_member_portrait(a, b)
                for (p, a), (q, b) in zip(before, after))
            stationary = stationary+1 if unchanged else 0
            if stationary >= 2:
                if not after:
                    # Empty detection must see the white grid, not a loading screen.
                    panel = self.image[200:650, 280:1120]
                    if np.mean(panel.min(axis=2) > 235) < .9:
                        raise FlowError('未识别到成员，列表也未确认清空')
                self.report['status'] = 'finished'
                self.home()
                return
        raise FlowError('同名成员列表未收敛，停止以避免重复练习')


class UpgradeSameMemberSkills(CustomAction):
    def run(self, context, argv):
        output = Path('debug/skill-practice')/time.strftime('%Y%m%d-%H%M%S')
        output.mkdir(parents=True, exist_ok=True)
        flow = SkillPracticeFlow(context, output)
        try:
            flow.run()
            finish('同名成员技能升级', flow.report)
            return True
        except Exception as exc:
            flow.report.update(status='error', error=str(exc))
            failure('同名成员技能升级', exc, context)
            if flow.image is not None:
                flow.save_frame('error.png')
            return False
        finally:
            flow.save_report()
