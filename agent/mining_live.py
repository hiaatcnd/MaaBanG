"""Free-live FC mining and stage challenge progression."""
from dataclasses import asdict
import time
import re
import numpy as np

from chart_live import ChartLiveFlow
from chart_policy import ChartOptions, ChartSelection
from costume_unlock import FlowError, normalized
from mining_policy import DIFFICULTIES, star_state, pending_difficulties
from song_catalog import BY_ID, available_difficulties


class MiningLiveFlow(ChartLiveFlow):
    def __init__(self, context, options, output):
        super().__init__(context, ChartOptions.parse(dict(jitter='precise', fire=options.fire,
                                                         shortage=options.shortage)), output)
        self.mining = options
        self.report['options'] = dict(jitter='precise',fire=options.fire,shortage=options.shortage)
        self.report.update(mining_options={**asdict(options), 'stars': sorted(options.stars)},
                           scanned=[], skipped=[], full_combos=[], attempted=0)
        self.configured = False

    def limited(self):
        return self.mining.max_rounds is not None and self.report['attempted'] >= self.mining.max_rounds

    def result_modals(self):
        # The JP song title can make OCR read the Chinese 一 as a long vowel ー.
        if self.hit_text([175,48,875,54],r'达成(?:报酬|奖励)[一ー—-]览'):
            hit = self.hit_text([510,580,260,80],'^关闭$')
            if hit:
                self.tap_hit(hit)
                return True
        return super().result_modals()

    def song_stars(self, song):
        if not self.selected_song_matches(song):
            raise FlowError('读取星星前选中歌曲不符')
        return {d: star_state(self.image[312:321, x-3:x+4])
                for d, x in zip(DIFFICULTIES, (440,467,494,521,548))
                if d in available_difficulties(song)}

    def open_all_songs(self, from_top=True):
        self.navigate_menu()
        self.open_page('LV_FreeEntry', 'LV_SongPage')
        for _ in range(12):
            self.snap()
            hit = self.hit_text([0,106,180,95], '^所有$')
            if hit:
                self.tap_hit(hit)
                break
            self.swipe(85,210,650)
        else:
            raise FlowError('未找到所有歌曲分类')
        self.tap(1116,55)
        self.wait('MN_SongFilter')
        self.tap(1165,44)  # Restore all filters, including difficulty/level/status.
        self.tap(963,652)
        self.wait('LV_SongPage')
        if not from_top:
            return
        previous = None
        for _ in range(250):
            self.snap()
            area = self.image[105:703,201:565].astype(float)
            if previous is not None and np.mean(np.abs(area-previous)) < 1:
                return
            previous = area
            self.swipe(385,220,650)
        raise FlowError('歌曲列表未能滚动到顶部')

    def next_song(self, seen):
        self.open_all_songs(from_top=not seen)
        previous = None
        for _ in range(1200):
            self.wait('LV_SongPage')
            candidates = []
            for hit in self.ocr([201,105,365,590]):
                matches = [s for s in BY_ID.values() if self.title_matches(hit.text,s)]
                if matches and any(s['id'] not in seen for s in matches):
                    candidates.append((hit,matches))
            if candidates:
                hit, matches = candidates[0]
                self.tap_hit(hit)
                self.wait('LV_SongPage')
                self.pause(.5)
                self.snap()
                selected = [s for s in matches if self.selected_song_matches(s)]
                if len(selected) != 1:
                    raise FlowError('歌曲名称/乐队无法唯一确认，停止扫描')
                song = selected[0]
                seen.add(song['id'])
                states = self.song_stars(song)
                self.pause(.2)
                self.snap()
                if self.song_stars(song) != states:
                    raise FlowError('歌曲星星状态不稳定')
                self.report['scanned'].append({'song_id':song['id'],'stars':states})
                pending = [ChartSelection.parse(song['id'],d)
                           for d in pending_difficulties(states,self.mining.difficulties)]
                if any(state == 'unknown' for state in states.values()):
                    self.report['skipped'].append({'song_id':song['id'],'reason':'unknown_star'})
                if pending:
                    return pending
                previous = None
                continue
            area = self.image[105:703,201:565].astype(float)
            if previous is not None and np.mean(np.abs(area-previous)) < 1:
                return []
            previous = area
            self.swipe(385,650,220)
        raise FlowError('歌曲扫描达到保护上限，未确认扫描完成')

    def perform(self, selection):
        _, metadata = self.store.get(selection)
        if not self.configured:
            self.configure_stage()
            self.configured = True
        if not self.refill_fire(self.settings.fire):
            self.report['status'] = 'insufficient_fire'
            self.home()
            return None
        self.configure_fire(self.settings.fire)
        self.wait_ready()
        row = {**asdict(selection), 'status':'preparing','fire':self.settings.fire}
        self.report['rounds'].append(row)
        self.report['attempted'] += 1
        print(f'[挖矿] 第 {self.report["attempted"]} 次：{selection.song["title"]} '
              f'{selection.difficulty.upper()}，{self.settings.fire} 火',flush=True)
        self.play_chart(1, selection, metadata, self.settings.fire, row)
        self.await_chart_result(1,row)
        self.settle_results()
        self.report['completed_rounds'] += 1
        return row

    def run(self):
        if not self.mining.difficulties:
            self.report['status'] = 'no_difficulties_selected'
            self.home()
            return
        seen = set()
        while not self.limited():
            pending = self.next_song(seen)
            if not pending:
                self.report['status'] = 'scan_finished'
                self.home()
                return
            for selection in pending:
                for attempt in range(3):
                    if self.limited():
                        break
                    self.navigate_menu()
                    self.open_page('LV_FreeEntry','LV_SongPage')
                    self.find_song(selection.song)
                    if self.song_stars(selection.song)[selection.difficulty] == 'full_combo':
                        break
                    self.select_song(selection)
                    self.wait_ready()
                    row = self.perform(selection)
                    if row is None:
                        return
                    self.navigate_menu()
                    self.open_page('LV_FreeEntry','LV_SongPage')
                    self.find_song(selection.song)
                    state = self.song_stars(selection.song)[selection.difficulty]
                    row['star_after'] = state
                    if state == 'full_combo':
                        self.report['full_combos'].append(asdict(selection))
                        break
                    if state == 'unknown':
                        raise FlowError('结算后无法确认星星颜色')
                else:
                    self.report['skipped'].append({**asdict(selection),'reason':'three_attempts_without_fc'})
        self.report['status'] = 'max_rounds_reached'
        self.home()


