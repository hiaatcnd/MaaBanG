"""Read filtered member stories, optionally practice and unlock with materials."""
import re
import time
import numpy as np
from dataclasses import asdict
from pathlib import Path

from daily_tasks import DailyFlow
from costume_unlock import FlowError, normalized
from notifications import dialog_box
from mining_policy import (parse_level, can_practice, material_rois, member_cards,
                           gold_member_stars,
                           member_signature, same_member_portrait)


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
        self.wait('MN_Members')

    def filter_unread(self, memory):
        self.tap(1015,130)
        self.wait('MN_MemberFilter')
        for _ in range(8):
            self.swipe(1010,200,500)
        self.snap()
        # The global toggle selects all checkboxes if any were deselected.
        # If all were selected, it clears them; inspect and toggle back once.
        self.tap(950,117)
        self.snap()
        if not self.checkbox(260,282):
            self.tap(950,117)
        self.snap()
        if not all(self.checkbox(x,282) for x in (260,459,658,857)):
            raise FlowError('未确认培养筛选已全选所有属性')
        self.filter_member_stars(memory)
        for _ in range(12):
            self.snap()
            hit = self.hit_text([700,170,260,330],'^回忆小故事$')
            if hit and hit.box[1] < 475:
                y = hit.box[1]+hit.box[3]//2+7
                self.tap(683 if memory else 472,y)
                self.snap()
                if not self.checkbox(683 if memory else 472,y,radio=True):
                    raise FlowError('未读故事筛选未选中')
                for _ in range(6):
                    self.snap()
                    animation = self.hit_text([240,170,175,345],'^动画$')
                    if animation:
                        ay = animation.box[1]+animation.box[3]//2+54
                        if ay < 510:
                            self.tap(261,ay)
                            self.snap()
                            if not self.checkbox(261,ay,radio=True):
                                raise FlowError('成员动画筛选未恢复全部')
                            break
                    self.swipe(1010,490,370)
                else:
                    raise FlowError('未定位成员动画筛选')
                self.tap(770,582)
                self.wait('MN_Members')
                return
            self.swipe(1010,495,300)
        raise FlowError('未定位小故事未读筛选')

    def filter_member_stars(self, memory):
        # Game order is five to one stars, independent of practice/unlock.
        slots=((5,261),(4,419),(3,578),(2,736),(1,895))
        for _ in range(6):
            self.snap()
            label=self.hit_text([235,170,180,340],'^稀有度$')
            if label:
                y=label.box[1]+label.box[3]//2+54
                if 195<=y<=485:
                    for stars,x in slots:
                        if self.checkbox(x,y)!=(stars in self.mining.stars):
                            self.tap(x,y)
                    self.snap()
                    if any(self.checkbox(x,y)!=(stars in self.mining.stars) for stars,x in slots):
                        raise FlowError('成员星级筛选未与配置一致')
                    self.save_frame(f'filter_{"memory" if memory else "story"}_stars.png')
                    self.report.setdefault('filters',[]).append(
                        {'memory':memory,'stars':sorted(self.mining.stars)})
                    return
            self.swipe(1010,490,300)
        raise FlowError('未定位成员星级筛选')

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
        if not 1 <= count <= 5:
            raise FlowError('无法确认成员星级，不练习')
        return count

    def practice(self, row, required):
        current,maximum = parse_level(self.text([654,348,150,35]))
        rarity = self.rarity()
        row.update(level=current,maximum=maximum,rarity=rarity,required=required)
        if not self.mining.practice or rarity not in self.mining.stars or current >= required:
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
                raise FlowError('未确认自动特训已开启')
            current,maximum = parse_level(self.text([654,348,150,35]))
            row.update(automatic_training=True,maximum=maximum)
        if not can_practice(self.mining,rarity,current,maximum,required):
            row['status'] = 'practice_disabled_or_ineligible'
            return False
        self.click('MN_PracticeEntry')
        self.snap()
        if self.reco('MN_PracticeHelp'):
            self.tap(980,645)
        self.wait('MN_PracticePage')
        self.tap(830,645)
        self.snap()
        after,cap = parse_level(self.text([371,457,104,45]))
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
                self.save_frame(f'training_result_{len(self.report["members"])}.png')
                self.click('MN_TrainingResultOK')
            elif self.reco('MN_PracticeConfirm'):
                if not row.get('practice_confirmed'):
                    row['practice_confirmed'] = True
                    self.click('MN_PracticeConfirmOK')
            elif self.reco('MN_PracticeSuccess'):
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
                if actual == maximum:
                    self.back()
                    self.wait('MN_MemberDetail')
            elif self.reco('MN_MemberDetail'):
                actual,_ = parse_level(self.text([654,348,150,35]))
                if actual != maximum:
                    raise FlowError('练习后等级不符，不重复消耗')
                row['practiced_to'] = actual
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
            current,maximum = parse_level(level_text)
            text = normalized(self.text([465,444,360,48]))
            match = re.search(r'解锁等级(\d+)级以上',text)
            if not match:
                raise FlowError('无法确认故事解锁等级')
            required = int(match[1])
            if current < required:
                self.cancel_story_unlock()
                if not memory or not self.mining.unlock or not self.practice(row,required):
                    row.setdefault('status','level_locked')
                    return
                self.tap(480,620)
                self.wait('MN_StoryUnlock')
            if not self.mining.unlock:
                row['status'] = 'material_unlock_disabled'
                self.cancel_story_unlock()
                return
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

    def open_member_detail(self, target):
        number=len(self.report.setdefault('selections',[]))+1
        self.save_frame(f'selection_{number}_list.png')
        self.tap(*target)
        self.wait('MN_MemberDetail')
        audit={'position':list(target)}
        self.report['selections'].append(audit)
        self.save_frame(f'selection_{number}_detail.png')
        # Name OCR is only a log label, never an eligibility or deduplication
        # gate. The SDK may swap lines after a one-pixel shift in their x value.
        # Traversal deduplicates list artwork; story state controls eligibility.
        hits=sorted(self.ocr([292,156,296,72]),key=lambda hit:(hit.box[1],hit.box[0]))
        identity=normalized(' '.join(hit.text for hit in hits)) or f'成员{number}'
        audit.update(member=identity,status='detail_ready')
        print(f'[成员选择] {target} -> {identity}',flush=True)
        return identity

    def run(self):
        if not self.mining.stars:
            self.report['status']='no_stars_selected'
            return
        self.member_list()
        for memory,enabled in ((False,self.mining.stories),(True,self.mining.memories)):
            if not enabled:
                continue
            self.filter_unread(memory)
            seen = []
            for _ in range(3000):
                self.wait('MN_Members')
                target = None
                cards = member_cards(self.image)
                for x,y in cards:
                    signature = member_signature(self.image,(x,y))
                    if any(same_member_portrait(signature,old) for old in seen):
                        continue
                    target = (x,y)
                    break
                if not cards:
                    if self.hit_text([290,190,845,470],r'没有.*成员|无符合.*成员|无合适.*成员|不存在.*成员'):
                        break
                    raise FlowError('未识别到完整成员卡片，无法确认列表为空')
                if target:
                    identity = self.open_member_detail(target)
                    seen.append(signature)
                    row = {'member':identity,'memory':memory}
                    self.report['members'].append(row)
                    print(f'[挖矿故事] {identity}：'+('回忆小故事' if memory else '小故事'),flush=True)
                    self.read_story(memory,row)
                    print(f'[挖矿故事] {row.get("status","unknown")}',flush=True)
                    self.back()
                    self.wait('MN_Members')
                    continue
                before = self.image[185:665,285:1140].astype(float)
                self.swipe(1153,600,340)
                self.snap()
                if np.mean(np.abs(before-self.image[185:665,285:1140])) < 1:
                    break
            else:
                raise FlowError('成员扫描达到保护上限，未确认完成')
        self.report['status'] = 'finished'
        self.home()
