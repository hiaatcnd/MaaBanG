"""Chart-driven live task: selection, settings, item refill, playback and tours."""
from dataclasses import asdict
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
from maa.custom_action import CustomAction

from auto_live import LiveFlow
from chart_policy import ChartOptions, ChartSelection, OPTION_DEFAULTS, OPTION_NODES
from chart_store import ChartStore
from costume_unlock import FlowError, normalized
from live_policy import DIFFICULTIES
from song_catalog import BY_ID


from song_navigation import title_key


class ChartLiveFlow(LiveFlow):
    def __init__(self, context, options, output):
        # Reuse navigation and UI primitives without built-in-auto quota policies.
        super().__init__(context,SimpleNamespace(mode='free' if options.mode=='free' else 'tour'))
        self.settings = options
        self.output = Path(output)
        self.output.mkdir(parents=True,exist_ok=True)
        self.store = ChartStore()
        self.report = {'status':'running','options':asdict(options),'rounds':[],
                       'completed_rounds':0,'refills':[]}
        self.selection = None

    def save_frame(self, name):
        from PIL import Image
        Image.fromarray(self.image[:,:,::-1]).save(self.output/name)

    def choose_difficulty_exact(self, requested, centers=(714,826,939,1051,1185), y=540):
        self.tap(centers[DIFFICULTIES.index(requested)],y)
        self.snap()
        if self.selected_difficulty(centers,y) != requested:
            raise FlowError(f'无法选择 {requested.upper()}；不自动替换为其他难度')

    def select_song(self, selection):
        self.find_song(selection.song)
        if selection.difficulty!='expert':
            self.clear_song_level_filter(selection.song)
        self.choose_difficulty_exact(selection.difficulty)
        self.tap(1070,648)

    def fixed_selections(self):
        result=[]
        for i,difficulty in enumerate(self.settings.difficulties):
            text=self.text([30+413*i,345,385,37])
            matches=[song for song in BY_ID.values() if self.title_matches(text,song)]
            if len(matches)!=1:
                raise FlowError(f'课题巡演第 {i+1} 首无法唯一识别：{text}')
            result.append(ChartSelection.parse(matches[0]['id'],difficulty))
            centers=tuple(x+413*i for x in (66,143,218,294,377))
            self.choose_difficulty_exact(difficulty,centers,415)
        return tuple(result)

    def prepare_round(self):
        self.navigate_menu()
        if self.settings.mode=='free':
            selections=self.settings.selections
            charts=[self.store.get(s) for s in selections]
            self.open_page('LV_FreeEntry','LV_SongPage')
            self.select_song(selections[0])
        else:
            self.open_page('LV_TourEntry','LV_TourHome')
            if self.settings.mode=='tour_fixed':
                self.open_page('CL_TourFixedEntry','CL_TourFixedSetup')
                selections=self.fixed_selections()
                charts=[self.store.get(s) for s in selections]
            else:
                selections=self.settings.selections
                charts=[self.store.get(s) for s in selections]
                self.open_page('LV_TourFree','LV_TourSetup')
                for i,selection in enumerate(selections):
                    self.wait('LV_TourSetup')
                    self.tap(324+412*i,256)
                    self.select_song(selection)
                self.wait('LV_TourSetup')
                for i,selection in enumerate(selections):
                    if not self.title_matches(self.text([30+413*i,348,385,36]),selection.song):
                        raise FlowError(f'自由巡演第 {i+1} 首选曲未确认')
            self.tap(1125,640)
        self.wait_ready()
        return selections,charts

    def settings_row(self, pattern, x=190, width=895):
        for _ in range(10):
            self.snap()
            hit=self.hit_text([x,200,width,330],pattern)
            if hit:
                y=hit.box[1]+hit.box[3]//2+54
                if y<522:
                    return y
            self.swipe(1090,490,350)
        raise FlowError(f'未找到演出设置：{pattern}')

    def set_number(self, roi, target, minus, plus, scale=1, limit=100):
        for _ in range(limit):
            self.snap()
            text=normalized(self.text(roi)).replace('%','')
            try:
                current=round(float(text)*scale)
            except ValueError:
                raise FlowError(f'无法读取设置值：{text}')
            if current==target:
                return
            self.tap(*(plus if current<target else minus))
        raise FlowError('设置调整未收敛')

    def configure_stage(self):
        self.snap()
        # The selected checkbox is pink; gray is OFF, even when it shows a tick.
        if self.pink(self.image[637:663,487:513]):
            self.tap(500,650)
        mode=normalized(self.text([175,633,80,32])).upper()
        if mode=='ON':
            self.tap(145,650)
        self.tap(951,650)
        self.tap(295,155)
        # Restore scroll position, then adjust the decimal speed with bounded feedback.
        for _ in range(3):
            self.swipe(1090,230,530)
        for _ in range(40):
            self.snap()
            value=normalized(self.text([365,288,110,55]))
            try:
                difference=980-round(float(value)*100)
            except ValueError:
                raise FlowError(f'无法读取音符速度：{value}')
            if difference==0:
                break
            step=100 if abs(difference)>=100 else 10 if abs(difference)>=10 else 1
            x=({100:635,10:574,1:513} if difference>0 else {100:206,10:267,1:328})[step]
            self.tap(x,312)
        else:
            raise FlowError('无法将音符速度调整至 9.80')
        self.tap(522,441)  # note size default 100%
        self.snap()
        if normalized(self.text([244,422,111,41]))!='100%':
            raise FlowError('音符大小未恢复为 100%')
        self.save_frame('settings_speed.png')
        y=self.settings_row('判定调节',190,400)
        self.set_number([244,y-24,110,46],0,(205,y),(391,y))
        self.set_number([707,y-24,110,46],0,(668,y),(851,y))
        y=self.settings_row('节奏图标的出现位置')
        self.tap(645,y)
        y=self.settings_row('镜像',795,290)
        self.tap(917,y)  # mirror OFF
        self.tap(610,y)  # color assistance OFF
        self.snap()
        if not all(self.pink(self.image[y-8:y+9,x-8:x+9]) for x in (917,610)):
            raise FlowError('镜像或色觉辅助未关闭')
        self.tap(527,155)
        for _ in range(3):
            self.swipe(1090,230,530)
        self.tap(303,317)  # 3D effects OFF
        self.tap(906,317)  # lightweight animation
        self.snap()
        if not self.pink(self.image[309:326,897:914]):
            raise FlowError('轻量模式未选中')
        self.tap(750,155)
        for _ in range(3):
            self.swipe(1090,230,530)
        self.tap(997,225)  # default skin restores the calibrated cyan/green lane layout
        self.tap(640,601)
        self.wait_ready()
        self.report['stage_settings']={'speed':9.8,'note_size':100,'mirror':False,
                                      'color_assist':False,'light_mode':True,'skin':'default'}

    def read_integer(self, roi):
        text=normalized(self.text(roi))
        if not re.fullmatch(r'\d{1,5}',text):
            raise FlowError(f'无法确认数量：{text}')
        return int(text)

    def refill_fire(self, needed):
        self.snap()
        balance=self.fire_balance()
        if balance>=needed:
            return True
        if self.settings.shortage!='items':
            return False
        self.tap(1150,39)
        self.snap()
        item=self.hit_text([380,418,238,47],'^道具$')
        if not item:
            raise FlowError('未确认道具回复入口')
        self.tap_hit(item)
        self.snap()
        if not self.hit_text([376,75,260,44],'回复量选择'):
            raise FlowError('未打开道具数量页')
        before=self.read_integer([708,471,57,42])
        after=self.read_integer([820,471,69,42])
        if before!=balance or after!=before:
            raise FlowError('道具回复预览初始值不符')
        used=[]
        # Prefer +1 drinks to minimize surplus; +10 drinks are the fallback.
        for name,y,count_roi,gain in [('小型',227,[430,242,59,32],1),
                                      ('普通',376,[441,390,48,31],10)]:
            self.snap()
            available=self.read_integer(count_roi)
            count=min(available,max(0,(needed-after+gain-1)//gain))
            for _ in range(count):
                self.tap(766,y)
            if count:
                used.append({'item':name,'count':count,'gain':gain,'inventory_before':available})
                after+=count*gain
        if after<needed:
            self.tap(506,602)
            return False
        self.snap()
        actual=self.read_integer([820,471,69,42])
        if actual!=after or self.read_integer([708,471,57,42])!=before:
            raise FlowError('回复道具预览与所选数量不符')
        row={'before':before,'after':after,'items':used,'status':'confirmation_pending'}
        self.report['refills'].append(row)
        self.tap(770,602)
        self.snap()
        if '将要回复LIVEBOOST' not in normalized(self.text([488,302,306,72])).upper():
            raise FlowError('未确认道具回复确认框，不提交')
        confirmation=self.hit_text([652,415,236,66],'^回复$')
        if not confirmation:
            raise FlowError('未找到道具回复确认按钮')
        row['status']='submitted'
        self.tap_hit(confirmation)  # single, explicitly enabled item consumption; no star path
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            self.snap()
            text=normalized(self.text([440,325,410,53]))
            restored=re.search(r'已回复(\d+)',text)
            if restored:
                if int(restored[1])!=after-before:
                    raise FlowError('道具实际回复量不符')
                button=self.hit_text([520,415,239,69],'^关闭$')
                if not button:
                    raise FlowError('未找到回复结果关闭按钮')
                self.tap_hit(button)
                self.snap()
                observed=self.fire_balance()
                if observed<after:
                    raise FlowError('回复后的火余额不足')
                row.update(status='confirmed',actual_recovered=int(restored[1]),observed_balance=observed)
                return True
            self.pause(.5)
        raise FlowError('道具回复结果未确认，不重复提交')

    def verify_chart_start(self, index, selection, amount):
        self.wait_ready(index)
        # Long free-live titles extend beyond x=660; stop before the auto button.
        rect=[220,541,570,38] if self.settings.mode=='free' else [111,541,420,38]
        title=self.text(rect)
        if not self.title_matches(title,selection.song):
            raise FlowError(f'开演前歌曲与谱面不一致：识别到 {title!r}，预期 {selection.song["title"]!r}')
        if self.settings.mode=='free':
            if normalized(self.text([113,561,104,33])).lower()!=selection.difficulty:
                raise FlowError('开演前难度与谱面不一致')
        else:
            centers=tuple(x+413*(index-1) for x in (66,143,218,294,377))
            if self.selected_difficulty(centers,376)!=selection.difficulty:
                raise FlowError('巡演当前曲目难度未确认')
        if self.reco('LV_AutoOn'):
            self.tap(883,580)
            self.wait_ready(index)
        if not self.reco('LV_AutoOff'):
            raise FlowError('未确认游戏内置自动已关闭')
        before,after=self.fire_preview()
        self.pause(.15)
        self.snap()
        balance=self.fire_balance()
        # Natural recovery can update the top counter one frame before the ready
        # page's cached preview. A larger balance does not change the selected cost.
        if self.fire_preview()!=(before,after) or before-after!=amount or balance<before or balance<amount:
            raise FlowError('开演前火数预览不符')
        return balance

    def play_chart(self, index, selection, metadata, amount, row):
        online=self.settings.mode == 'team'
        destination=self.output/(f'attempt{self.report["attempts"]}_playback' if online else
                                 f'round{self.report["completed_rounds"]+1}_song{index}')
        destination.mkdir()
        config={'controller':self.controller.info,'chart':metadata,
                'jitter':self.settings.jitter,'seed':secrets.randbits(32),
                'start_mode':'online' if online else 'click'}
        (destination/'config.json').write_text(json.dumps(config),encoding='utf8')
        log=(destination/'worker.log').open('w',encoding='utf8')
        process=subprocess.Popen([sys.executable,'-X','utf8',str(Path(__file__).with_name('chart_worker.py')),
                                  str(destination/'config.json')],stdout=log,stderr=subprocess.STDOUT,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+60
            while not (destination/'armed').exists():
                self.check_stop()
                if online:
                    self.monitor_online_worker(destination)
                if process.poll() is not None or time.monotonic()>deadline:
                    raise FlowError(f'演奏进程准备失败，详见 {destination}/worker.log')
                self.pause(.1)
            row['fire_before']=self.verify_chart_start(index,selection,amount)
            row['status']='submitted'
            self.save_frame(f'ready_attempt{self.report["attempts"]}.png' if online else
                            f'ready_{self.report["completed_rounds"]+1}_{index}.png')
            (destination/'start').write_text('start')
            if online:
                self.submit_online_ready(selection)
            deadline=time.monotonic()+metadata['duration']+(270 if online else 90)
            while process.poll() is None:
                self.check_stop()
                if online:
                    self.monitor_online_worker(destination)
                else:
                    self.monitor_chart_worker(destination)
                if time.monotonic()>deadline:
                    raise FlowError('演奏进程超时')
                self.pause(.1)
            playback=json.loads((destination/'playback.json').read_text(encoding='utf8'))
            row['playback']=str(destination/'playback.json')
            if process.returncode!=0 or playback['status']!='input_complete':
                if online:
                    self.handle_online_playback_error(playback)
                raise FlowError(playback.get('error','演奏进程失败'))
        finally:
            if process.poll() is None:
                (destination/'stop').write_text('stop')
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
            log.close()

    def monitor_chart_worker(self, destination):
        """Optional handling of mode-specific confirmations before notes start."""
        pass

    def result_modals(self):
        return self.dismiss_daily_reward()

    def await_chart_result(self, index, row):
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            self.pause(1)
            self.snap()
            if self.result_modals():
                continue
            if self.hit_text([190,200,350,90],'演出失败'):
                row['status']='game_failed'
                raise FlowError('演出失败，已停止；不会使用星石继续')
            if self.settings.mode!='free' and index<3:
                if self.reco('LV_TourHeader') and self.tour_index()==index+1:
                    row['status']='next_song_ready'
                    return
            elif self.hit_text([680,314,170,160],'GREAT|GOOD|BAD|MISS'):
                button=self.hit_text([940,602,274,100],'^下一步$')
                if button:
                    self.save_frame(f'judgment_{self.report["completed_rounds"]+1}_{index}.png')
                    if self.settings.mode=='free':
                        row['status']='result_confirmed'
                        row['judgment_text']=self.text([680,270,250,211])
                    else:
                        # Tour result pages start with song 1 after all three inputs.
                        # Do not attribute that first page's judgments to song 3.
                        row['status']='tour_results_started'
                    self.tap_hit(button)
                    return
            elif self.reco('LV_TourSummary'):
                row['status']='tour_result_confirmed'
                self.save_frame(f'tour_result_{self.report["completed_rounds"]+1}.png')
                return
            elif self.reco('LV_Rewards') or self.hit_text([100,420,145,43],'演出报酬'):
                button=self.hit_text([940,602,274,100],'^下一步$')
                if button:
                    # A reward modal can cover the judgment page before it is
                    # observed. Rewards still prove this submitted live ended.
                    row['status']='result_confirmed'
                    row['result_evidence']='rewards_page'
                    self.save_frame(f'rewards_{self.report["completed_rounds"]+1}_{index}.png')
                    return
        raise FlowError('未确认演出结果，不重新开演')

    def settle_results(self):
        deadline=time.monotonic()+180
        page=0
        while time.monotonic()<deadline:
            self.snap()
            if self.result_modals():
                continue
            if self.reco('LV_RankUp'):
                self.tap(640,526);continue
            if self.reco('LV_RewardModal') or self.reco('LV_RankReward'):
                self.tap(640,602);continue
            if self.reco('LV_TalkSkipConfirm'):
                self.tap(770,448);continue
            if self.reco('LV_TalkSkip'):
                self.click('LV_TalkSkip');continue
            if self.reco('LV_TalkMenu'):
                self.click('LV_TalkMenu');continue
            self.require_clear_notification_overlay()
            if any(self.reco(n) for n in ('CU_HomeBand','LV_Menu','LV_TourHome','LV_TourSetup','LV_SongPage')):
                return
            manual=bool(self.hit_text([680,314,170,160],'GREAT|GOOD|BAD|MISS'))
            known=manual or any(self.reco(n) for n in ('LV_ScoreAuto','LV_TourSummary','LV_Rewards',
                                                       'LV_Experience','LV_LoginReward'))
            known=known or bool(self.hit_text([110,270,175,148],'获得活动|获得徽章'))
            known=known or bool(self.reco('LV_EventResult'))
            known=known or self.login_reward_page()
            if known:
                button=self.hit_text([940,602,274,100],'^下一步$|^确定$|^确认$')
                if button:
                    if manual:
                        page+=1
                        self.save_frame(f'result_{self.report["completed_rounds"]+1}_{page}.png')
                    self.tap_hit(button)
                    self.pause(1)
                    continue
            self.pause(1)
        raise FlowError('未识别谱面演出结算页面，保留现场')

    def run(self):
        stage_configured=False
        while self.settings.max_rounds is None or self.report['completed_rounds']<self.settings.max_rounds:
            selections,charts=self.prepare_round()
            if not stage_configured:
                self.configure_stage()
                stage_configured=True
            if not self.refill_fire(self.settings.fire*len(selections)):
                self.report['status']='insufficient_fire'
                self.navigate_menu()
                self.home()
                self.report['returned_home']=True
                return
            row={'songs':[],'status':'running'}
            self.report['rounds'].append(row)
            for index,(selection,(_,metadata)) in enumerate(zip(selections,charts),1):
                self.wait_ready(index)
                if not self.refill_fire(self.settings.fire):
                    self.report['status']='insufficient_fire'
                    return
                self.configure_fire(self.settings.fire)
                self.wait_ready(index)
                song={'index':index,**asdict(selection),'fire':self.settings.fire}
                row['songs'].append(song)
                print(f'[Maa代打演出] 第 {self.report["completed_rounds"]+1} 轮 / 第 {index} 首：'
                      f'{selection.song["title"]} {selection.difficulty.upper()}',flush=True)
                self.play_chart(index,selection,metadata,self.settings.fire,song)
                self.await_chart_result(index,song)
            self.settle_results()
            row['status']='finished'
            self.report['completed_rounds']+=1
        self.report['status']='max_rounds_reached'
        self.home()


class ChartLive(CustomAction):
    def run(self, context, argv):
        destination=Path('debug/chart_live')/time.strftime('%Y%m%d-%H%M%S')
        destination.mkdir(parents=True,exist_ok=True)
        flow=None
        report={'status':'error'}
        try:
            values={}
            for key,node in OPTION_NODES.items():
                data=context.get_node_data(node)
                values[key]=(data or {}).get('attach',{}).get('value',OPTION_DEFAULTS[key])
            options=ChartOptions.parse(values)
            if options.mode == 'team':
                from online_live import OnlineLiveFlow
                flow=OnlineLiveFlow(context,options,destination)
            else:
                flow=ChartLiveFlow(context,options,destination)
            report=flow.report
            flow.run()
            return True
        except Exception as exc:
            report.update(status='error',error=str(exc))
            print(f'[Maa代打演出] 已停止：{exc}',flush=True)
            return False
        finally:
            (destination/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            if flow and flow.image is not None and report['status']=='error':
                try:
                    flow.snap()
                except Exception:
                    pass
                flow.save_frame('error.png')
