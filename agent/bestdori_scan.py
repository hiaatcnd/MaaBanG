"""Read member details without practicing, training, spending or reading stories."""
import re
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from bestdori_catalog import atomic_json, identify, identify_members, normalized as identity_text
from bestdori_profiles import ATTRIBUTES, in_scope, validate_scope, validate_maximize
from bestdori_portraits import CardPortraits
from user_data import data_root
from costume_unlock import FlowError, normalized
from daily_tasks import DailyFlow
from mining_policy import member_cards, member_signature, gold_member_stars
from mining_stories import StoryMiningFlow, member_filter_box
from task_logging import log


def filter_names(member, member_id=None):
    names = [n for key in ('firstName', 'nickname') for n in member.get(key, []) if n]
    # The game calls Michelle by her human name, 美咲, in this selector.
    # Bestdori's full localized character name supplies that exact alias.
    names += [n.split()[-1] for i, n in enumerate(member.get('characterName', []))
              if n and i in (0, 2, 3) and len(n.split()) > 1]
    if member_id == 15:
        names.append('美咲')  # Verified CN game selector; Bestdori names this entry 米歇尔.
    return {identity_text(n) for n in names}


def character_filter_rows(hits, catalog, box):
    """Locate full rows by band and member names, never by their screen row index."""
    left, top, width, height = box
    result = []
    for hit in hits:
        x, y, w, h = hit.box
        if not left+20 <= x <= left+190:
            continue
        bands = [int(id_) for id_, band in catalog['bands'].items()
                 if identity_text(hit.text) in {identity_text(n) for n in band['bandName'] if n}]
        cy = y+h/2
        if len(bands) != 1 or not top+110 <= cy <= top+height-190:
            continue
        band = bands[0]
        members = {int(id_): member for id_, member in catalog['characters'].items() if member['bandId'] == band}
        found = {}
        for name in hits:
            nx, ny, nw, nh = name.box
            if not left+270 <= nx <= left+width-45 or not cy+12 <= ny+nh/2 <= cy+45:
                continue
            ids = [id_ for id_, member in members.items()
                   if identity_text(name.text) in filter_names(member, id_)]
            if len(ids) == 1:
                # Text starts 58 px to the right of its checkbox in the observed
                # member tile. Its row is anchored to the recognized band label.
                found[ids[0]] = (int(nx-58), int(cy+4))
        if set(found) == set(members):
            result.append((band, found))
    return result


def read_ratio(text, *, skill=False):
    value = normalized(text)
    match = re.fullmatch(r'(?:Lv[.]?)?(\d{1,3})/(\d{1,3})', value, re.I)
    if not match:
        raise ValueError('培养等级未识别完整')
    current, limit = map(int, match.groups())
    if not 1 <= current <= limit <= 100 or (skill and limit != 5):
        raise ValueError('培养等级范围无效')
    return current, limit


def same_page_portrait(current, previous):
    # Scroll renders can differ by a fraction of a pixel. Compare low-pass
    # interior patches only to establish page continuity, never card identity.
    def reduced(patch):
        return patch.reshape(10,4,13,4,3).mean(axis=(1,3))
    reference = reduced(previous[12:52,14:66])
    for dy in range(-6,7):
        for dx in range(-3,4):
            diff = np.abs(reference-reduced(current[12+dy:52+dy,14+dx:66+dx]))
            if diff.mean() < 7 and np.percentile(diff,95) < 20:
                return True
    return False


