"""Team live state machine. A room attempt is distinct from a completed song."""
from task_logging import log
from dataclasses import asdict
from concurrent.futures import Future, TimeoutError as FutureTimeout, CancelledError
from threading import Event, Thread
import re
import subprocess
import time

from chart_live import ChartLiveFlow
from chart_policy import COOP_ROOMS, COOP_ROOM_GROUPS
from costume_unlock import FlowError, normalized
from online_policy import RoomClock, RoomInterrupted, retry_delay, final_song, final_selection
from notifications import dialog_box
from song_catalog import resolve_song
from chart_sync import stage_state


class OnlineLiveFlow(ChartLiveFlow):
    def __init__(self, context, options, output):
        super().__init__(context, options, output)
        # Single-song settings helpers are shared with free live, not tours.
        self.options.mode='free'
        self.report.update(attempts=0, retries=[], state='preparing')
        self.room_clock=None
        self.room_active=False
        self.left_entry=False
        self._last_worker_check=0.
        self.charts={}
        self.current_attempt=None
        self._last_foreground_check=0.
        self.entry_node='OL_CoopEntry' if options.mode=='coop' else 'OL_TeamEntry'
        self.home_node='OL_RoomPage' if options.mode=='coop' else 'OL_TeamHome'
        self.difficulty_y=594 if options.mode=='coop' else 574
        self.coop_song_submitted=False

    def reco(self, name, **override):
        if name=='OL_FinalConfirm' and self.settings.mode=='coop':
            name='OL_CoopFinalConfirm'
        return super().reco(name,**override)

    def state(self, value):
        if self.report['state']!=value:
            self.report['state']=value
            label='协力演出' if self.settings.mode=='coop' else '团队演出'
            log(f'[{label}] {value}')
            if self.current_attempt is not None:
                self.current_attempt.setdefault('states',[]).append(value)

    def snap(self):
        image=super().snap()
        if self.room_active:
            self.guard_room()
        else:
            fatal=self.reco('OL_Fatal')
            if fatal:
                raise FlowError('联网演出无法继续：'+str(fatal.best_result.text))
        return image

    def guard_room(self):
        if time.monotonic()-self._last_foreground_check>1:
            self._last_foreground_check=time.monotonic()
            foreground=self.foreground_package()
            if foreground and foreground!='com.bilibili.star.bili':
                raise RoomInterrupted('游戏已切到后台')
        fatal=self.reco('OL_Fatal')
        if fatal:
            raise FlowError('联网演出无法继续：'+str(fatal.best_result.text))
        lost=self.reco('OL_Disconnected')
        if lost:
            raise RoomInterrupted(str(lost.best_result.text))
        if self.reco('CU_HomeBand') or self.reco('LV_Menu'):
            raise RoomInterrupted('已返回主界面或演出菜单')
        entry=bool(self.reco(self.home_node))
        if entry and self.left_entry:
            raise RoomInterrupted('已返回联网演出入口')
        if not entry:
            self.left_entry=True
        if self.room_clock and self.room_clock.expired(time.monotonic()):
            raise RoomInterrupted('房间3分钟未开演')

    def foreground_package(self):
        info=self.controller.info
        result=subprocess.run([info['adb_path'],'-s',info['adb_serial'],'shell','dumpsys','activity','activities'],
                              capture_output=True,timeout=3,encoding='utf-8',errors='replace',
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        match=re.search(r'(?:topResumedActivity|mResumedActivity).*?\bu\d+\s+([^/\s]+)/',result.stdout)
        return match[1] if result.returncode==0 and match else None

    def resume_game(self):
        # Allow the server to observe loss of focus before resuming the game.
        self.pause(3)
        if not self.controller.post_start_app('com.bilibili.star.bili').wait().succeeded:
            raise FlowError('无法返回游戏')
        self.pause(2)

    def background_room(self):
        self.controller.post_key_down(3).wait()
        self.controller.post_key_up(3).wait()
        self.resume_game()

    def quick_tap(self,x,y):
        self.check_stop()
        if not self.controller.post_click(int(x),int(y)).wait().succeeded:
            raise FlowError('联网页面点击失败')
        self.pause(.15)

    def quick_swipe(self,x,y1,y2):
        self.check_stop()
        if not self.controller.post_swipe(x,y1,x,y2,400).wait().succeeded:
            raise FlowError('协力选曲滑动失败')
        self.pause(.2)

    def dismiss_talk(self):
        for node in ('LV_TalkSkipConfirm','LV_TalkSkip','LV_TalkMenu'):
            if self.reco(node):
                if node=='LV_TalkSkipConfirm':
                    self.tap(770,448)
                else:
                    self.click(node)
                return True
        return False

    def result_modals(self):
        if self.hit_text([450,280,390,120],'没有达到.*基准|活动点数为0'):
            button=self.hit_text([510,480,260,100],'^关闭$')
            if button:
                self.tap_hit(button)
                return True
        return super().result_modals()

    def navigate_menu(self):
        for _ in range(12):
            self.snap()
            if self.dismiss_talk() or self.result_modals():
                continue
            if self.reco(self.home_node):
                self.back()
                continue
            return super().navigate_menu()
        raise FlowError('返回演出菜单未收敛')

    def prepare_settings(self):
        if self.settings.mode!='coop' or not self.settings.coop_song:
            return
        self.state('检查协力选曲')
        self.navigate_menu()
        self.open_page('LV_FreeEntry','LV_SongPage')
        if self.settings.mode=='coop' and self.settings.coop_song:
            # Check that the song is unlocked before entering a timed room.
            # Cooperative live keeps its own selection and filter state.
            requested=resolve_song(self.settings.coop_song)
            self.find_song(requested)
            self.save_frame('coop_song_prepared.png')
        self.navigate_menu()

    def selected_coop_room(self):
        lines=sorted(self.ocr([550,225,200,100]),key=lambda hit:(hit.box[1],hit.box[0]))
        title=normalized(''.join(hit.text for hit in lines))
        matches=[key for key,label in COOP_ROOMS.items() if title==label]
        if len(matches)!=1:
            raise FlowError('无法确认当前选中的协力房间：'+title)
        return matches[0]

    def choose_coop_room(self):
        self.wait('OL_RoomPage')
        group=self.settings.coop_room_group
        label=COOP_ROOM_GROUPS[group]
        button=self.hit_text([955,145,245,55],'^'+label+'$')
        if not button:
            raise FlowError('当前没有所选协力房间类别：'+label)
        self.tap_hit(button)
        self.wait('OL_RoomPage')
        x=983 if group=='normal' else 1105
        if (self.image[160:168,x:x+8].min(2)>235).mean()<.9:
            raise FlowError('未确认协力房间类别：'+label)
        keys=list(COOP_ROOMS)
        for _ in range(len(keys)):
            current=self.selected_coop_room()
            if current==self.settings.coop_room:
                break
            self.tap(975 if keys.index(current)<keys.index(self.settings.coop_room) else 300,330)
            self.wait('OL_RoomPage')
        else:
            raise FlowError('未能选择指定协力房间')
        required=normalized(self.text([550,385,190,70]))
        if required=='无':
            minimum=0
        elif required.isdigit():
            minimum=int(required)
        else:
            raise FlowError('无法确认协力房间所需综合能力：'+required)
        power_text=normalized(self.text([195,145,135,40]))
        if not power_text.isdigit():
            raise FlowError('无法确认当前综合能力：'+power_text)
        power=int(power_text)
        if power<minimum:
            raise FlowError(f'{COOP_ROOMS[current]}需要综合能力{minimum}，当前{power}')
        self.report['coop_room_verified']={'type':current,'group':group,'power':power,'minimum':minimum}
        self.save_frame('coop_room_selected.png')

    def submit_coop_song(self):
        if self.coop_song_submitted:
            return True
        if self.hit_text([865,595,415,115],'NOW LOADING'):
            return False
        # The page title can precede both the list and the loading indicator.
        # This common button must be visible before either selection path runs.
        button=self.hit_text([685,605,200,80],'^不指定歌曲$')
        if not button:
            return False
        self.state('协力选曲')
        self.save_frame('coop_song_page.png')
        if self.settings.coop_song:
            song=resolve_song(self.settings.coop_song)
            # Check the current song before changing the room's saved filters.
            self.find_song(song,max_steps=8,forward_first=True,quick=True)
            if not self.reco('OL_CoopSongPage') or not self.selected_song_matches(song):
                raise FlowError('协力提交前未确认所选歌曲')
            button=self.hit_text([950,605,250,80],'^确定$')
            if not button or not self.pink(self.image[625:638,980:995]):
                raise FlowError('协力选歌确认按钮不可用')
        self.save_frame('coop_song_submission.png')
        self.tap_hit(button)
        self.coop_song_submitted=True
        self.current_attempt['submitted_song']=self.settings.coop_song or None
        return True

    def configure_menu_fire(self):
        self.inherit_menu_fire()
        if not self.refill_fire(self.settings.fire):
            self.report['status']='insufficient_fire'
            return False
        return True

    def join_room(self):
        self.navigate_menu()
        if not self.configure_menu_fire():
            return False
        self.open_page(self.entry_node,self.home_node)
        if self.settings.mode=='coop':
            self.choose_coop_room()
        self.report['attempts']+=1
        self.current_attempt={'attempt':self.report['attempts'],'status':'matching',
                              'songs':[],'fire_before_matching':self.fire_balance()}
        self.report['rounds'].append(self.current_attempt)
        self.state('匹配房间')
        self.room_clock=RoomClock(time.monotonic())
        self.coop_song_submitted=False
        self.tap(1050,648)
        self.room_active=True
        self.left_entry=False
        return True

    def await_final(self):
        unknown_since=None
        song_loading_since=None
        while True:
            self.snap()
            if self.reco('OL_FinalConfirm'):
                if self.settings.mode=='coop' and self.settings.coop_song and not self.coop_song_submitted:
                    raise FlowError('协力选曲已结束，未确认提交指定歌曲')
                self.state('确认最终歌曲和难度')
                return
            if self.settings.mode=='coop' and self.hit_text([450,300,400,100],'以[2-5]人开始演出'):
                button=self.hit_text([650,415,250,90],'^开始$')
                if button:
                    self.tap_hit(button)
                    unknown_since=None
                    continue
            if self.settings.mode=='coop' and self.reco('OL_CoopSongPage'):
                if self.submit_coop_song():
                    song_loading_since=None
                else:
                    now=time.monotonic()
                    if song_loading_since is None:
                        song_loading_since=now
                        self.state('等待协力选曲页面加载')
                        self.save_frame('coop_song_loading.png')
                    if now-song_loading_since>=30:
                        raise FlowError('协力选曲页面30秒未加载出操作按钮')
                unknown_since=None
                self.pause(.25)
                continue
            if self.settings.mode=='coop' and time.monotonic()-self.room_clock.entered>15:
                start=self.hit_text([920,610,275,70],'^立即开始$|^立刻开始$')
                if start and self.pink(self.image[625:638,960:980]):
                    self.tap_hit(start)
                    unknown_since=None
                    continue
            if self.reco('OL_RandomSong'):
                self.state('随机选曲')
                unknown_since=None
            elif self.reco('OL_Matching') or self.reco(self.home_node):
                unknown_since=None
            else:
                unknown_since=unknown_since or time.monotonic()
                if self.settings.mode=='coop':
                    self.save_frame('coop_waiting.png')
                if time.monotonic()-unknown_since>30:
                    raise FlowError('联网房间出现未知页面超过30秒')
            self.pause(.25)

    def read_final_song(self):
        # The header appears before the title finishes its entrance animation.
        deadline=time.monotonic()+5
        previous=None
        error='最终歌曲尚未显示完整'
        while time.monotonic()<deadline:
            self.snap()
            if not self.reco('OL_FinalConfirm'):
                raise FlowError('最终确认页面已离开，停止读取旧页面歌曲')
            title=self.text([110,538,454,41])
            try:
                song=final_song(title)
            except ValueError as exc:
                previous=None
                error=str(exc)
            else:
                if previous==song['id']:
                    return song
                previous=song['id']
            self.pause(.2)
        raise FlowError(error)

    def read_final_selection(self):
        song=self.read_final_song()
        labels=self.ocr([540,550,452,65],'EASY|NORMAL|HARD|EXPERT|SPECIAL')
        centers={hit.text.strip().lower():hit.box[0]+hit.box[2]//2 for hit in labels}
        special='special' in centers
        selection=final_selection(song,self.settings.difficulties[0],special)
        if selection.difficulty not in centers:
            raise FlowError('未找到目标难度按钮：'+selection.difficulty)
        self.quick_tap(centers[selection.difficulty],self.difficulty_y)
        self.snap()
        if not self.reco('OL_FinalConfirm'):
            raise FlowError('未能在倒计时内确认难度')
        x=centers[selection.difficulty]
        area=self.image[self.difficulty_y-20:self.difficulty_y+12,x-15:x+15].astype(float)
        if (area.max(2)-area.min(2)>75).mean()<.25:
            raise FlowError('联网难度未选中：'+selection.difficulty)
        # Cut-in checkbox is separate per mode; disable it before ready submission.
        if self.pink(self.image[637:663,487:513]):
            self.quick_tap(500,650)
            self.snap()
        if self.pink(self.image[637:663,487:513]):
            raise FlowError('联网3D Cut in未关闭')
        self.selection=selection
        return selection

    def prepare_final_chart(self,selection):
        """Fetch only the confirmed chart without blocking room/stop monitoring."""
        self.state('读取最终歌曲谱面（缺失时下载）')
        deadline=time.monotonic()+12
        cancelled=Event()
        future=Future()
        def check_download():
            if cancelled.is_set():
                raise CancelledError('谱面准备已取消')
            if time.monotonic()>=deadline:
                raise TimeoutError('最终歌曲谱面准备超过12秒')
        def download():
            try:
                future.set_result(self.store.get(selection,check_download,timeout=3))
            except Exception as exc:
                future.set_exception(exc)
        # A stalled network read must never hold the game UI or room recovery.
        # The worker has no controller access and checks cancellation before saving.
        worker=Thread(target=download,name='final-song-chart',daemon=True)
        try:
            started=False
            while True:
                self.check_stop()
                if time.monotonic()>=deadline:
                    raise FlowError('最终歌曲谱面准备超过12秒，退出房间')
                self.snap()
                if not self.reco('OL_FinalConfirm'):
                    raise FlowError('谱面准备期间最终确认页面已离开，停止开演')
                if not started:
                    worker.start()
                    started=True
                # Recheck the page even when a cache hit completes immediately.
                elif future.done():
                    try:
                        _,metadata=future.result()
                    except Exception as exc:
                        raise FlowError(f'最终歌曲谱面准备失败：{exc}') from exc
                    self.charts[(selection.song_id,selection.difficulty)]=metadata
                    self.report['cached_charts']=len(self.charts)
                    return metadata
                try:
                    future.result(timeout=.2)
                except FutureTimeout:
                    pass
                except Exception:
                    pass  # Report errors after the next room/stop check.
        finally:
            cancelled.set()

    def verify_chart_start(self,index,selection,amount):
        for attempt in range(2):
            # Every reread must still belong to the same room/song/difficulty.
            self.verify_final_selection(selection)
            try:
                before,after=self.fire_preview()
                if self.settings.mode=='coop':
                    # Coop replaces the top currency bar with player cards.
                    # Verify its only counter twice on the same final page.
                    self.pause(.15)
                    self.snap()
                    if not self.reco('OL_FinalConfirm') or self.fire_preview()!=(before,after):
                        raise FlowError('协力火数预览读数不稳定或已离开准备页')
                    balance=before
                else:
                    balance=self.fire_balance()
                if before-after!=amount or before<amount or before!=balance:
                    raise FlowError(f'联网开演火数预览不符：预览 {before} → {after}，'
                                    f'顶部 {balance}，要求消耗 {amount}')
                return before
            except FlowError:
                if attempt==1:
                    raise
                self.pause(.25)

    def verify_final_selection(self,selection):
        self.snap()
        if not self.reco('OL_FinalConfirm'):
            raise FlowError('联网确认页面已离开，不能确认开演配置')
        if self.read_final_song()['id']!=selection.song_id:
            raise FlowError('联网最终歌曲发生变化')
        labels=self.ocr([540,550,452,65],'EASY|NORMAL|HARD|EXPERT|SPECIAL')
        target=next((h for h in labels if h.text.strip().lower()==selection.difficulty),None)
        if target is None:
            raise FlowError('开演前联网难度按钮消失')
        x=target.box[0]+target.box[2]//2
        area=self.image[self.difficulty_y-20:self.difficulty_y+12,x-15:x+15].astype(float)
        if (area.max(2)-area.min(2)>75).mean()<.25:
            raise FlowError('开演前联网难度发生变化')

    def submit_online_ready(self,selection):
        self.snap()
        if not self.reco('OL_FinalConfirm'):
            raise FlowError('准备提交前联网确认页面已离开')
        ready=self.hit_text([1010,580,225,105],'^准备完毕[！!]?$')
        if not ready:
            raise FlowError('未找到联网准备完毕按钮')
        self.quick_tap(ready.box[0]+ready.box[2]//2,ready.box[1]+ready.box[3]//2)
        self.state('等待联网开演')

    def monitor_online_worker(self,destination):
        if (destination/'stage_started').exists():
            self.room_clock.started=True
            self.state('谱面代打中')
        if time.monotonic()-self._last_worker_check>=.5:
            self._last_worker_check=time.monotonic()
            self.snap()

    def handle_online_playback_error(self,playback):
        if playback.get('interrupted'):
            raise RoomInterrupted(playback.get('observer_error','联网演出中断'))
        self.snap()  # A confirmed room exit is retryable; sync/input errors are not.

    def count_result(self,row):
        if not row.get('counted'):
            row['counted']=True
            self.report['completed_rounds']+=1

    def settle_online(self,row):
        self.state('结算')
        deadline=time.monotonic()+180
        confirmed=bool(row.get('counted'))
        overlay_since=None
        while time.monotonic()<deadline:
            self.snap()
            if confirmed and self.reco('OL_Disconnected'):
                self.recover_room()
                return
            if self.result_modals() or self.dismiss_talk():
                continue
            # A modal can leave the background Next button OCR-visible.
            # Dismiss it before interpreting score/experience controls below.
            modal_handled=False
            for node,point in [('LV_RankUp',(640,526)),('LV_RewardModal',(640,602)),
                               ('LV_RankReward',(640,602))]:
                if self.reco(node):
                    self.tap(*point)
                    modal_handled=True
                    break
            if modal_handled:
                continue
            if dialog_box(self.image) is not None:
                overlay_since=overlay_since or time.monotonic()
                if time.monotonic()-overlay_since>3:
                    self.require_clear_notification_overlay()
                self.pause(.2)
                continue
            overlay_since=None
            if self.hit_text([680,275,220,220],'GREAT|GOOD|BAD|MISS'):
                if not confirmed:
                    self.save_frame(f'judgment_attempt{self.report["attempts"]}.png')
                    row['judgment_text']=self.text([680,270,490,220])
                    row['status']='result_confirmed'
                    confirmed=True
                    self.count_result(row)
                    self.room_active=False
            group_result=self.settings.mode=='coop' and self.reco('OL_CoopGroupResult')
            if group_result:
                self.save_frame(f'group_result_attempt{self.report["attempts"]}.png')
            # We are already settling a finished playback and have excluded
            # overlays. Advance the footer without enumerating result titles.
            button=self.hit_text([940,600,290,105],'^下一步$|^确定$|^确认$|^关闭$')
            if button:
                self.tap_hit(button)
                if confirmed:
                    self.room_active=False
                continue
            if confirmed and (self.reco(self.home_node) or self.reco('LV_Menu') or self.reco('CU_HomeBand')):
                return
            self.pause(.4)
        raise FlowError('联网演出结算超时，保留现场')

    def recover_room(self):
        self.room_active=False
        self.selection=None
        self.room_clock=None
        self.state('退出并重新匹配')
        foreground=self.foreground_package()
        backgrounded=False
        back_attempted=False
        pause_attempted=False
        if foreground and foreground!='com.bilibili.star.bili':
            self.resume_game()
            backgrounded=True
        # Only navigate from recognized pages/dialogs; never send stale ready taps.
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            self.snap()
            # Modal text takes precedence over a still-visible background header.
            if self.reco('OL_LeaveConfirm'):
                button=self.hit_text([640,410,275,170],'^确定$|^确认$|^退出$|^解散$|^返回主页$|^中断$')
                if button:
                    self.tap_hit(button)
                    continue
            if self.reco('OL_Disconnected'):
                interrupt=self.hit_text([380,400,260,100],'^中断$')
                if interrupt:
                    self.tap_hit(interrupt)
                    continue
                button=self.hit_text([350,390,580,230],'^确定$|^确认$|^关闭$|^OK$|^返回标题$')
                if button:
                    self.tap_hit(button)
                    continue
            if self.hit_text([210,210,850,85],'^暂停$'):
                interrupt=self.hit_text([250,400,700,110],'^中断$')
                if interrupt:
                    self.tap_hit(interrupt)
                    continue
            cancel=self.hit_text([490,470,300,110],'^取消$')
            if cancel:
                self.tap_hit(cancel)
                continue
            if self.reco('CU_HomeBand') or self.reco('LV_Menu'):
                return
            if self.reco(self.home_node):
                self.back()
                continue
            if not pause_attempted and self.image is not None and stage_state(self.image) is not None:
                # Playback has already failed and its worker is stopped. Open
                # the game's pause/interrupt flow instead of looking for a back
                # button on the still-running stage.
                self.tap(1240,50)
                pause_attempted=True
                continue
            waiting=any(self.reco(node) for node in
                        ('OL_Matching','OL_FinalConfirm','OL_Loading','OL_WaitingMembers'))
            if waiting and back_attempted and not backgrounded:
                self.background_room()
                backgrounded=True
                continue
            if self.reco('CU_Back'):
                self.back()
                back_attempted=True
                continue
            if not backgrounded and waiting:
                self.background_room()
                backgrounded=True
                continue
            self.pause(.5)
        raise FlowError('无法确认退出房间的安全入口，保留现场')

    def run(self):
        self.report['cached_charts']=0
        self.prepare_settings()
        failures=0
        while self.settings.max_rounds is None or self.report['completed_rounds']<self.settings.max_rounds:
            if not self.join_room():
                self.home()
                return
            try:
                self.await_final()
                selection=self.read_final_selection()
                song={**asdict(selection),'requested_difficulty':self.settings.difficulties[0],
                      'fire':self.settings.fire}
                self.current_attempt['songs'].append(song)
                self.save_frame(f'final_attempt{self.report["attempts"]}.png')
                started=time.monotonic()
                metadata=self.prepare_final_chart(selection)
                song['chart']={**metadata,'prepare_seconds':time.monotonic()-started}
                self.play_chart(1,selection,metadata,self.settings.fire,song)
                self.settle_online(song)
                self.current_attempt['status']='finished'
                self.count_result(song)
                log(f'[联网演出] 结果已确认，累计完成 {self.report["completed_rounds"]} 轮')
                failures=0
                self.room_active=False
                self.home()
                self.current_attempt=None
            except RoomInterrupted as exc:
                self.room_active=False
                self.current_attempt.update(status='interrupted',reason=str(exc),
                    room_elapsed_seconds=time.monotonic()-self.room_clock.entered if self.room_clock else None)
                self.save_frame(f'interrupted_attempt{self.report["attempts"]}.png')
                failures+=1
                delay=retry_delay(failures)
                self.report['retries'].append({'attempt':self.report['attempts'],'reason':str(exc),'delay':delay})
                log(f'[联网演出] 房间中断：{exc}；第 {failures} 次重试，退出后等待 {delay} 秒', level='warn')
                self.recover_room()
                self.pause(delay)
            except (FlowError,ValueError) as exc:
                self.current_attempt.update(status='failed',reason=str(exc))
                # A failed final confirmation must not leave a timed room to auto-start.
                self.save_frame(f'failed_attempt{self.report["attempts"]}.png')
                if self.room_active and not self.ctx.tasker.stopping and not self.reco('OL_Fatal'):
                    try:
                        self.recover_room()
                    except Exception as recovery_error:
                        self.current_attempt['recovery_error']=str(recovery_error)
                raise
        self.report['status']='max_rounds_reached'
        self.state('完成')
