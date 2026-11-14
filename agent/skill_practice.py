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
from mining_stories import StoryMiningFlow, member_filter_box
from notifications import dialog_box
from task_logging import failure, finish, log
from skill_list import framed_member_cards, accounted_edge_cards


def target_grid_clear(image, accounted=()):
    """Require every small part of the grid to be blank, not a global majority.

    Only positively identified, terminal members may be masked. A single missed
    portrait or a clipped row must remain visible to this independent check.
    """
    panel = image[185:665, 285:1140].copy()
    if panel.shape != (480, 855, 3):
        return False
    for x, y in accounted:
        panel[max(0, y-77-185):min(480, y+57-185),
              max(0, x-60-285):min(855, x+60-285)] = 255
    dark = panel.min(axis=2) <= 235
    blocks = dark.reshape(32, 15, 57, 15).mean(axis=(1, 3))
    return bool(np.max(blocks) < .03)


def target_grid_unchanged(before, after):
    """Keep local movement evidence while ignoring a stationary card's badge."""
    first, second = before[185:665, 285:1140], after[185:665, 285:1140]
    if first.shape != (480, 855, 3) or second.shape != first.shape:
        return False
    changed = np.max(np.abs(first.astype(np.int16)-second.astype(np.int16)), axis=2) > 24
    if np.max(changed.reshape(32, 15, 57, 15).mean(axis=(1, 3))) < .25:
        return True
    # Bonus badges bob independently of their cards. Mask only the badge of a
    # complete card found in BOTH frames at the same position with matching
    # interior artwork. Unknown or moving cards retain all movement evidence.
    current = framed_member_cards(after)
    for x, y in framed_member_cards(before):
        signature = member_signature(before, (x, y))
        matches = [(px, py) for px, py in current
                   if abs(px-x) <= 3 and abs(py-y) <= 4
                   and same_member_portrait(member_signature(after, (px, py)), signature)]
        if len(matches) == 1:
            px, py = matches[0]
            changed[max(0, min(y, py)-77-185):max(0, max(y, py)-48-185),
                    max(0, min(x, px)-60-285):min(855, max(x, px)+60-285)] = False
    return bool(np.max(changed.reshape(32, 15, 57, 15).mean(axis=(1, 3))) < .25)


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