def align_page(previous, current, low=-12, high=12):
    """Map slots by a unique grid translation confirmed by several neighbours.

    Animated five-star artwork can change completely while a card is open.
    A whole row's stable neighbours establish its position; an individual
    similar portrait never causes an unseen card to be skipped.
    """
    candidates = []
    shifts = sorted({round((p[1]-q[1])/8)*8 for p,_ in previous for q,_ in current
                     if p[0] == q[0] and low-6 <= p[1]-q[1] <= high+6})
    for shift in shifts:
        if not low-6 <= shift <= high+6:
            continue
        mapping, matches, rows = {}, [], {}
        for j,(q,b) in enumerate(current):
            found = [i for i,(p,a) in enumerate(previous)
                     if p[0] == q[0] and abs(p[1]-q[1]-shift) <= 6]
            if len(found) != 1:
                continue
            i = found[0]; p,a = previous[i]
            mapping[j] = i
            row_key = round(q[1]/30)
            # Centres in a row can straddle a rounding boundary.
            row_key = next((k for k,v in rows.items() if abs(v[0]-q[1]) < 15),row_key)
            rows.setdefault(row_key,[q[1],0,0])
            rows[row_key][1] += 1
            if same_page_portrait(b,a):
                matches.append(j); rows[row_key][2] += 1
        if (len(matches) >= min(3,len(previous),len(current)) and mapping and len(matches) >= len(mapping)*.6 and
                all(count < 3 or good >= 3 for _,count,good in rows.values())):
            candidates.append(mapping)
    unique = {tuple(sorted(m.items())):m for m in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None


class BestdoriScan(DailyFlow):
    checkbox = StoryMiningFlow.checkbox
    filter_member_stars = StoryMiningFlow.filter_member_stars

    def __init__(self, context, catalog, scope, output, *, cancel=None, progress=None, maximize=None,
                 existing_ids=None, portraits_root=None):
        super().__init__(context)
        self.catalog = catalog
        self.scope = validate_scope(scope, catalog)
        self.maximize = validate_maximize(maximize)
        self.existing_ids = {id_ for id_ in (existing_ids or []) if in_scope(id_, self.scope, catalog)}
        self.portraits_root = Path(portraits_root) if portraits_root else data_root()/'bestdori/public-cache'
        self.portraits = None
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.cancel = cancel
        self.progress = progress or (lambda message: None)
        self.mining = SimpleNamespace(stars=set(self.scope['rarities']))
        self.report = {'status': 'running', 'scope': self.scope, 'maximize': self.maximize,
                       'observations': [], 'errors': []}

    def check_stop(self):
        super().check_stop()
        if self.cancel is not None and self.cancel.is_set():
            raise FlowError('已取消扫描')

    def save_frame(self, name):
        Image.fromarray(self.image[:, :, ::-1]).save(self.output/name)

    def save_report(self):
        atomic_json(self.output/'scan.json', self.report)

    def rarity(self):
        hit = self.reco('BD_MemberStars')
        slots = set()
        for item in hit.filtered_results if hit else []:
            y = item.box[1]+item.box[3]/2
            slot = round((540-y)/29)
            if 0 <= slot < 5 and abs(y-(540-29*slot)) <= 7:
                slots.add(slot)
        # Missing middle stars must not turn a five-star card into a lower rarity.
        count = len(slots) if slots == set(range(len(slots))) else 0
        count = max(count, gold_member_stars(self.image))
        return count if 1 <= count <= 5 else None

    def page_signature(self):
        return [(point, member_signature(self.image, point)) for point in self.page_cards()]

    def same_page(self, before):
        after = self.page_signature()
        mapping = align_page(before, after)
        return bool(before) and len(before) == len(after) and mapping == dict(enumerate(range(len(before))))

    def return_to_list(self, before):
        self.back()
        self.wait('MN_Members')
        for _ in range(5):
            self.require_clear_notification_overlay()
            if self.same_page(before):
                return self.page_signature()
            time.sleep(.2)
            self.snap()
        raise FlowError('返回后未能确认原列表顺序，已保留结果并停止读取')

    def open_card(self, before, slot):
        for attempt in range(3):
            self.tap(*before[slot][0])
            deadline = time.monotonic()+2
            while time.monotonic() < deadline:
                self.snap()
                self.require_clear_notification_overlay()
                if self.reco('MN_MemberDetail'):
                    return before
                time.sleep(.15)
            if not self.reco('MN_Members') or not self.same_page(before):
                raise FlowError('点击卡牌后页面发生变化，已停止读取')
            before = self.page_signature()
            self.report['open_retries'] = self.report.get('open_retries',0)+1
        raise FlowError('卡牌点击连续未响应，已保留结果，请检查游戏状态')

    def list_bottom(self, cards):
        # A stopped swipe is not proof of the bottom: the last portrait must be
        # fully visible and have the game's white list padding underneath it.
        if not cards:
            return False
        thumb = self.list_scroll_thumb()
        if thumb is not None and thumb[1] < 661:
            return False
        last_y = max(y for _,y in cards)
        if thumb is not None and last_y >= 600:
            return True  # Complete last-row portraits/footers were checked above.
        start = last_y+55
        if start > 662:
            return False
        strip = self.image[start:665, 290:1135].astype(float)
        return float(((strip.min(2) > 245) & (np.ptp(strip, axis=2) < 8)).mean()) > .985

    def list_scroll_thumb(self):
        strip = self.image[185:665,1151:1156].astype(float)
        dark = ((strip.max(2) < 145) & (np.ptp(strip,axis=2) < 12)).mean(1) > .6
        ys = np.flatnonzero(dark)
        if len(ys) < 12 or not np.all(np.diff(ys) == 1):
            return None
        return int(ys[0])+185, int(ys[-1])+185

    def page_cards(self):
        cards = member_cards(self.image)
        rows = []
        for point in cards:
            if not rows or point[1]-rows[-1][0][1] > 20:
                rows.append([])
            rows[-1].append(point)
        # A hole inside a visible row means portrait detection missed a card.
        # Stop instead of silently treating that gap as an absent collection item.
        columns = [344, 467, 590, 713, 836, 959, 1082]
        rows = [row for row in rows if np.median([y for _,y in row]) >= 235]
        def has_portrait(x, cy):
            art = self.image[cy-45:cy+33,x-42:x+43].astype(float)
            foot = self.image[cy+38:cy+49,x-10:x+42].astype(float)
            return (float((art.min(2) < 225).mean()) > .45 and
                    float((foot.max(2) < 210).mean()) > .35)
        if rows:
            # Sometimes every border of the last row touches the viewport edge.
            # Confirm its artwork and footers at the regular grid spacing.
            next_y = round(float(np.median([y for _,y in rows[-1]])))+122
            if next_y <= 613:
                last = [(x,next_y) for x in columns if has_portrait(x,next_y)]
                if last:
                    rows.append(last)
        for i, row in enumerate(rows):
            # Animated borders can touch the viewport's bottom edge and make
            # the generic portrait finder omit an otherwise complete last card.
            # Recover only slots with both substantial artwork and a level footer.
            cy = int(np.median([y for _, y in row]))
            if 235 <= cy <= 613:
                for x in columns:
                    if any(px == x for px, _ in row):
                        continue
                    if has_portrait(x,cy):
                        row.append((x,cy))
                row.sort()
            xs = [x for x, _ in row]
            if xs != columns[:len(xs)] or (i < len(rows)-1 and len(xs) != 7):
                raise FlowError('卡片行识别不完整，已保留结果，请重试读取')
            if i and cy-np.median([y for _,y in rows[i-1]]) > 135:
                raise FlowError('卡片行间距异常，可能漏识别整行，已停止读取')
        return [point for row in rows for point in row]

    def settle_list(self):
        self.wait('MN_Members')
        before = None
        for _ in range(12):
            self.snap()
            self.require_clear_notification_overlay()
            try:
                if before == [] and not self.page_cards() and self.empty_list():
                    return
                if before is not None and self.same_page(before):
                    return
                before = self.page_signature()
            except FlowError:
                before = None
            time.sleep(.2)
        raise FlowError('列表滑动后未能稳定，已停止以免遗漏')

    def advance_page(self):
        before = self.page_signature()
        self.overlap_slots = set()
        if self.list_bottom(self.page_cards()):
            time.sleep(.2)
            self.snap()
            self.require_clear_notification_overlay()
            if self.same_page(before) and self.list_bottom(self.page_cards()):
                return False
        # Keep over half the viewport as overlap. The second gesture is inside
        # the card area, so an ignored scrollbar gesture cannot end the scan.
        for x in (1140, 282):
            self.swipe(x, 580, 360)
            self.settle_list()
            self.require_clear_notification_overlay()
            if not self.same_page(before):
                after = self.page_signature()
                overlap = align_page(before, after, 15, 300)
                if overlap is None:
                    raise FlowError('下滑后的重叠卡片未能确认，已停止以免遗漏')
                self.overlap_slots = set(overlap)
                return True
        if not self.list_bottom(self.page_cards()):
            raise FlowError('列表下滑无进展，且未确认到底；已保留部分结果')
        return False

    def empty_list(self):
        return self.hit_text([285, 185, 855, 480], '没有.*成员|无符合.*成员|不存在.*成员')

    def open_list(self):
        self.snap()
        if self.reco('MN_MemberFilter'):
            left, top, width, height = member_filter_box(self.image)
            cancel = self.hit_text([left, top+height-110, width, 100], '^取消$')
            if not cancel:
                raise FlowError('请先关闭游戏中的成员筛选窗口')
            self.tap_hit(cancel)
            self.wait('MN_Members')
        self.require_clear_notification_overlay()
        if self.reco('MN_MemberDetail'):
            self.back()
        elif not self.reco('MN_Members'):
            if not self.reco('MN_TrainingEntry'):
                self.home()
                self.click('CU_HomeBand')
            self.click('MN_TrainingEntry')
        self.click('MN_Members')
        self.wait('MN_Members')
        self.reset_filters()

    def reset_filters(self):
        for _ in range(3):
            self.snap()
            if self.reco('MN_MemberFilter'):
                break
            if not self.reco('MN_Members'):
                raise FlowError('未确认成员列表，无法打开筛选')
            self.require_clear_notification_overlay()
            self.tap(1015,130)
        self.wait('MN_MemberFilter')
        left, top, width, height = member_filter_box(self.image)
        self.member_filter_box = (left, top, width, height)
        toggle = self.hit_text([left, top, width, 100], '^全选[/／]取消全选$')
        if not toggle:
            raise FlowError('无法确认成员筛选全选按钮')
        self.tap_hit(toggle)
        for _ in range(30):
            self.snap()
            label = self.hit_text([left+20, top+100, 180, height-230], '^种类$|^属性$')
            if label and label.box[1]+label.box[3]//2+54 < top+height-140:
                y = label.box[1]+label.box[3]//2+54
                xs = [left+offset for offset in (55, 254, 453, 652)]
                if not any(self.checkbox(x, y) for x in xs):
                    self.tap_hit(toggle)
                    self.snap()
                if not all(self.checkbox(x, y) for x in xs):
                    raise FlowError('无法确认成员筛选已恢复全部')
                self.filter_attributes(xs, y)
                break
            self.swipe(left+width-25, top+160, top+450)
        else:
            raise FlowError('无法读取成员筛选状态')
        self.filter_member_stars(False)
        self.filter_characters()
        self.clear_extra_filters()
        confirm = self.hit_text([left, top+height-110, width, 100], '^确定$')
        if not confirm:
            raise FlowError('无法确认成员筛选确定按钮')
        self.tap_hit(confirm)
        self.wait('MN_Members')
        # The list can retain its previous scroll position after changing filters.
        unchanged = 0
        for step in range(100):
            before = self.page_signature()
            thumb = self.list_scroll_thumb()
            if self.empty_list() or (before and 250 <= before[0][0][1] <= 267 and (thumb is None or thumb[0] <= 188)):
                return
            self.swipe(1140 if step % 2 == 0 else 282, 260, 580)
            self.settle_list()
            unchanged = unchanged+1 if self.same_page(before) or self.empty_list() else 0
            if unchanged >= 2:
                thumb = self.list_scroll_thumb()
                cards = self.page_cards()
                if self.empty_list() or (thumb is not None and thumb[0] <= 188) or (cards and 250 <= cards[0][1] <= 265):
                    return
                raise FlowError('列表上滑无进展，且未确认顶部；已停止以免遗漏')
        raise FlowError('未能确认成员列表顶部')

    def filter_attributes(self, xs, y):
        wanted = self.scope['attributes']
        for attribute, x in zip(ATTRIBUTES, xs):
            if self.checkbox(x, y) != (attribute in wanted):
                self.tap(x, y)
                self.snap()
        if any(self.checkbox(x, y) != (attribute in wanted) for attribute, x in zip(ATTRIBUTES, xs)):
            raise FlowError('无法确认成员种类筛选，已停止以免遗漏')
        self.save_frame('filter-attributes.png')

    def filter_characters(self):
        left, top, width, height = self.member_filter_box
        wanted = {id_ for id_ in self.scope['members']
                  if self.catalog['characters'][str(id_)]['bandId'] in self.scope['bands']}
        verified = set()
        for step in range(16):
            self.snap()
            hits = self.ocr([left+20, top+90, width-55, height-220])
            # Full-page detection sometimes omits a single-character name (彩).
            # Retry each name in its own cell, anchored to the recognized band.
            found = {band for band,_ in character_filter_rows(hits,self.catalog,self.member_filter_box)}
            extra = []
            for hit in hits:
                x,y,w,h = hit.box; cy = y+h/2
                bands = [int(id_) for id_,band in self.catalog['bands'].items()
                         if identity_text(hit.text) in {identity_text(n) for n in band['bandName'] if n}]
                if len(bands) != 1 or bands[0] in found|verified or not top+110 <= cy <= top+height-190:
                    continue
                for col in range(5):
                    roi = [left+289+154*col,round(cy)+10,80,42]
                    text = ''.join(h.text for h in self.ocr(roi,'.+',True))
                    extra.append(SimpleNamespace(text=text,box=(left+294+154*col,round(cy)+17,80,24)))
            hits += extra
            self.save_frame(f'filter-page-{step+1:02d}.png')
            atomic_json(self.output/f'filter-page-{step+1:02d}.json',
                        [{'text': hit.text, 'box': list(hit.box)} for hit in hits])
            for band, members in character_filter_rows(hits, self.catalog, self.member_filter_box):
                if band in verified:
                    continue
                if all(self.checkbox(x,y) for x,y in members.values()) and not all(id_ in wanted for id_ in members):
                    # The recognized row's “选择5人” checkbox clears a fully
                    # selected band in one click; still verify each member below.
                    row_y = next(iter(members.values()))[1]
                    self.tap(left+62,row_y+38)
                    self.snap()
                for id_, (x, y) in members.items():
                    if self.checkbox(x, y) != (id_ in wanted):
                        self.tap(x, y)
                        self.snap()
                if any(self.checkbox(x, y) != (id_ in wanted) for id_, (x, y) in members.items()):
                    raise FlowError('游戏内成员筛选未与所选范围一致，已停止读取')
                verified.add(band)
                self.save_frame(f'filter-band-{band}.png')
                self.progress(f'正在设置游戏内成员筛选：已核对 {len(verified)}/{len(self.catalog["bands"])} 个乐队')
            if verified == set(map(int, self.catalog['bands'])):
                self.report['selected_members'] = sorted(wanted)
                self.progress(f'已确认游戏内成员筛选：{len(wanted)} 位成员')
                return
            # Swipe inside the content. The scrollbar itself ignores swipes.
            self.swipe(left+width-70, top+height-180, top+height-410)
        raise FlowError('未能确认全部乐队的成员筛选状态，已停止读取')

    def clear_extra_filters(self):
        # Story/animation use radio buttons; selecting every checkbox must not
        # leave a user's previous unread-story or animation-only filter active.
        left,top,width,height = self.member_filter_box
        verified = set()
        for _ in range(12):
            self.snap()
            if not self.reco('MN_MemberFilter'):
                raise FlowError('成员筛选窗口已改变，已停止读取')
            for label in ('小故事','动画'):
                if label in verified:
                    continue
                hit = self.hit_text([left+20,top+100,180,height-230], '^'+label+'$')
                if not hit:
                    continue
                y = hit.box[1]+hit.box[3]//2+54
                if not top+120 <= y <= top+height-140:
                    continue
                x = left+55
                if not self.checkbox(x,y,radio=True):
                    self.tap(x,y); self.snap()
                if not self.checkbox(x,y,radio=True):
                    raise FlowError(f'未确认{label}筛选已恢复全部，已停止以免遗漏')
                verified.add(label)
            if len(verified) == 2:
                self.save_frame('filter-other-all.png')
                self.report['extra_filters'] = 'all'
                return
            self.swipe(left+width-70,top+height-180,top+height-410)
        raise FlowError('无法确认故事和动画筛选已清除，已停止以免遗漏')

    def stable(self, reader):
        previous = None
        for _ in range(5):
            self.snap()
            self.require_clear_notification_overlay()
            try:
                value = reader()
            except (ValueError, TypeError):
                value = None
            if value is not None and value == previous:
                return value
            previous = value
            time.sleep(.15)
        return None

    def read_detail(self, number):
        self.wait('MN_MemberDetail')
        # Identity is visible on both tabs. Maximum-only scans need no stat page.
        identity = self.stable(lambda: (self.text([290, 156, 305, 34]),
                                       self.text([290, 190, 305, 40]), self.rarity()))
        if identity is None:
            raise FlowError('卡牌身份读取不稳定')
        title, member, rarity = identity
        # Two identical tuples can still contain a missing star count while the
        # rainbow border animates. Retry that field until two valid reads agree.
        if rarity is None:
            rarity = self.stable(self.rarity)
        member_ids = identify_members(member, self.catalog)
        member_id = next(iter(member_ids)) if len(member_ids) == 1 else None
        if member_id is not None and (member_id not in self.scope['members'] or
                self.catalog['characters'][str(member_id)]['bandId'] not in self.scope['bands']):
            return None
        id_ = identify(title, member, rarity, self.catalog)
        row = {'id': id_, 'title': title, 'member': member, 'rarity': rarity,
               'evidence': f'detail-{number:04d}.png'}
        if id_ is None:
            row['warning'] = ('星级未读清，请通过搜索确认卡牌身份' if rarity is None else
                              '卡名、成员和星级未能唯一匹配，请通过搜索确认卡牌身份')
        self.save_frame(row['evidence'])
        if id_ is not None and not in_scope(id_, self.scope, self.catalog):
            return None
        if id_ in self.existing_ids:
            return row  # An uncertain thumbnail only needs an identity check.
        # Opening a card always lands on its values page. No redundant tab tap.
        levels = None if self.maximize['level'] else self.stable(lambda: read_ratio(self.text([650, 350, 160, 40])))
        skill = None if self.maximize['skill'] else self.stable(lambda: read_ratio(self.text([650, 478, 130, 40]), skill=True))
        if skill:
            row['skill'] = skill[0]-1
        if levels:
            level, maximum = levels
            row['level'] = level
            if id_:
                card = self.catalog['cards'][str(id_)]
                if maximum == card['levelLimit']:
                    row['train'] = 0
                elif card['trainingLevel'] and maximum == card['levelLimit']+card['trainingLevel']:
                    row['train'] = 1
        self.save_frame(row['evidence'])
        # Skipped fields stay absent, so turning an option off later cannot pass
        # an assumed maximum off as an observed game value.
        if self.maximize['master']:
            return row
        rank = self.stable(self.detail_master)
        if rank is not None:
            row['master'] = rank
        else:
            row['warning'] = '卡面星光标记未读清，请核对'
        return row

    def detail_master(self):
        # The pink diamond is present for 1–4 and absent for zero. Match its
        # white outline as well as colour so pink artwork is not a rank badge.
        chip = self.image[307:361,550:603].astype(float)
        pink = (chip[:,:,2] > 220) & (chip[:,:,1] < 150) & (chip[:,:,0] > 70)
        white = chip.min(2) > 235
        edge = self.image[325:344,588:598].astype(float)
        outside_pink = (edge[:,:,2] > 220) & (edge[:,:,1] < 150) & (edge[:,:,0] > 70)
        if outside_pink.mean() < .35:
            return 0
        if pink.mean() < .28 or white.mean() < .10:
            raise ValueError('卡面星光标记不完整')
        text = normalized(''.join(h.text for h in self.ocr([564,319,25,30], '[1-4]', True)))
        if not re.fullmatch('[1-4]', text):
            raise ValueError('卡面星光等级未确认')
        return int(text)

    def run(self, *, limit=None):
        ids, visits, no_new_pages = {}, 0, 0
        seen, existing_seen = set(), set()
        self.overlap_slots = set()
        self.report['summary'] = {'existing_skipped': 0, 'thumbnail_skipped': 0}
        def progress(page):
            summary = self.report['summary']
            summary['existing_skipped'] = len(existing_seen)
            message = f'第 {page+1} 页 · 已读取 {len(ids)} 张 · 已跳过 {len(existing_seen)} 张已有卡 · 重叠位置 {self.report.get("overlap_skipped",0)} 个'
            self.progress(message)
            log('[Bestdori] '+message)
            self.save_report()
        try:
            if self.existing_ids:
                self.portraits = CardPortraits.prepare(self.portraits_root, self.catalog, self.scope,
                    self.existing_ids, self.check_stop, self.progress)
            self.open_list()
            for page in range(600):
                self.wait('MN_Members')
                self.require_clear_notification_overlay()
                cards = self.page_cards()
                if not cards:
                    if self.empty_list():
                        self.snap()
                        if self.empty_list():
                            self.report['status'] = 'complete'
                            break
                    raise FlowError('未识别到卡片，也未确认列表为空')
                before = self.page_signature()
                initial_count = len(seen)
                self.save_frame(f'page-{page+1:04d}.png')
                for slot in range(len(cards)):
                    if slot in self.overlap_slots:
                        self.report['overlap_skipped'] = self.report.get('overlap_skipped',0)+1
                        continue
                    self.check_stop()
                    portrait_id = self.portraits.identify(before[slot][1]) if self.portraits else None
                    if portrait_id in self.existing_ids:
                        seen.add(portrait_id)
                        existing_seen.add(portrait_id)
                        self.report['summary']['thumbnail_skipped'] += 1
                        progress(page)
                        continue
                    if visits >= 5000:
                        raise FlowError('读取次数达到保护上限，尚未确认全部完成')
                    before = self.open_card(before,slot)
                    visits += 1
                    self.report['detail_reads'] = visits
                    row = self.read_detail(visits)
                    if row:
                        key = row['id'] if row['id'] is not None else (row['title'], row['member'], row['rarity'])
                        seen.add(key)
                        if row['id'] in self.existing_ids:
                            existing_seen.add(row['id'])
                        elif key not in ids:
                            ids[key] = row
                            self.report['observations'].append(row)
                        elif any(ids[key].get(k) != row.get(k) for k in ('level', 'skill', 'master', 'train')):
                            ids[key]['warning'] = '重复读取的培养数值不一致，请核对后再采用'
                            ids[key]['conflict'] = True
                    else:
                        raise FlowError('发现所选范围外的卡牌，成员筛选可能已改变，已停止读取')
                    before = self.return_to_list(before)
                    progress(page)
                    if limit is not None and visits >= limit:
                        raise FlowError('仅完成指定数量的验证扫描')
                self.report['pages'] = page+1
                self.report['detail_reads'] = visits
                if not self.advance_page():
                    self.report['status'] = 'complete'
                    break
                no_new_pages = no_new_pages+1 if len(seen) == initial_count else 0
                if no_new_pages >= 3:
                    raise FlowError('连续翻页没有新卡片，可能出现重复页面；已保留部分结果')
            else:
                raise FlowError('成员扫描达到保护上限')
        except Exception as exc:
            self.report['status'] = 'partial'
            self.report['errors'].append(str(exc))
            if self.image is not None:
                self.save_frame('error.png')
        finally:
            self.save_report()
        return self.report
