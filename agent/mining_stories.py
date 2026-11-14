"""Unlock and read filtered member stories, with optional member practice."""
from task_logging import log, status_text, record_warning
import re
import time
import numpy as np
from dataclasses import asdict
from pathlib import Path

from daily_tasks import DailyFlow
from costume_unlock import FlowError, normalized
from notifications import dialog_box
from mining_policy import (parse_level, can_practice, material_rois,
                           gold_member_stars,
                           member_signature, same_member_portrait)
from story_levels import (parse_list_level, level_sort_direction,
                          memory_minimum_level, member_list_at_top,
                          story_member_cards as member_cards,
                          member_list_at_bottom, boundary_prefix_accounted)


def member_filter_box(image):
    """Bounds of the large, already recognized member-filter panel."""
    white=(image.min(axis=2)>245)&(np.ptp(image,axis=2)<10)
    columns=np.flatnonzero(white.sum(axis=0)>400)
    if not len(columns):
        raise FlowError('无法定位成员筛选弹窗')
    left,right=int(columns[0]),int(columns[-1])+1
    rows=np.flatnonzero(white[:,left:right].mean(axis=1)>.75)
    if not len(rows):
        raise FlowError('无法定位成员筛选弹窗')
    top,bottom=int(rows[0]),int(rows[-1])+1
    if not (100<=left<=230 and 820<=right-left<=1050 and 500<=bottom-top<=670):
        raise FlowError('成员筛选弹窗尺寸异常')
    return [left,top,right-left,bottom-top]


def training_tab_index(image):
    """Read the selected pink sidebar tab, not the always-visible tab text."""
    if image.shape != (720, 1280, 3):
        return None
    selected = []
    for index, top in enumerate((185, 276, 367, 458, 549)):
        # The left interior stays clear of tab text, stars and panel edges.
        area = image[top+12:top+62, 12:46].astype(np.int16)
        b, g, r = (area[:, :, channel] for channel in range(3))
        if np.mean((r > 225) & (r-g > 100) & (r-b > 65)) > .85:
            selected.append(index)
    return selected[0] if len(selected) == 1 else None