def grey_unchecked(image, x, y):
    """Positive evidence of an unchecked box, not just absence of pink."""
    area = image[y-7:y+8, x-7:x+8].astype(np.int16)
    if area.shape[:2] != (15, 15):
        return False
    low, high = area.min(axis=2), area.max(axis=2)
    return float(np.mean((low > 120) & (high < 225) & (high-low < 15))) > .6


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
    # Both pages use the same member-display panel. Share its observed-state
    # reset rather than treating preselected attributes as a successful tap.
    checked_filter_box = StoryMiningFlow.checked_filter_box
    position_filter_top = StoryMiningFlow.position_filter_top
    filter_checkbox_state = StoryMiningFlow.filter_checkbox_state
    filter_top_state = StoryMiningFlow.filter_top_state
    reset_all_member_filters = StoryMiningFlow.reset_all_member_filters
    confirm_all_characters = StoryMiningFlow.confirm_all_characters
    confirm_member_filter = StoryMiningFlow.confirm_member_filter

    def __init__(self, context, output):
        super().__init__(context)
        self.output = Path(output)
        self.report = dict(status='running', members=[], practiced_members=0, consumed_cards=0)
        self.material_filter_ready = False

    def save_frame(self, name):
        from PIL import Image
        Image.fromarray(self.image[:, :, ::-1]).save(self.output/name)

    def load_frame(self, name):
        from PIL import Image
        with Image.open(self.output/name) as frame:
            return np.array(frame.convert('RGB'))[:, :, ::-1].copy()

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
        box = member_filter_box(self.image)
        self.reset_all_member_filters(box)
        # Global selection resets checkboxes, but story/animation are radios.
        # Explicitly remove those filters left by a previous story task.
        if not materials:
            for label in ('小故事', '动画'):
                self.clear_story_radio(box, label)
        self.confirm_member_filter(box, ready=lambda:
                                   (self.reco('SP_Page') and self.reco('SP_Lounge')
                                    and not self.reco('SP_Filter')) if materials else self.reco('SP_Filter'))
        log('[同名成员技能升级] 已将'+('休息室材料' if materials else '培养成员')+'筛选设为全选')

    def filter_radio_state(self, x, y):
        """Positive filled/empty-circle evidence, never absence of pink alone."""
        area=self.image[y-20:y+21,x-20:x+21].astype(np.int16)
        if area.shape!=(41,41,3):
            return None
        b,g,r=(area[:,:,i] for i in range(3))
        if np.mean((r[14:27,14:27]>190)&(r[14:27,14:27]-g[14:27,14:27]>45)
                   &(r[14:27,14:27]-b[14:27,14:27]>10))>.25:
            return True
        if np.mean(area[16:25,16:25].min(axis=2)>245)<.9:
            return None
        gray=(area.min(axis=2)>175)&(area.max(axis=2)<243)&(np.ptp(area,axis=2)<18)
        if all(np.count_nonzero(part)>=8 for part in (
                gray[2:13,8:33],gray[28:39,8:33],
                gray[8:33,2:13],gray[8:33,28:39])):
            return False
        return None

    def clear_story_radio(self, box, label):
        """Locate the radio's label afresh, and retry only a still-empty circle."""
        left,top,width,height=box
        roi=[left+25,top+100,180,height-230]
        deadline=time.monotonic()+50
        control_deadline=None
        previous=None
        next_click=0
        attempts=expand_attempts=confirmed=scrolls=0
        selected_seen=False
        while time.monotonic()<deadline:
            self.checked_filter_box(box)
            hit=self.hit_text(roi,'^'+label+'$')
            point=None
            if hit:
                y=hit.box[1]+hit.box[3]//2+54
                if top+120<=y-20 and y+20<=top+height-140:
                    point=(left+55,y)
            if point:
                if control_deadline is None:
                    control_deadline=min(deadline,time.monotonic()+16)
                state=self.filter_radio_state(*point)
                if state is True:
                    selected_seen=True
                    confirmed+=1
                    if confirmed>=2:
                        return
                    previous=None
                else:
                    confirmed=0
                    if selected_seen and state is False:
                        raise FlowError(f'{label}全部选中后状态再次变化，不重复切换')
                    if state is False and previous==point and time.monotonic()>=next_click:
                        if attempts>=3 or time.monotonic()>=control_deadline:
                            break
                        if attempts:
                            log(f'[成员筛选] {label}全部仍未选中，重新确认空单选框后补点')
                        self.tap(*point)
                        attempts+=1
                        next_click=time.monotonic()+3
                        previous=None
                    else:
                        previous=point if state is False else None
                if time.monotonic()>=control_deadline:
                    break
            elif control_deadline is not None:
                raise FlowError(f'{label}筛选目标已变化，不沿用旧坐标')
            else:
                section=self.hit_text([left+25,top+100,width-50,height-230],'^限制条件$')
                cy=section.box[1]+section.box[3]//2 if section else 0
                cx=left+width-60
                area=self.image[cy-7:cy+8,cx-1:cx+2].astype(np.int16)
                plus=section is not None and area.shape==(15,3,3) and np.mean(
                    (area[:,:,2]>190)&(area[:,:,2]-area[:,:,1]>45))>.6
                if plus:
                    expand=(cx,cy)
                    if previous==expand and time.monotonic()>=next_click:
                        if expand_attempts>=3 or time.monotonic()>=deadline:
                            break
                        if expand_attempts:
                            log('[成员筛选] 限制条件仍未展开，重新定位后补点')
                        self.tap(*expand)
                        expand_attempts+=1
                        next_click=time.monotonic()+3
                        previous=None
                    else:
                        previous=expand
                else:
                    if scrolls>=20:
                        break
                    self.swipe(left+width-70,top+height-180,top+height-410)
                    scrolls+=1
                    previous=None
            time.sleep(.2)
        raise FlowError(f'未确认{label}筛选已恢复全部，不使用残留筛选结果')

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

    def material_retry_ready(self, point, previous, reference):
        """Recheck the unchanged member/card on two fresh frames before a retry."""
        x, y = point
        regions = ((slice(145, 525), slice(110, 610)),
                   (slice(y-50, y+52), slice(x-50, x+52)))
        for sample in range(2):
            if sample:
                time.sleep(.2)
            self.snap()
            # preview also requires a clear lounge page, rejecting other tabs
            # and overlays. A grey box is required before toggling it again.
            if (self.preview() != previous or self.selected(point)
                    or not grey_unchecked(self.image, x-34, y-33)):
                return False
            if not any(abs(px-x) <= 3 and abs(py-y) <= 3
                       for px, py in self.material_slots()):
                return False
            if any(np.mean(np.abs(self.image[roi].astype(np.int16)
                                  - reference[roi].astype(np.int16))) > 2
                   for roi in regions):
                return False
        return True

    def select_material(self, point, previous):
        """Retry a missed selection only while its unchanged state is certain."""
        self.check_stop()
        if self.selected(point):
            raise FlowError('材料已选中，不重复点击取消选择')
        reference = self.image.copy()
        attempts = 0
        retry_safe = True
        for attempt in range(3):
            if attempt:
                if not retry_safe or not self.material_retry_ready(point, previous, reference):
                    break
                log(f'[同名成员技能升级] 材料未选中且经验未变化，补点 {attempt}/2')
            self.check_stop()
            if not self.controller.post_click(*point).wait().succeeded:
                raise FlowError('选择同名材料失败')
            attempts += 1
            deadline = time.monotonic()+8
            last = None
            while time.monotonic() < deadline:
                time.sleep(.15)
                self.snap()
                value = self.preview()
                selected = self.selected(point)
                # Latch any observed effect, even if a later frame looks
                # unchanged: retrying a toggle could undo a delayed selection.
                if selected or value != previous:
                    retry_safe = False
                if (selected and value[0] == previous[0]
                        and value[1] >= previous[1] and value[2] > previous[2]):
                    if value == last:
                        return value
                    last = value
                else:
                    last = None
        raise FlowError(f'未确认同名卡选中且经验增加，不提交练习（已点击 {attempts} 次）')

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

    def target_candidates(self, *, fallback=False):
        points = list(member_cards(self.image))
        if fallback:
            for point in framed_member_cards(self.image):
                if not any(abs(point[0]-x) < 10 and abs(point[1]-y) < 15 for x, y in points):
                    points.append(point)
        return [(point, member_signature(self.image, point)) for point in points]

    def stable_target(self, signature, *, allow_open=False):
        """Find the same target again on two fresh, stable list frames."""
        previous = None
        for sample in range(2):
            if sample:
                time.sleep(.2)
            self.snap()
            self.require_clear_notification_overlay()
            if allow_open and self.reco('SP_Page') and self.reco('SP_Lounge'):
                return None
            try:
                self.ensure_filter()
            except FlowError:
                # wait(SP_Filter) may lose the list while the earlier tap finally
                # opens details. Accept that outcome instead of clicking again.
                self.snap()
                self.require_clear_notification_overlay()
                if allow_open and self.reco('SP_Page') and self.reco('SP_Lounge'):
                    return None
                raise
            self.require_clear_notification_overlay()
            if allow_open and self.reco('SP_Page') and self.reco('SP_Lounge'):
                return None
            matches = [(point, current) for point, current in self.target_candidates(fallback=True)
                       if same_member_portrait(current, signature)]
            if len(matches) != 1:
                raise FlowError('培养成员已变化或无法唯一定位，不沿用旧坐标')
            point, current = matches[0]
            if previous is not None and (abs(point[0]-previous[0][0]) > 3
                    or abs(point[1]-previous[0][1]) > 4
                    or not same_member_portrait(current, previous[1])):
                raise FlowError('培养成员列表尚未稳定，不补点')
            # Stability belongs to the selected artwork and position. A badge
            # animation elsewhere in the grid must not block a safe first tap.
            previous = (point, current)
        return point

    def open_target(self, signature):
        for attempt in range(3):
            self.check_stop()
            # A delayed earlier tap may already have reached the detail page.
            self.snap()
            self.require_clear_notification_overlay()
            if self.reco('SP_Page') and self.reco('SP_Lounge'):
                if attempt:
                    return
                raise FlowError('打开培养成员前页面已变化，不选择未知成员')
            point = self.stable_target(signature, allow_open=bool(attempt))
            if point is None:
                return
            if attempt:
                log(f'[同名成员技能升级] 成员详情未打开，重新确认原成员后补点 {attempt}/2')
            self.tap(*point)
            deadline = time.monotonic()+8
            while time.monotonic() < deadline:
                self.snap()
                self.require_clear_notification_overlay()
                if self.reco('SP_Page') and self.reco('SP_Lounge'):
                    return
                time.sleep(.2)
        raise FlowError('培养成员详情未打开，已达到补点上限')

    def return_to_list(self):
        reference = None
        for attempt in range(3):
            self.check_stop()
            self.snap()
            self.require_clear_notification_overlay()
            if self.reco('SP_Filter'):
                return
            self.require_material_page()
            area = self.image[145:525, 110:610].copy()
            if reference is not None and np.mean(np.abs(
                    reference.astype(np.int16)-area.astype(np.int16))) > 2:
                raise FlowError('返回前培养成员页面已变化，不补点')
            reference = area
            # Locate the back button again only on the unchanged material page.
            button = self.reco('CU_Back')
            if not button:
                raise FlowError('未找到技能练习返回按钮')
            if attempt:
                log(f'[同名成员技能升级] 返回列表未生效，补点 {attempt}/2')
            self.tap_hit(button)
            deadline = time.monotonic()+8
            while time.monotonic() < deadline:
                self.snap()
                self.require_clear_notification_overlay()
                if self.reco('SP_Filter'):
                    return
                time.sleep(.2)
        raise FlowError('未返回培养成员列表，已达到补点上限')

    def select_target_batch(self, row):
        # An empty material OCR result is not evidence that this filtered
        # member is complete. Retry observation only; never replay submission.
        for attempt in range(3):
            self.check_stop()
            if self.select_batch(row):
                return True
            if row['status'] == 'already_max':
                return False
            if attempt < 2:
                log(f'[同名成员技能升级] 未识别到同名材料，重新确认 {attempt+1}/2')
                time.sleep(.3)
        row['status'] = 'unresolved_materials'
        self.save_frame('unresolved-materials.png')
        raise FlowError('筛选列表仍有该成员，但未确认可用同名材料，不报告完成')

    def empty_target_message(self):
        """Read the game's explicit empty state without hiding remaining cards."""
        if target_grid_clear(self.image):
            return False  # Plain white alone is not the explicit empty message.
        hits = self.ocr([470, 365, 500, 75], r'^没有可以进行技能练习的成员[。.！!]?$')
        if len(hits) != 1:
            return False
        hit = hits[0]
        if normalized(hit.text).rstrip('。.！!') != '没有可以进行技能练习的成员':
            return False
        x, y, w, h = map(int, hit.box)
        if not (470 <= x and x+w <= 970 and 375 <= y and y+h <= 430
                and 260 <= w <= 460 and 16 <= h <= 40):
            return False
        copy = self.image.copy()
        copy[y-4:y+h+4, x-4:x+w+4] = 255
        return target_grid_clear(copy)

    def confirm_empty_targets(self):
        for sample in range(2):
            if sample:
                time.sleep(.3)
            self.check_stop()
            self.ensure_filter()
            self.require_clear_notification_overlay()
            if self.target_candidates(fallback=True) or not self.empty_target_message():
                return False
        self.save_frame('finished-list.png')
        log('[同名成员技能升级] 已连续确认游戏提示：没有可以进行技能练习的成员')
        self.report['completion_check'] = 'empty_message_confirmed'
        self.home()
        self.report['status'] = 'finished'
        return True

    def run(self):
        log('[同名成员技能升级] 筛选同名成员，批量选择至材料用完或技能可升满')
        self.open_list()
        seen = []
        stationary = 0
        # First reach the end, then return to the top and scan down again.
        # Keep confirmed members across the rescan to avoid repeated spending.
        direction = 'down'
        verifying = False
        unresolved = []
        scrolls = 0
        for _ in range(2000):
            self.check_stop()
            self.ensure_filter()
            self.require_clear_notification_overlay()
            candidates = self.target_candidates()
            pending = [(point, signature) for point, signature in candidates
                       if not any(same_member_portrait(signature, old) for old in seen)]
            if not pending:
                # Artwork and complete-frame detection have independent failure
                # modes. Use both before concluding there is no next member.
                candidates = self.target_candidates(fallback=True)
                pending = [(point, signature) for point, signature in candidates
                           if not any(same_member_portrait(signature, old) for old in seen)]
            if not candidates and self.empty_target_message():
                if self.confirm_empty_targets():
                    return
                # A member or an animation appeared during confirmation; read
                # the current screen again instead of acting on stale results.
                continue
            if pending:
                point, signature = pending[0]
                self.save_frame('target-list.png')
                self.open_target(signature)
                self.require_clear_notification_overlay()
                name = normalized(self.text([290, 155, 295, 75]))
                if not name:
                    raise FlowError('技能练习成员名称无法读取')
                row = dict(member=name, status='selecting')
                self.report['members'].append(row)
                if self.select_target_batch(row):
                    self.submit_batch(row)
                    if row['status'] != 'success_confirmed':
                        raise FlowError('技能练习尚未确认成功，不重复提交或报告完成')
                seen.append(signature)
                self.save_report()
                self.return_to_list()
                stationary = 0
                direction, verifying, unresolved, scrolls = 'down', False, [], 0
                continue
            accounted = [point for point, signature in candidates
                         if any(same_member_portrait(signature, old) for old in seen)]
            if verifying and not target_grid_clear(self.image, accounted):
                # Keep evidence on disk, rather than retaining full screenshots
                # in memory or permanently treating a known clipped card as an
                # unresolved member. Only an actual artwork match may clear it.
                name = f'unresolved-list-{len(unresolved):03d}.png'
                self.save_frame(name)
                unresolved.append((name, accounted))
            before = self.image.copy()
            if scrolls >= 100:
                raise FlowError('培养成员列表复核达到滚动上限，未确认完成')
            self.swipe(1135, 338 if direction == 'up' else 598,
                       598 if direction == 'up' else 338)
            scrolls += 1
            self.ensure_filter()
            self.require_clear_notification_overlay()
            unchanged = target_grid_unchanged(before, self.image)
            stationary = stationary+1 if unchanged else 0
            if stationary >= 2:
                stationary = 0
                if direction == 'up':
                    direction, verifying, unresolved = 'down', True, []
                    log('[同名成员技能升级] 已回到列表顶部，复核是否仍有遗漏成员')
                    continue
                if not verifying:
                    direction = 'up'
                    log('[同名成员技能升级] 列表暂未找到新成员，回到顶部复核')
                    continue
                # Re-read the final screen as well: it may have updated just
                # after the last swipe. Unknown pixels must never mean success.
                after = self.target_candidates(fallback=True)
                if not after and self.empty_target_message():
                    continue  # Confirm the new explicit empty state next loop.
                if any(not any(same_member_portrait(signature, old) for old in seen)
                       for _, signature in after):
                    continue
                accounted = [p for p, _ in after]
                if not target_grid_clear(self.image, accounted):
                    accounted += accounted_edge_cards(self.image, seen)
                # A blank/loading panel is not the game's empty-list message.
                # Without that message, only positively accounted terminal
                # members can support this older traversal completion path.
                clear = bool(accounted) and target_grid_clear(self.image, accounted)
                for name, points in unresolved:
                    self.check_stop()
                    evidence = self.load_frame(name)
                    if not target_grid_clear(evidence, points+accounted_edge_cards(evidence, seen)):
                        clear = False
                        break
                if not clear:
                    self.save_frame('unfinished-list.png')
                    raise FlowError('未识别到全部成员，列表也未确认清空；已复核，仍有未确认内容，不报告完成')
                self.save_frame('finished-list.png')
                self.report['completion_check'] = 'rescanned_without_unresolved_cards'
                self.home()
                self.report['status'] = 'finished'
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