class ChallengeMiningFlow(MiningLiveFlow):
    def select_stage_kind(self):
        self.wait('MN_ChallengeSelect')
        expected = 'MN_MainStage' if self.mining.stage == 'main' else 'MN_SpecialStage'
        if not self.reco(expected):
            self.tap(350,145)
            self.wait(expected)
        if self.mining.stage == 'special':
            self.tap(1225,155)
            self.wait('MN_SpecialBandFilter')
            self.tap(438,294)
            self.snap()
            if not self.pink(self.image[268:279,400:470]):
                raise FlowError('特别舞台乐队筛选未恢复全部')
            self.tap(640,549)
            self.wait('MN_ChallengeSelect')

    def challenge_cards(self):
        from PIL import Image
        for hit in self.ocr([260,104,80,610],r'\d+\s*/\s*\d+'):
            y = hit.box[1]+hit.box[3]//2
            if not 120 < y < 675:
                continue
            signature = np.asarray(Image.fromarray(self.image[y-22:y+24,40:155]).resize((30,12))).astype(float)
            current, maximum = map(int,re.search(r'(\d+)\s*/\s*(\d+)',hit.text).groups())
            yield y,signature,current,maximum

    def restore_challenge(self):
        # Returning through home resets the challenge category and selection.
        # Restore both before comparing levels; another band's 30 is not this 9's next stage.
        self.select_stage_kind()
        for _ in range(5):
            self.swipe(200,210,625)
        for _ in range(150):
            self.wait('MN_ChallengeSelect')
            for y,signature,_,_ in self.challenge_cards():
                if np.mean(np.abs(signature-self.challenge_signature)) < 12:
                    self.tap(200,y)
                    self.tap(1070,648)
                    self.wait('MN_StageSelect')
                    return
            before = self.image[160:710,25:340].astype(float)
            self.swipe(200,640,230)
            self.snap()
            if np.mean(np.abs(before-self.image[160:710,25:340])) < 1:
                break
        raise FlowError('结算后未找到同一个舞台挑战，不比较其他挑战的等级')

    def area_modals(self):
        if self.reco('MN_AreaChanged'):
            self.click('MN_AreaChangedOK')
            return True
        if self.reco('MN_AreaChange'):
            self.click('MN_AreaChangeConfirm')
            return True
        return False

    def monitor_chart_worker(self, destination):
        if (destination/'stage_started').exists():
            return
        now = time.monotonic()
        if now-getattr(self,'_last_start_check',0) < .5:
            return
        self._last_start_check = now
        self.snap()
        if self.hit_text([340,270,610,120],r'编成的成员与条件不符合'):
            self.save_frame('invalid_challenge_members.png')
            raise FlowError('舞台挑战编组不符合成员条件，未进入演出；请检查推荐编组结果')
        if self.area_modals():
            self._area_during_start = True
        elif getattr(self,'_area_during_start',False) and self.ready():
            self._area_during_start = False
            # A confirmed area change can return to ready instead of starting.
            if self.fire_preview()[0]-self.fire_preview()[1] != self.settings.fire:
                raise FlowError('区域道具更换后火数预览发生变化')
            self.tap(1130,616)

    def ready(self, index=1):
        self.snap()
        return bool(self.reco('MN_ChallengeHeader') and self.reco('MN_BandConfirm'))

    def verify_chart_start(self, index, selection, amount):
        self.wait_ready()
        if not self.title_matches(self.text([220,541,600,38]),selection.song):
            raise FlowError('舞台挑战歌曲与谱面不符')
        if normalized(self.text([110,560,110,35])).lower() != selection.difficulty:
            raise FlowError('舞台挑战难度与谱面不符')
        before, after = self.fire_preview()
        self.pause(.15)
        self.snap()
        balance = self.fire_balance()
        if self.fire_preview() != (before,after) or before-after != amount or balance < before:
            raise FlowError('舞台挑战火数预览不符')
        return balance

    def configure_stage(self):
        # Challenge ready pages have no AUTO/MV controls; the settings dialog is shared.
        # The parent checks the absent controls and leaves them untouched.
        super().configure_stage()

    def selected_level(self):
        self.wait('MN_StageSelect')
        # Exclude the stars beneath the label; they otherwise merge with digits.
        for hit in self.ocr([35,190,150,40], r'^舞台\s*\d+$'):
            return int(re.search(r'\d+',hit.text)[0])
        raise FlowError('无法读取当前选中舞台等级')

    def advance_level(self, previous):
        self.wait('MN_StageSelect')
        current = self.selected_level()
        if current > previous+1:
            raise FlowError('结算后舞台等级跨度异常，不自动推进')
        if current == previous+1:
            return True
        for hit in self.ocr([35,98,170,40],r'^舞台\s*\d+$'):
            level = int(re.search(r'\d+',hit.text)[0])
            if level != previous+1:
                continue
            y = hit.box[1]+hit.box[3]//2
            if self.reco('MN_StageLock',roi=[218,max(95,y-25),55,55]):
                return False
            self.tap_hit(hit)
            if self.selected_level() != level:
                raise FlowError('下一舞台选择未确认')
            return True
        return False

    def recommend(self):
        self.click('MN_Recommend')
        self.wait('MN_Recommended')
        missing=bool(self.hit_text([400,265,485,80],r'符合乐队编成条件的成员不足'))
        if not missing and not self.hit_text([400,300,490,95],r'已按照.*推荐(?:编|组)成了乐队'):
            raise FlowError('未确认推荐编组成功或成员不足，不开始演出')
        close=self.hit_text([505,482,275,85],'^关闭$')
        if not close:
            raise FlowError('推荐编组结果未找到关闭按钮')
        if missing:
            self.save_frame(f'challenge_members_missing_{len(self.report["skipped"])+1}.png')
        self.tap_hit(close)
        closes=1
        for _ in range(10):
            self.snap()
            if self.reco('MN_Recommended'):
                expected=r'符合乐队编成条件的成员不足' if missing else r'已按照.*推荐(?:编|组)成了乐队'
                close=self.hit_text([505,482,275,85],'^关闭$')
                if closes>=3 or not close or not self.hit_text([400,265,490,140],expected):
                    raise FlowError('推荐编组结果弹窗未确认关闭，不操作背景页面')
                self.tap_hit(close)
                closes+=1
                continue
            if self.area_modals():
                continue
            self.require_clear_notification_overlay()
            self.wait_ready()
            return not missing
        raise FlowError('区域道具确认未结束')

    def result_modals(self):
        if super().result_modals():
            return True
        if self.reco('MN_NextStage'):
            self.report['next_unlocked'] = True
            self.click('MN_NextStageOK')
            return True
        return False

    def settle_results(self):
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            self.snap()
            if self.result_modals():
                continue
            if self.reco('MN_StageSelect'):
                return
            if self.reco('CU_HomeBand') or self.reco('LV_Menu'):
                self.navigate_menu()
                self.click('MN_ChallengeEntry')
                self.restore_challenge()
                return
            if self.reco('MN_ChallengeSelect'):
                self.restore_challenge()
                return
            if self.reco('LV_TalkSkipConfirm'):
                self.tap(770,448)
            elif self.reco('LV_TalkSkip'):
                self.click('LV_TalkSkip')
            elif self.reco('LV_TalkMenu'):
                self.click('LV_TalkMenu')
            elif self.reco('LV_RankUp'):
                self.tap(640,526)
            elif self.reco('LV_RewardModal') or self.reco('LV_RankReward'):
                self.tap(640,602)
            elif self.login_reward_page() or any(self.reco(n) for n in ('LV_Rewards','LV_Experience','LV_EventResult')) or self.hit_text(
                    [680,314,170,160],'GREAT|GOOD|BAD|MISS') or self.hit_text([110,270,175,180],'获得活动|获得徽章|演出报酬'):
                hit = self.hit_text([940,600,280,105],'^下一步$|^确定$')
                if hit:
                    self.tap_hit(hit)
            self.pause(.5)
        raise FlowError('舞台挑战结算未返回舞台选择')

    def run(self):
        self.navigate_menu()
        self.click('MN_ChallengeEntry')
        self.select_stage_kind()
        for _ in range(5):
            self.swipe(200,210,625)
        seen = []
        stagnant = 0
        scans = 0
        while not self.limited():
            scans += 1
            if scans > 400:
                raise FlowError('挑战列表未收敛，停止重复扫描')
            self.wait('MN_ChallengeSelect')
            target = None
            for y,signature,current,maximum in self.challenge_cards():
                if any(np.mean(np.abs(signature-old)) < 12 for old in seen):
                    continue
                seen.append(signature)
                if current < maximum:
                    target = y
                    self.challenge_signature = signature
                    break
            if target is None:
                before = self.image[160:710,25:340].astype(float)
                self.swipe(200,640,230)
                self.snap()
                stagnant = stagnant+1 if np.mean(np.abs(before-self.image[160:710,25:340])) < 1 else 0
                if stagnant >= 2:
                    self.report['status'] = 'challenges_finished'
                    self.home()
                    return
                continue
            self.tap(200,target)
            self.tap(1070,648)
            for _ in range(30):
                if self.limited():
                    break
                level = self.selected_level()
                title = self.text([460,124,480,44])
                songs = [s for s in BY_ID.values() if self.title_matches(title,s)]
                if len(songs) != 1:
                    raise FlowError(f'舞台歌曲无法唯一识别：{title}')
                selection = ChartSelection.parse(songs[0]['id'],'expert')
                self.choose_difficulty_exact('expert')
                self.store.get(selection)
                self.tap(1070,648)
                self.wait_ready()
                if not self.recommend():
                    self.report['skipped'].append({**asdict(selection), 'stage_level':level,
                                                  'stage_kind':self.mining.stage,
                                                  'reason':'insufficient_eligible_members'})
                    print(f'[挖矿挑战] {selection.song["title"]}：符合条件的成员不足，跳过该挑战',flush=True)
                    self.back()
                    self.wait('MN_StageSelect')
                    break
                self.report['next_unlocked'] = False
                row = self.perform(selection)
                if row is None:
                    return
                row['stage_level'] = level
                row['stage_kind'] = self.mining.stage
                row['next_unlocked'] = self.advance_level(level)
                if not row['next_unlocked']:
                    break
            self.back()
            self.wait('MN_ChallengeSelect')
        self.report['status'] = 'max_rounds_reached'
        self.home()