class StoryMiningFlow(DailyFlow):
    def __init__(self, context, options, output):
        super().__init__(context)
        self.mining = options
        self.output = Path(output)
        self.report = dict(status='running', members=[], read=0,
                           options={**asdict(options),'stars':sorted(options.stars)})

    def save_frame(self, name):
        from PIL import Image
        Image.fromarray(self.image[:,:,::-1]).save(self.output/name)

    def member_list(self):
        self.home()
        self.click('CU_HomeBand')
        self.click('MN_TrainingEntry')
        self.ensure_member_list()

    def ensure_member_list(self):
        """Navigate away from a remembered training tab before setting filters."""
        deadline = time.monotonic()+28
        next_click = 0
        attempts = selected_frames = 0
        previous = None
        while time.monotonic() < deadline:
            self.snap()
            if dialog_box(self.image) is not None:
                previous, selected_frames = None, 0
                time.sleep(.2)
                continue
            self.require_clear_notification_overlay()
            button = self.reco('MN_Members')
            index = training_tab_index(self.image)
            if button and index == 0:
                previous = None
                selected_frames += 1
                if selected_frames >= 2:
                    self.save_frame('member-list-selected.png')
                    return
            else:
                selected_frames = 0
                if button and index is not None:
                    box = tuple(map(int, button.box))
                    stable = (previous is not None and previous[0] == index
                              and all(abs(a-b) <= 4 for a, b in zip(previous[1], box)))
                    if stable and time.monotonic() >= next_click:
                        if attempts >= 3:
                            break
                        if attempts:
                            log(f'[挖矿故事] 成员一览尚未选中，重新定位标签后补点 {attempts}/2')
                        else:
                            log('[挖矿故事] 正在切换到成员一览')
                        self.tap_hit(button)
                        attempts += 1
                        next_click = time.monotonic()+8
                        previous = None
                    else:
                        previous = (index, box)
                else:
                    previous = None
            time.sleep(.2)
        self.save_frame('member-list-unconfirmed.png')
        raise FlowError(f'未确认成员一览已选中，停止故事筛选（已点击 {attempts} 次）')

    def wait_member_list(self):
        """Do not interpret another tab's cards/empty message as story results."""
        deadline = time.monotonic()+12
        selected_frames = 0
        while time.monotonic() < deadline:
            self.snap()
            if dialog_box(self.image) is None:
                self.require_clear_notification_overlay()
                button = self.reco('MN_Members')
                index = training_tab_index(self.image)
                if button and index == 0:
                    selected_frames += 1
                    if selected_frames >= 2:
                        return
                else:
                    selected_frames = 0
                    if button and index is not None:
                        raise FlowError('当前未选中成员一览，不读取故事列表或报告完成')
            else:
                selected_frames = 0
            time.sleep(.2)
        raise FlowError('未确认成员一览页面，停止故事列表处理')

    def position_filter_top(self, box):
        """Scroll only while the top controls and their checkbox row are absent."""
        left,top,width,height=box
        for attempt in range(9):
            self.check_stop()
            self.snap()
            if not self.reco('MN_MemberFilter'):
                raise FlowError('成员筛选弹窗已变化，停止回顶')
            current=member_filter_box(self.image)
            if any(abs(a-b)>4 for a,b in zip(current,box)):
                raise FlowError('成员筛选弹窗位置已变化，停止回顶')
            toggle=self.hit_text([left,top,width,100],'^全选[/／]取消全选$')
            label=self.hit_text([left+25,top+100,width-50,height-230],'^种类$|^属性$')
            if toggle and label:
                y=label.box[1]+label.box[3]//2+54
                if top+120<=y-7 and y+7<=top+height-140:
                    return toggle,label
            if attempt==8:
                break
            self.check_stop()
            log(f'[成员筛选] 筛选顶部选项尚未完整显示，回顶 {attempt+1}/8')
            self.swipe(left+width-30,top+160,top+400)
        raise FlowError('未定位成员筛选全选按钮和完整属性行，回顶已达上限')

    def filter_unread(self, memory):
        self.wait_member_list()
        self.tap(1015,130)
        self.wait('MN_MemberFilter')
        box=member_filter_box(self.image)
        self.member_filter_box=box
        left,top,width,height=box
        dx=left-205
        scroll_x=left+width-30
        roi=[left+25,top+100,width-50,height-230]
        self.reset_all_member_filters(box)
        self.filter_member_stars(memory)
        for _ in range(20):
            self.snap()
            section=self.hit_text(roi,'^限制条件$')
            if section:
                cy=section.box[1]+section.box[3]//2
                cx=left+width-60
                vertical=self.image[cy-7:cy+8,cx-1:cx+2].astype(float)
                if np.mean((vertical[:,:,2]>190)&(vertical[:,:,2]-vertical[:,:,1]>45))>.6:
                    self.tap(cx,cy)
                    continue
            hit = self.hit_text([left+25,top+100,180,height-230],'^小故事$')
            if hit and hit.box[1] < top+height-200:
                y = hit.box[1]+hit.box[3]//2+54
                self.tap((683 if memory else 472)+dx,y)
                self.snap()
                if not self.checkbox((683 if memory else 472)+dx,y,radio=True):
                    raise FlowError('未读故事筛选未选中')
                for _ in range(6):
                    self.snap()
                    animation = self.hit_text(roi,'^动画$')
                    if animation:
                        ay = animation.box[1]+animation.box[3]//2+54
                        if ay < top+height-140:
                            self.tap(261+dx,ay)
                            self.snap()
                            if not self.checkbox(261+dx,ay,radio=True):
                                raise FlowError('成员动画筛选未恢复全部')
                            break
                    self.swipe(scroll_x,top+height-170,top+height-290)
                else:
                    raise FlowError('未定位成员动画筛选')
                if memory:
                    self.select_level_sort(box)
                self.confirm_member_filter(box)
                return
            self.swipe(scroll_x,top+height-170,top+height-365)
        raise FlowError('未定位小故事未读筛选')

    def filter_checkbox_state(self, x, y):
        """Require positive checked/unchecked pixels before toggling a filter."""
        area=self.image[y-6:y+7,x-6:x+7].astype(np.int16)
        if area.shape!=(13,13,3):
            return None
        b,g,r=(area[:,:,channel] for channel in range(3))
        if np.mean((r>190)&(r-g>45)&(r-b>10))>.35:
            return True
        if np.mean((area.min(axis=2)>120)&(area.max(axis=2)<230)
                   &(np.ptp(area,axis=2)<18))>.6:
            return False
        return None

    def filter_top_state(self, box):
        self.checked_filter_box(box)
        left,top,width,height=box
        toggle=self.hit_text([left,top,width,100],'^全选[/／]取消全选$')
        label=self.hit_text([left+25,top+100,width-50,height-230],'^种类$|^属性$')
        if not toggle or not label:
            raise FlowError('筛选顶部目标已变化，不沿用旧坐标')
        y=label.box[1]+label.box[3]//2+54
        if not top+120<=y-7 or not y+7<=top+height-140:
            raise FlowError('属性复选框未完整显示，不重按全选')
        points=tuple((left+offset,y) for offset in (55,254,453,652))
        states=tuple(self.filter_checkbox_state(*point) for point in points)
        return toggle,points,states

    def reset_all_member_filters(self, box):
        """Prove the global action ran, instead of accepting preselected attributes.

        With one known unchecked attribute, the global toggle can only select
        every checkbox (attributes, rarities, characters and skill conditions).
        Its observed unchecked-to-checked transition acknowledges that action.
        Story and animation are radios, and are explicitly set afterwards.
        """
        self.position_filter_top(box)
        # Establish an observable precondition. Already-unchecked needs no tap.
        for selecting in (False,True):
            deadline=time.monotonic()+16
            attempts=confirmed=0
            previous=None
            next_click=0
            changed=False
            while time.monotonic()<deadline:
                toggle,points,states=self.filter_top_state(box)
                marker=states[0]
                achieved=(marker is False if not selecting else
                          attempts>0 and all(state is True for state in states))
                if achieved:
                    changed=True
                    confirmed+=1
                    previous=None
                    if confirmed>=2:
                        break
                else:
                    confirmed=0
                    if changed:
                        raise FlowError('筛选状态在确认期间再次变化，不重复切换全选')
                    if selecting and marker is True:
                        # The marker changed, but another checkbox did not. A
                        # second global click could clear everything; do not try.
                        if attempts:
                            changed=True
                        previous=None
                    elif marker is (not selecting) and all(state is not None for state in states):
                        target=tuple(toggle.box) if selecting else points[0]
                        sample=(target,points,states)
                        if previous==sample and time.monotonic()>=next_click:
                            if attempts>=3 or time.monotonic()>=deadline:
                                break
                            if attempts:
                                log('[成员筛选] 筛选点击尚未生效，确认原状态后补点')
                            if selecting:
                                self.tap_hit(toggle)
                            else:
                                self.tap(*points[0])
                            attempts+=1
                            next_click=time.monotonic()+3
                            previous=None
                        else:
                            previous=sample
                    else:
                        previous=None
                time.sleep(.2)
            else:
                raise FlowError('成员筛选重置超时，不使用未确认的筛选结果')
            if confirmed<2:
                raise FlowError('成员筛选重置未生效，已达到补点上限')
        self.confirm_all_characters(box)

    def confirm_all_characters(self, box):
        """Check the character aggregate; expand its section only if collapsed."""
        left,top,width,height=box
        roi=[left+25,top+100,width-50,height-230]
        deadline=time.monotonic()+16
        next_click=0
        attempts=confirmed=0
        previous=None
        while time.monotonic()<deadline:
            self.checked_filter_box(box)
            hit=self.hit_text(roi,'^选择全部$')
            if hit:
                cy=hit.box[1]+hit.box[3]//2
                state=self.filter_checkbox_state(left+width-182,cy)
                if state is False:
                    raise FlowError('角色筛选仍未全部选中，不使用残留筛选结果')
                confirmed=confirmed+1 if state is True else 0
                if confirmed>=2:
                    return
                previous=None
            else:
                confirmed=0
                section=self.hit_text(roi,'^角色筛选$')
                if not section:
                    raise FlowError('未定位角色筛选，不报告已全选')
                cy=section.box[1]+section.box[3]//2
                cx=left+width-60
                area=self.image[cy-7:cy+8,cx-1:cx+2].astype(np.int16)
                plus=area.shape==(15,3,3) and np.mean((area[:,:,2]>190)
                         &(area[:,:,2]-area[:,:,1]>45))>.6
                point=(cx,cy)
                if plus and previous==point and time.monotonic()>=next_click:
                    if attempts>=3 or time.monotonic()>=deadline:
                        break
                    if attempts:
                        log('[成员筛选] 角色筛选仍未展开，重新定位后补点')
                    self.tap(*point)
                    attempts+=1
                    next_click=time.monotonic()+3
                    previous=None
                else:
                    previous=point if plus else None
            time.sleep(.2)
        raise FlowError('未确认角色筛选全部选中，不继续故事筛选')

    def filter_member_stars(self, memory):
        # Game order is five to one stars, independent of practice/unlock.
        slots=((5,261),(4,419),(3,578),(2,736),(1,895))
        left,top,width,height=getattr(self,'member_filter_box',(205,75,870,570))
        dx=left-205
        slots=tuple((stars,x+dx) for stars,x in slots)
        wanted=getattr(self,'active_story_stars',self.mining.stars)
        for _ in range(6):
            self.snap()
            label=self.hit_text([left+25,top+100,180,height-230],'^稀有度$')
            if label:
                y=label.box[1]+label.box[3]//2+54
                if top+120<=y<=top+height-140:
                    for stars,x in slots:
                        if self.checkbox(x,y)!=(stars in wanted):
                            self.tap(x,y)
                    self.snap()
                    if any(self.checkbox(x,y)!=(stars in wanted) for stars,x in slots):
                        raise FlowError('成员星级筛选未与配置一致')
                    self.save_frame(f'filter_{"memory" if memory else "story"}_stars.png')
                    self.report.setdefault('filters',[]).append(
                        {'memory':memory,'stars':sorted(wanted)})
                    return
            self.swipe(left+width-30,top+height-170,top+height-360)
        raise FlowError('未定位成员星级筛选')

    def checked_filter_box(self, box):
        self.snap()
        if not self.reco('MN_MemberFilter'):
            raise FlowError('成员显示弹窗已变化，停止设置排序')
        if any(abs(a-b)>4 for a,b in zip(member_filter_box(self.image),box)):
            raise FlowError('成员显示弹窗尚未稳定，停止设置排序')

    def select_level_sort(self, box):
        """Choose the exact level radio, with fresh-position, bounded retries."""
        left,top,width,height=box
        roi=[left+25,top+100,width-50,height-230]
        for scroll in range(13):
            self.checked_filter_box(box)
            hit=self.hit_text(roi,'^等级$')
            if hit and top+120 <= hit.box[1] <= top+height-160:
                break
            if scroll==12:
                raise FlowError('未定位等级排序选项')
            self.swipe(left+width-30,top+height-170,top+height-330)
        deadline=time.monotonic()+16
        previous=None
        selected_frames=attempts=0
        next_click=0
        while time.monotonic()<deadline:
            self.checked_filter_box(box)
            hit=self.hit_text(roi,'^等级$')
            if not hit:
                raise FlowError('等级排序选项已离开可见区域')
            # Fourth radio column; its label is located again after every frame.
            point=(left+652,hit.box[1]+hit.box[3]//2)
            if self.checkbox(*point,radio=True):
                selected_frames+=1
                if selected_frames>=2:
                    return
                previous=None
            else:
                selected_frames=0
                stable=previous is not None and all(abs(a-b)<=3 for a,b in zip(point,previous))
                if stable and time.monotonic()>=next_click:
                    if time.monotonic()>=deadline:
                        break
                    if attempts>=3:
                        break
                    if attempts:
                        log('[挖矿故事] 等级排序仍未选中，重新定位后补点')
                    self.tap(*point)
                    attempts+=1
                    next_click=time.monotonic()+3
                    previous=None
                else:
                    previous=point
            time.sleep(.2)
        raise FlowError('未确认等级排序已选中，不继续筛选')

    def confirm_member_filter(self, box, *, ready=None):
        """Do not repeat the confirm click once the member list has appeared."""
        left,top,width,height=box
        deadline=time.monotonic()+20
        next_click=0
        attempts=list_frames=0
        previous=None
        list_seen=False
        while time.monotonic()<deadline:
            self.snap()
            if self.reco('MN_MemberFilter'):
                if list_seen:
                    raise FlowError('确认筛选后页面再次变化，不重复提交')
                current=member_filter_box(self.image)
                if any(abs(a-b)>4 for a,b in zip(current,box)):
                    previous=None
                    time.sleep(.2)
                    continue
                hit=self.hit_text([left,top+height-110,width,100],'^确定$')
                point=tuple(hit.box) if hit else None
                if point and previous==point and time.monotonic()>=next_click:
                    if time.monotonic()>=deadline:
                        break
                    if attempts>=3:
                        break
                    if attempts:
                        log('[成员筛选] 筛选弹窗仍未关闭，重新定位确定按钮后补点')
                    self.tap_hit(hit)
                    attempts+=1
                    next_click=time.monotonic()+4
                    previous=None
                else:
                    previous=point
                list_frames=0
            elif (ready() if ready is not None else self.reco('MN_Members') and training_tab_index(self.image)==0) and dialog_box(self.image) is None:
                list_seen=True
                list_frames+=1
                if list_frames>=2:
                    return
                previous=None
            else:
                self.require_clear_notification_overlay()
                previous=None
                list_frames=0
            time.sleep(.2)
        raise FlowError('未确认成员筛选已应用，不报告完成')

    def level_sort_is_descending(self):
        return (bool(self.hit_text([920,112,187,41],'^等级$'))
                and level_sort_direction(self.image)=='descending')

    def ensure_level_descending(self):
        deadline=time.monotonic()+20
        previous=None
        attempts=selected_frames=0
        next_click=0
        while time.monotonic()<deadline:
            self.snap()
            self.require_clear_notification_overlay()
            if (dialog_box(self.image) is not None or not self.reco('MN_Members')
                    or training_tab_index(self.image)!=0):
                previous=None
                selected_frames=0
                time.sleep(.2)
                continue
            if not self.hit_text([920,112,187,41],'^等级$'):
                raise FlowError('列表未使用等级排序，不提前结束回忆检查')
            direction=level_sort_direction(self.image)
            if direction=='descending':
                selected_frames+=1
                if selected_frames>=2:
                    return
                previous=None
            else:
                selected_frames=0
                if direction=='ascending' and previous==direction and time.monotonic()>=next_click:
                    if time.monotonic()>=deadline:
                        break
                    if attempts>=3:
                        break
                    if attempts:
                        log('[挖矿故事] 等级排序仍为升序，确认方向未变后补点')
                    self.tap(1148,131)
                    attempts+=1
                    next_click=time.monotonic()+4
                    previous=None
                else:
                    previous=direction
            time.sleep(.2)
        raise FlowError('未确认等级降序，不提前结束回忆检查')

    def empty_member_list(self):
        return bool(self.hit_text([290,190,845,470],
                    r'没有.*成员|无符合.*成员|无合适.*成员|不存在.*成员'))

    def ensure_member_list_top(self):
        """Inspect the scrollbar first; a list already at the top needs no swipe."""
        for attempt in range(21):
            self.wait_member_list()
            if not member_cards(self.image) and self.empty_member_list():
                return
            at_top=member_list_at_top(self.image)
            if at_top is True:
                return
            if at_top is None or attempt==20:
                raise FlowError('无法确认从列表顶部检查回忆，不报告完成')
            log('[挖矿故事] 成员列表尚未到顶部，继续定位最高等级成员')
            self.swipe(1153,280,610)

    def list_member_level(self, point):
        x,y=point
        number=self.ocr([x+27,y+34,22,17],only_rec=True)
        footer=self.ocr([x+3,y+32,48,21],only_rec=True)
        if len(number)!=1 or len(footer)!=1 or min(number[0].score,footer[0].score)<.90:
            return None
        return parse_list_level(number[0].text,footer[0].text)

    def confirmed_list_level(self, target, signature):
        """Only two matching reads of an unchanged card may exclude the tail."""
        if not self.level_sort_is_descending():
            raise FlowError('回忆检查时等级排序已变化，不提前结束')
        first=self.list_member_level(target)
        if first is None:
            return None
        self.wait_member_list()
        if not self.level_sort_is_descending():
            raise FlowError('回忆检查时等级排序已变化，不提前结束')
        matches=[point for point in member_cards(self.image)
                 if same_member_portrait(member_signature(self.image,point),signature)]
        if len(matches)!=1 or any(abs(a-b)>4 for a,b in zip(matches[0],target)):
            raise FlowError('读取等级时成员列表已变化，停止本轮检查')
        second=self.list_member_level(matches[0])
        return first if first==second else None

    def advance_story_list(self, seen):
        """An ignored swipe is not evidence that all members were inspected."""
        for attempt in range(4):
            self.wait_member_list()
            cards=member_cards(self.image)
            if any(not any(same_member_portrait(member_signature(self.image,point),old)
                           for old in seen) for point in cards):
                return False
            if member_list_at_bottom(self.image) is True:
                if not boundary_prefix_accounted(self.image,(1280,720),cards,seen):
                    raise FlowError('列表底部仍有未核对的成员区域，不报告完成')
                # Recheck on a fresh confirmed page, without another swipe.
                self.wait_member_list()
                cards=member_cards(self.image)
                if (member_list_at_bottom(self.image) is True
                        and all(any(same_member_portrait(member_signature(self.image,point),old)
                                    for old in seen) for point in cards)
                        and boundary_prefix_accounted(self.image,(1280,720),cards,seen)):
                    return True
                continue
            if attempt==3:
                break
            if attempt:
                log('[挖矿故事] 未发现后续成员且未确认到底，重新检查后补滑')
            self.swipe(1153,600,340)
        raise FlowError('滑动后未确认后续成员或列表底部，不报告完成')

    def checkbox(self, x, y, radio=False):
        area = self.image[y-6:y+7,x-6:x+7].astype(float)
        b,g,r = (area[:,:,i] for i in range(3))
        return np.mean((r>190)&(r-g>45)&(r-b>10)) > (.25 if radio else .35)

    def rarity(self):
        hit = self.reco('MN_MemberStars')
        ys = []
        for item in hit.filtered_results if hit else []:
            y = item.box[1]
            if all(abs(y-other)>15 for other in ys):
                ys.append(y)
        # Larger five-star icons may only partially match the old template.
        # Gold interiors independently cover all five positions; never downgrade
        # a five-star member to an allowed three-star practice candidate.
        count=max(len(ys),gold_member_stars(self.image))
        return count if 1 <= count <= 5 else None

    def skip_story(self, row, status, message):
        row['status'] = status
        record_warning(self.report,'[挖矿故事] '+message+'，跳过当前故事')
        return False

    def practice(self, row, required):
        if not self.mining.practice:
            row['status'] = 'practice_disabled_or_ineligible'
            return False
        try:
            current,maximum = parse_level(self.text([654,348,150,35]))
        except ValueError:
            return self.skip_story(row,'practice_info_unreadable','无法读取练习前的等级')
        rarity = self.rarity()
        if rarity is None:
            return self.skip_story(row,'practice_info_unreadable','无法确认成员星级，不提交练习')
        row.update(level=current,maximum=maximum,rarity=rarity,required=required)
        if rarity not in self.mining.stars or current >= required:
            row['status'] = 'practice_disabled_or_ineligible'
            return False
        # The game exposes the trained cap immediately when this checkbox is on.
        # Enable it before checking eligibility or recommending tickets, allowing
        # one practice operation to go directly from Lv.1 to the trained maximum.
        if self.hit_text([850,309,130,45],'^自动特训$'):
            if not self.checkbox(831,331):
                self.tap(831,331)
            self.snap()
            if not self.checkbox(831,331):
                return self.skip_story(row,'practice_setup_unconfirmed','未确认自动特训已开启，不提交练习')
            try:
                current,maximum = parse_level(self.text([654,348,150,35]))
            except ValueError:
                return self.skip_story(row,'practice_info_unreadable','无法读取特训后的等级上限')
            row.update(automatic_training=True,maximum=maximum)
        if not can_practice(self.mining,rarity,current,maximum,required):
            row['status'] = 'practice_disabled_or_ineligible'
            return False
        log('[挖矿故事] 等级不足，正在准备练习')
        self.click('MN_PracticeEntry')
        self.snap()
        if self.reco('MN_PracticeHelp'):
            self.tap(980,645)
        self.wait('MN_PracticePage')
        self.tap(830,645)
        self.snap()
        try:
            after,cap = parse_level(self.text([371,457,104,45]))
        except ValueError:
            self.back()
            self.wait('MN_MemberDetail')
            return self.skip_story(row,'practice_info_unreadable','无法读取用券预览，不提交练习')
        if after != maximum or cap != maximum:
            self.back()
            self.wait('MN_MemberDetail')
            row['status'] = 'insufficient_practice_tickets'
            return False
        self.save_frame(f'practice_{len(self.report["members"])}.png')
        self.tap(1070,645)
        return self.await_practice(row,maximum)

    def await_practice(self, row, maximum):
        deadline = time.monotonic()+90
        result_seen = False
        illustration_taps = 0
        last_illustration_tap = time.monotonic()
        while time.monotonic() < deadline:
            self.snap()
            if self.reco('MN_AutoTrainingConfirm'):
                if not row.get('practice_confirmed'):
                    materials = []
                    slots = material_rois(self.image,frame_y=438,amount_y=522)
                    for roi in slots:
                        match = re.fullmatch(r'(\d+)/(\d+)',normalized(self.text(roi)))
                        if match:
                            materials.append(tuple(map(int,match.groups())))
                    if len(slots) not in (1,2,3) or len(materials)!=len(slots) or any(need<=0 or have<need for have,need in materials):
                        self.tap(510,620)
                        self.wait('MN_PracticePage')
                        self.back()
                        self.wait('MN_MemberDetail')
                        row['status'] = 'insufficient_or_unreadable_training_materials'
                        return False
                    row.update(training_materials=materials,practice_confirmed=True,
                               automatic_training_confirmed=True)
                    self.save_frame(f'auto_training_{len(self.report["members"])}.png')
                    self.click('MN_AutoTrainingOK')
                    last_illustration_tap = time.monotonic()
            elif self.reco('MN_TrainingResult'):
                result_seen = True
                self.save_frame(f'training_result_{len(self.report["members"])}.png')
                self.click('MN_TrainingResultOK')
            elif self.reco('MN_PracticeConfirm'):
                if not row.get('practice_confirmed'):
                    row['practice_confirmed'] = True
                    self.click('MN_PracticeConfirmOK')
            elif self.reco('MN_PracticeSuccess'):
                result_seen = True
                self.click('MN_PracticeClose')
            elif self.dismiss_notifications():
                continue
            elif self.reco('MN_PracticePage'):
                # Some practice results return to the ticket page. Read the
                # current level (left side), not the proposed level on the right.
                level_text = self.text([231,457,96,46])
                try:
                    actual,_ = parse_level(level_text)
                except ValueError:
                    actual = None
                if result_seen or actual is not None and actual >= row.get('required',maximum):
                    self.back()
                    self.wait('MN_MemberDetail')
            elif self.reco('MN_MemberDetail'):
                try:
                    actual,_ = parse_level(self.text([654,348,150,35]))
                except ValueError:
                    row['status'] = 'practice_level_unreadable'
                    record_warning(self.report,'[挖矿故事] 练习已结束，等级无法读取，跳过当前故事')
                    return False
                row['practiced_to'] = actual
                if actual < row.get('required',maximum):
                    row['status'] = 'level_locked'
                    record_warning(self.report,'[挖矿故事] 练习后仍未达到故事解锁等级，跳过当前故事')
                    return False
                return True
            elif (row.get('automatic_training_confirmed') and illustration_taps<3
                  and time.monotonic()-last_illustration_tap>=4):
                # Special training pauses on a full-screen illustration. Only
                # advance it after submission and after excluding all known UI
                # pages/dialogs above, never repeat the consuming confirmation.
                self.tap(1100,650)
                illustration_taps += 1
                last_illustration_tap = time.monotonic()
            time.sleep(.4)
        raise FlowError('练习结果未确认，不重复提交')

    def cancel_story_unlock(self):
        # The detail header remains visible behind the modal. Waiting for that
        # header alone can return before Cancel has actually taken effect.
        attempts=0
        last_tap=0.
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            self.snap()
            if self.reco('MN_StoryUnlock'):
                cancel=self.hit_text([380,500,265,85],'^取消$')
                if cancel and attempts<3 and time.monotonic()-last_tap>=1:
                    self.tap_hit(cancel)
                    attempts+=1
                    last_tap=time.monotonic()
            elif dialog_box(self.image) is None and self.reco('MN_MemberDetail'):
                return
            time.sleep(.2)
        raise FlowError('故事解锁弹窗未确认取消，不点击底层返回')

    def read_story(self, memory, row):
        # Capture the unobstructed detail now, but only require a valid level
        # when the story is locked. Its modal covers the level's screen region.
        level_text = self.text([654,348,150,35])
        self.tap(480 if memory else 225,620)
        self.snap()
        if self.reco('MN_StoryUnlock'):
            try:
                current,maximum = parse_level(level_text)
            except ValueError:
                self.cancel_story_unlock()
                self.skip_story(row,'story_requirement_unreadable','无法读取当前等级，不解锁')
                return
            text = normalized(self.text([465,444,360,48]))
            match = re.search(r'解锁等级(\d+)级以上',text)
            if not match:
                self.cancel_story_unlock()
                self.skip_story(row,'story_requirement_unreadable','无法读取故事解锁等级，不解锁')
                return
            required = int(match[1])
            if current < required:
                self.cancel_story_unlock()
                if not memory or not self.practice(row,required):
                    row.setdefault('status','level_locked')
                    return
                self.tap(480,620)
                self.wait('MN_StoryUnlock')
            materials = []
            slots = material_rois(self.image)
            for roi in slots:
                match = re.fullmatch(r'(\d+)/(\d+)',normalized(self.text(roi)))
                if match:
                    materials.append(tuple(map(int,match.groups())))
            if len(slots) not in (1,2,3) or len(materials)!=len(slots) or any(need <= 0 or have < need for have,need in materials):
                row['status'] = 'insufficient_or_unreadable_materials'
                self.cancel_story_unlock()
                return
            row['materials'] = materials
            log('[挖矿故事] 材料已确认，正在解锁故事')
            self.save_frame(f'unlock_{len(self.report["members"])}.png')
            self.tap(770,543)
            self.snap()
        self.finish_story(memory,row)

    def finish_story(self, memory, row):
        deadline = time.monotonic()+90
        reward = False
        entered = False
        detail_since = None
        while time.monotonic() < deadline:
            self.snap()
            if self.reco('MN_StoryReward'):
                reward = True
                entered = True
                detail_since = None
                row['reward_dialogs'] = row.get('reward_dialogs',0)+1
                self.save_frame(f'story_reward_{len(self.report["members"])}_{row["reward_dialogs"]}.png')
                self.click('MN_StoryRewardOK')
            elif self.dismiss_notifications():
                detail_since = None
                continue
            elif self.reco('LV_TalkSkipConfirm'):
                self.click('MN_StorySkipOK')
            elif self.reco('LV_TalkSkip'):
                entered = True
                self.click('LV_TalkSkip')
            elif self.reco('LV_TalkMenu'):
                entered = True
                self.click('LV_TalkMenu')
            elif self.reco('MN_StoryVoice'):
                entered = True
                self.click('MN_StoryNoVoice')
            elif self.reco('MN_MemberDetail') and entered:
                # A second reward dialog follows the stat increase. Require the
                # unobstructed detail page to persist beyond the transition.
                if dialog_box(getattr(self,'image',None)) is not None:
                    detail_since = None
                    time.sleep(.4)
                    continue
                if detail_since is None:
                    detail_since = time.monotonic()
                if time.monotonic()-detail_since < 1.5:
                    time.sleep(.4)
                    continue
                row['status'] = 'read_reward_confirmed' if reward else 'read_reward_unconfirmed'
                self.report['read'] += 1
                return
            elif self.reco('MN_MemberDetail') and not entered:
                # Unlocking returns to details before opening the story.
                if row.get('opened_after_unlock'):
                    raise FlowError('故事未开始，停止重复点击')
                row['opened_after_unlock'] = True
                self.tap(480 if memory else 225,620)
            time.sleep(.4)
        raise FlowError('故事阅读/奖励确认超时')

    def enter_member_detail(self, signature, audit):
        """Recover a missed card tap only while the same member is still visible."""
        deadline = time.monotonic()+28
        next_click = 0
        previous = None
        detail_frames = 0
        detail_seen = False
        point = None
        while time.monotonic() < deadline:
            self.snap()
            self.require_clear_notification_overlay()
            if self.reco('MN_MemberDetail'):
                if not audit['attempts']:
                    raise FlowError('打开成员前已离开列表，不选择未知成员')
                detail_seen = True
                detail_frames += 1
                if detail_frames >= 2:
                    return point
                previous = None
            else:
                detail_frames = 0
                index = training_tab_index(self.image)
                on_list = self.reco('MN_Members')
                if on_list and index is not None and index != 0:
                    raise FlowError('打开成员时已切换到其他培养标签，不补点')
                if on_list and index == 0:
                    if detail_seen:
                        raise FlowError('成员详情出现后又返回列表，不重复打开')
                    if time.monotonic() >= next_click:
                        if audit['attempts'] >= 3:
                            break
                        matches = []
                        for candidate in member_cards(self.image):
                            current = member_signature(self.image, candidate)
                            if same_member_portrait(current, signature):
                                matches.append((candidate, current))
                        if len(matches) != 1:
                            raise FlowError('目标成员已变化或无法唯一定位，不沿用旧坐标补点')
                        current_point, current_signature = matches[0]
                        stable = (previous is not None
                                  and abs(current_point[0]-previous[0][0]) <= 3
                                  and abs(current_point[1]-previous[0][1]) <= 4
                                  and same_member_portrait(current_signature, previous[1]))
                        if stable:
                            if time.monotonic() >= deadline:
                                break
                            if audit['attempts']:
                                log(f'[挖矿故事] 成员详情未打开，重新确认同一成员后补点 {audit["attempts"]}/2')
                            point = current_point
                            audit['attempts'] += 1
                            audit['clicks'].append(list(point))
                            self.tap(*point)
                            next_click = time.monotonic()+8
                            previous = None
                        else:
                            previous = (current_point, current_signature)
                    else:
                        previous = None
                else:
                    previous = None
            time.sleep(.2)
        raise FlowError(f'成员详情未确认打开（已点击 {audit["attempts"]} 次），不报告完成')

    def open_member_detail(self, target):
        number=len(self.report.setdefault('selections',[]))+1
        self.save_frame(f'selection_{number}_list.png')
        signature=member_signature(self.image,target)
        audit={'position':list(target),'status':'opening','attempts':0,'clicks':[]}
        self.report['selections'].append(audit)
        try:
            point=self.enter_member_detail(signature,audit)
        except FlowError as error:
            audit.update(status='detail_unconfirmed',error=str(error))
            raise
        audit['position']=list(point)
        self.save_frame(f'selection_{number}_detail.png')
        # Name OCR is only a log label, never an eligibility or deduplication
        # gate. The SDK may swap lines after a one-pixel shift in their x value.
        # Traversal deduplicates list artwork; story state controls eligibility.
        hits=sorted(self.ocr([292,156,296,72]),key=lambda hit:(hit.box[1],hit.box[0]))
        identity=normalized(' '.join(hit.text for hit in hits)) or f'成员{number}'
        audit.update(member=identity,status='detail_ready')
        log(f'[成员选择] {point} -> {identity}')
        return identity

    def run(self):
        if not self.mining.stars:
            self.report['status']='no_stars_selected'
            return
        self.member_list()
        for memory,enabled in ((False,self.mining.stories),(True,self.mining.memories)):
            if not enabled:
                continue
            groups=[self.mining.stars]
            if memory and not self.mining.practice:
                # Equal thresholds share one filter pass; the game filters the
                # rarity so tiny portrait stars never decide eligibility.
                groups=[frozenset(s for s in self.mining.stars if memory_minimum_level(s)==level)
                        for level in (60,50,30,20)]
                groups=[group for group in groups if group]
            for stars in groups:
                self.active_story_stars=stars
                log('[挖矿故事] 正在筛选未读' + ('回忆小故事' if memory else '小故事')
                    + '，星级 '+','.join(map(str,sorted(stars))))
                self.filter_unread(memory)
                if memory:
                    self.ensure_level_descending()
                    self.ensure_member_list_top()
                minimum=(min(memory_minimum_level(s) for s in stars)
                         if memory and not self.mining.practice else None)
                self.scan_story_members(memory,minimum)
        self.active_story_stars=self.mining.stars
        self.report['status'] = 'finished'
        self.home()

    def scan_story_members(self, memory, minimum=None):
        seen = []
        for _ in range(3000):
            self.wait_member_list()
            target = None
            cards = member_cards(self.image)
            for x,y in cards:
                signature = member_signature(self.image,(x,y))
                if any(same_member_portrait(signature,old) for old in seen):
                    continue
                target = (x,y)
                break
            if not cards:
                if self.empty_member_list():
                    break
                raise FlowError('未识别到完整成员卡片，无法确认列表为空')
            if target:
                if minimum is not None:
                    level=self.confirmed_list_level(target,signature)
                    if level is not None and level<minimum:
                        if not boundary_prefix_accounted(self.image,target,member_cards(self.image),seen):
                            raise FlowError('低等级成员前仍有未核对区域，不提前结束回忆检查')
                        boundary=dict(stars=sorted(self.active_story_stars),
                                      level=level,required=minimum,position=list(target),
                                      reason='remaining_below_memory_level')
                        self.report.setdefault('level_boundaries',[]).append(boundary)
                        self.save_frame(f'memory-level-boundary-{len(self.report["level_boundaries"])}.png')
                        log(f'[挖矿故事] 当前成员 Lv.{level}，低于回忆所需 Lv.{minimum}；'
                            '已确认等级降序，后续成员等级不足，结束本组检查（未解锁）')
                        break
                identity = self.open_member_detail(target)
                seen.append(signature)
                row = {'member':identity,'memory':memory}
                self.report['members'].append(row)
                log(f'[挖矿故事] {identity}：'+('回忆小故事' if memory else '小故事'))
                self.read_story(memory,row)
                log(f'[挖矿故事] {identity}：{status_text(row.get("status","unknown"))}',
                    level="success" if row.get("status")=="read_reward_confirmed" else "warn")
                self.back()
                self.wait_member_list()
                continue
            if self.advance_story_list(seen):
                break
        else:
            raise FlowError('成员扫描达到保护上限，未确认完成')
