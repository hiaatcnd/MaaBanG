"""Run only the game's built-in auto mode, selecting unlocked songs by band."""
import json
from pathlib import Path
import re
import time

import numpy as np
from maa.custom_action import CustomAction

from costume_unlock import FlowError, normalized
from daily_tasks import DailyFlow
from song_catalog import resolve_song
from song_navigation import SongNavigationMixin
from live_policy import (LiveOptions, DIFFICULTIES, parse_auto_remaining,
                         fire_for_song, round_stop_reason)

OPTION_NODES = {key: 'LV_' + suffix for key, suffix in (
    ('mode','Mode'), ('song','Song'), ('difficulty','Difficulty'),
    ('fire','Fire'), ('shortage','Shortage'), ('max_rounds','MaxRounds'))}


class LiveFlow(SongNavigationMixin, DailyFlow):
    def __init__(self, context, options):
        super().__init__(context)
        self.options = options
        self.report = {'status':'running', 'options':vars(options), 'rounds':[], 'completed_rounds':0}

    def pause(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_stop()
            time.sleep(min(.25, max(0,deadline-time.monotonic())))

    @staticmethod
    def pink(area):
        b,g,r = (area[:,:,i].astype(float) for i in range(3))
        return float(np.mean((r>180) & (r-g>60) & (g<170))) > .45

    def tour_index(self):
        text = normalized(self.text([125,52,250,42]))
        match = re.fullmatch(r'第([123])曲开始', text)
        return int(match[1]) if match else None

    def ready(self, index=1):
        self.snap()
        if self.dismiss_daily_reward():
            return False
        if self.options.mode == 'free':
            return bool(self.reco('LV_FreeReady'))
        return bool(self.reco('LV_TourHeader')) and self.tour_index() == index

    def wait_ready(self, index=1, timeout=25):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.ready(index): return
            self.pause(.5)
        raise FlowError(f'未到达第 {index} 首准备页')

    def open_page(self, button, destination):
        # Non-spending navigation only: a click during a closing animation may be lost.
        for _ in range(3):
            self.click(button)
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                self.snap()
                if self.reco(destination): return
                self.pause(.5)
            if not self.reco(button):
                raise FlowError(f'导航后出现未知页面：{destination}')
        raise FlowError(f'无法打开页面：{destination}')

    def navigate_menu(self):
        # Only unwind pre-live screens. Never abandon an in-progress tour automatically.
        for _ in range(5):
            self.snap()
            if self.reco('LV_Menu'): return
            if self.reco('LV_TourHeader') and self.tour_index() in (2,3):
                raise FlowError('当前有未完成巡演，请先完成或手动结束')
            if (self.reco('LV_SongPage') or self.reco('LV_FreeReady') or
                self.reco('LV_TourHome') or self.reco('LV_TourSetup') or self.tour_index()==1):
                self.back()
                continue
            self.home()
            self.open_page('LV_HomeEntry','LV_Menu')
        self.wait('LV_Menu')

    def selected_difficulty(self, centers, y):
        found=[]
        for difficulty,x in zip(DIFFICULTIES,centers):
            area=self.image[y-24:y+15,x-25:x+25].astype(float)
            if np.mean(area.max(axis=2)-area.min(axis=2)>75)>.25:
                found.append(difficulty)
        return found[0] if len(found)==1 else None

    def choose_difficulty(self):
        centers=(714,826,939,1051,1185)
        requested=self.options.difficulty
        self.tap(centers[DIFFICULTIES.index(requested)],540)
        self.snap()
        actual=self.selected_difficulty(centers,540)
        if actual != requested and requested=='special':
            self.tap(1051,540)
            self.snap()
            actual=self.selected_difficulty(centers,540)
            if actual=='expert':
                print('[自动演出] 所选歌曲无可用 SPECIAL，使用 EXPERT',flush=True)
        if actual != requested and not (requested=='special' and actual=='expert'):
            raise FlowError(f'难度选择未确认：{requested} / {actual}')
        return actual

    def choose_song(self):
        song=resolve_song(self.options.song_id or self.options.song)
        self.find_song(song)
        if self.options.difficulty!='expert':
            self.clear_song_level_filter(song)
        actual=self.choose_difficulty()
        self.tap(1070,648)
        return actual

    def prepare_round(self):
        self.navigate_menu()
        if self.options.mode=='free':
            self.open_page('LV_FreeEntry','LV_SongPage')
            difficulty=self.choose_song()
        else:
            self.open_page('LV_TourEntry','LV_TourHome')
            self.open_page('LV_TourFree','LV_TourSetup')
            difficulties=[]
            for x in (324,736,1148):
                self.wait('LV_TourSetup')
                self.tap(x,256)
                difficulties.append(self.choose_song())
            self.wait('LV_TourSetup')
            for i in range(3):
                title=normalized(self.text([30+413*i,348,385,36]))
                if title != normalized(self.options.song):
                    raise FlowError(f'巡演第 {i+1} 首歌名不符：{title}')
            if len(set(difficulties))!=1: raise FlowError('巡演三首难度不一致')
            difficulty=difficulties[0]
            self.tap(1125,640)
        self.wait_ready()
        return difficulty

    def remaining(self):
        return parse_auto_remaining(self.text([788,541,113,30]))

    def fire_balance(self):
        text=normalized(self.text([971,24,67,32]))
        match=re.fullmatch(r'(\d+)/(\d+)',text)
        if not match: raise FlowError(f'无法确认火数：{text}')
        return int(match[1])

    def configure_fire(self, amount):
        self.tap(850 if self.options.mode=='tour' else 857,650)
        self.wait('LV_FireDialog')
        self.tap(1002,133+77*amount)
        self.wait('LV_FireDialog')
        if not self.pink(self.image[124+77*amount:143+77*amount,993:1012]):
            raise FlowError('未确认指定火数的单选按钮')
        self.tap(640,641)

    def fire_preview(self):
        # The counter reflows for one/two digits. Split at the pink arrow,
        # which OCR otherwise reads as an extra 1.
        area=self.image[548:568,1058:1093].astype(float)
        b,g,r=(area[:,:,i] for i in range(3))
        _,xs=np.where((r>180)&(r-g>60)&(g<170))
        if len(xs)<10 or not 3<=int(xs.max()-xs.min())<=13:
            raise FlowError('未定位火数预览箭头')
        left,right=1058+int(xs.min()),1058+int(xs.max())
        before=normalized(self.text([1043,540,left-1043-2,33]))
        after=normalized(self.text([right+3,540,32,33]))
        if not re.fullmatch(r'\d{1,2}',before) or not re.fullmatch(r'\d{1,2}',after):
            raise FlowError(f'无法读取火数预览：{before} → {after}')
        return int(before),int(after)

    def verify_start(self, index, difficulty, amount, required_auto):
        self.wait_ready(index)
        title=normalized(self.text([111 if self.options.mode=='tour' else 220,541,
                                    420 if self.options.mode=='tour' else 440,38]))
        if title != normalized(self.options.song): raise FlowError(f'开演前歌名不符：{title}')
        if self.options.mode=='free':
            label=normalized(self.text([113,561,104,33])).lower()
            if label != difficulty: raise FlowError(f'开演前难度不符：{label}')
        else:
            centers=tuple(x+413*(index-1) for x in (66,143,218,294,377))
            if self.selected_difficulty(centers,376)!=difficulty:
                raise FlowError('巡演开演前难度不符')
        if not self.reco('LV_AutoOn'):
            if not self.reco('LV_AutoOff'): raise FlowError('未识别自动演出开关')
            self.tap(883,580)
            self.wait('LV_AutoOn')
        self.wait_ready(index)
        if not self.reco('LV_AutoOn'): raise FlowError('自动演出未开启')
        remaining=self.remaining()
        if remaining<required_auto: raise FlowError('自动演出剩余次数不足，不开演')
        balance=self.fire_balance()
        # Read the actual before/after preview immediately before the one spending click.
        before,after=self.fire_preview()
        self.pause(.15)
        self.snap()
        if self.fire_preview()!=(before,after): raise FlowError('火数预览读数不稳定')
        if before!=balance or before-after!=amount:
            raise FlowError(f'火数预览不一致：{balance} / {before} → {after}，设置 {amount}')
        return remaining,balance

    def dismiss_daily_reward(self):
        # This modal also appears BETWEEN tour songs. Its background remains
        # recognizable, but counters and controls are obscured until dismissed.
        if not self.reco('LV_DailyReward'):
            return False
        self.tap(640,544)
        self.pause(2)
        return True

    def wait_song(self, index):
        deadline=time.monotonic()+600
        # The game plays the notes. Poll at ten-second intervals without touching the stage.
        while time.monotonic()<deadline:
            self.pause(10)
            self.snap()
            if self.dismiss_daily_reward():
                continue
            if self.options.mode=='tour' and index<3:
                if self.reco('LV_TourHeader') and self.tour_index()==index+1:
                    return
            elif self.reco('LV_TourSummary') or self.reco('LV_ScoreAuto') or self.reco('LV_Rewards'):
                return
        raise FlowError('演出超过10分钟仍未确认结束，保留现场，不重新开演')

    def settle_results(self):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            self.snap()
            if self.dismiss_daily_reward():
                continue
            if self.reco('LV_RankUp'):
                self.tap(640,526)
                self.pause(2)
                continue
            if self.reco('LV_RewardModal') or self.reco('LV_RankReward'):
                self.tap(640,602)
                self.pause(2)
                continue
            if self.reco('LV_TalkSkipConfirm'):
                self.tap(770,448)
                self.pause(2)
                continue
            if self.reco('LV_TalkSkip'):
                self.click('LV_TalkSkip')
                continue
            if self.reco('LV_TalkMenu'):
                self.click('LV_TalkMenu')
                continue
            if (self.reco('CU_HomeBand') or self.reco('LV_Menu') or self.reco('LV_TourHome') or
                    self.reco('LV_TourSetup') or self.reco('LV_SongPage')):
                return
            if (self.reco('LV_ScoreAuto') or self.reco('LV_TourSummary') or
                    self.reco('LV_Rewards') or self.reco('LV_Experience') or self.reco('LV_LoginReward')):
                button=self.hit_text([940,602,274,100], '^下一步$|^确定$|^确认$')
                if button:
                    self.tap_hit(button)
                    self.pause(2)
                    continue
            self.pause(2)
        raise FlowError('未识别演出结算页面，保留现场')

    def run(self):
        while True:
            if (self.options.max_rounds is not None and
                    self.report['completed_rounds']>=self.options.max_rounds):
                self.report['status']='max_rounds_reached'
                self.home()
                self.report['returned_home']=True
                return
            difficulty=self.prepare_round()
            self.snap()
            remaining=self.remaining()
            fire=self.fire_balance()
            reason=round_stop_reason(self.options,self.report['completed_rounds'],remaining,fire)
            if reason:
                self.report.update(status=reason,auto_remaining=remaining,fire_remaining=fire)
                self.navigate_menu()
                self.home()
                self.report['returned_home']=True
                return
            row={'song':self.options.song,'difficulty':difficulty,'songs':[],'status':'running'}
            self.report['rounds'].append(row)
            for index in range(1,self.options.songs_per_round+1):
                self.wait_ready(index)
                amount=fire_for_song(self.options.fire,self.fire_balance(),self.options.shortage)
                if amount is None: raise FlowError('开演前火数减少，停止并保留现场')
                self.configure_fire(amount)
                before,balance=self.verify_start(index,difficulty,amount,self.options.songs_per_round-index+1)
                song={'index':index,'fire':amount,'fire_before':balance,'auto_before':before,'status':'submitted'}
                row['songs'].append(song)
                print(f'[自动演出] 第 {self.report["completed_rounds"]+1} 轮，第 {index} 首，{amount} 火，自动剩余 {before}',flush=True)
                self.tap(1127,626)  # Exactly one start submission; never retry an uncertain spend.
                self.wait_song(index)
                song['status']='result_confirmed'
                if index<self.options.songs_per_round:
                    after=self.remaining()
                    if after!=before-1: raise FlowError('自动次数未按预期减少，不继续下一首')
                    song['auto_after']=after
            self.settle_results()
            row['status']='finished'
            self.report['completed_rounds']+=1
            print(f'[自动演出] 第 {self.report["completed_rounds"]} 轮完成',flush=True)


class AutoLive(CustomAction):
    def run(self, context, argv):
        output=Path('debug/auto_live'); output.mkdir(parents=True,exist_ok=True)
        report={'status':'error'}
        flow=None
        try:
            values={}
            for key,node in OPTION_NODES.items():
                data=context.get_node_data(node)
                if not data or 'value' not in data.get('attach',{}):
                    raise ValueError(f'自动演出配置缺失：{key}')
                values[key]=data['attach']['value']
            flow=LiveFlow(context,LiveOptions.parse(values))
            report=flow.report
            flow.run()
            return True
        except Exception as exc:
            report.update(status='error',error=str(exc))
            print(f'[自动演出] 停止：{exc}',flush=True)
            return False
        finally:
            stamp=time.strftime('%Y%m%d-%H%M%S')
            (output/f'{stamp}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            if report['status']=='error' and flow is not None and flow.image is not None:
                from PIL import Image
                Image.fromarray(flow.image[:,:,::-1]).save(output/f'{stamp}.png')
