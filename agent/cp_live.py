"""Event challenge lives: a separate CP budget, never LIVE BOOST or refills."""
from dataclasses import asdict
import re
import time

from chart_live import ChartLiveFlow
from chart_policy import ChartSelection
from costume_unlock import FlowError, normalized
from online_policy import final_song
from notifications import dialog_box
from song_catalog import RECOGNITION_BY_ID


class CPLiveFlow(ChartLiveFlow):
    def __init__(self, context, options, output):
        super().__init__(context, options, output)
        self.options.mode='free'

    def ready(self, index=1):
        self.snap()
        if self.dismiss_daily_reward():
            return False
        return bool(self.reco('CP_Header') and self.reco('CP_Ready') and not self.reco('CP_Dialog'))

    def navigate_menu(self):
        for _ in range(5):
            self.snap()
            if self.reco('CP_Dialog'):
                self.tap(510,620)
            elif self.reco('CP_Header'):
                self.back()
            else:
                return super().navigate_menu()
        raise FlowError('无法从挑战演出返回菜单')

    def require_song_page(self):
        self.wait('CP_Select')
        if not self.reco('CP_Header') or self.reco('CP_Dialog'):
            raise FlowError('未确认挑战演出选曲页面')

    def selected_song(self):
        self.require_song_page()
        title=self.text([195,329,380,34])
        band=self.text([195,360,380,26])
        return final_song(title,band)

    def select_event_song(self):
        if not self.settings.cp_song:
            return self.selected_song()
        requested=RECOGNITION_BY_ID.get(self.settings.cp_song)
        if requested is None:
            requested=final_song(self.settings.cp_song)
        for attempt in range(8):
            self.require_song_page()
            if self.selected_song()['id']==requested['id']:
                return requested
            hits=[hit for hit in self.ocr([195,95,380,550]) if self.title_matches(hit.text,requested)]
            if len(hits)>1:
                raise FlowError('活动歌单出现同名歧义，停止选曲')
            if hits:
                self.tap_hit(hits[0])
                if self.selected_song()['id']==requested['id']:
                    return requested
                raise FlowError('挑战选曲后的歌曲或乐队不符')
            self.swipe(420,570 if attempt<4 else 220,220 if attempt<4 else 570)
        raise FlowError('指定歌曲不在当前活动歌单中：'+requested['title'])

    def configure_cp(self, from_ready=False):
        if from_ready:
            self.wait_ready()
            self.tap(857,650)
        self.wait('CP_Dialog')
        before=self.stable_integer([772,125,67,34])
        if before<self.settings.cp:
            self.tap(510,620)
            return None
        row=(200,400,800,1600).index(self.settings.cp)
        y=(212,285,358,431)[row]
        shown=self.read_integer([489,y-19,67,39])
        if shown!=self.settings.cp:
            raise FlowError('CP档位数值与设置不符')
        self.tap(877,y)
        self.wait('CP_Dialog')
        if not self.pink(self.image[y-10:y+11,867:888]):
            raise FlowError('未确认所选CP档位')
        if self.read_integer([772,125,67,34])!=before:
            raise FlowError('选择CP期间余额发生变化')
        self.tap(770,620)
        self.wait_ready()
        self.verify_cp_cost()
        return before

    def verify_cp_cost(self):
        text=normalized(self.text([1020,541,148,38]))
        match=re.fullmatch(r'(?:CP)?(\d+)消[费耗]',text,re.I)
        if not match or int(match[1])!=self.settings.cp:
            raise FlowError('开演前CP消耗未确认：'+text)

    def prepare_round(self):
        self.navigate_menu()
        self.open_page('CP_Entry','CP_Select')
        balance=self.stable_integer([1200,133,67,34])
        if balance<self.settings.cp:
            self.report.update(status='insufficient_cp',cp_balance=balance)
            return (),[]
        song=self.select_event_song()
        selection=ChartSelection.from_recognized(song,self.settings.difficulties[0])
        self.choose_difficulty_exact(selection.difficulty)
        # Fetch and validate before touching the CP dialog or start button.
        charts=[self.store.get(selection,self.check_stop)]
        self.tap(1070,648)
        balance=self.configure_cp()
        if balance is None:
            self.report['status']='insufficient_cp'
            return (),[]
        self.selection=selection
        self.report['cp_balance']=balance
        return (selection,),charts

    def disable_mv(self):
        self.wait_ready()
        # This event's confirmation page has only the 3D Cut in checkbox.
        if self.pink(self.image[637:663,487:513]):
            self.tap(500,650)
            self.wait_ready()
        if self.pink(self.image[637:663,487:513]):
            raise FlowError('挑战演出3D Cut in未关闭')

    def verify_chart_start(self,index,selection,amount):
        if amount!=self.settings.cp:
            raise FlowError('CP消耗参数不一致')
        before=self.configure_cp(from_ready=True)
        if before is None:
            raise FlowError('开演前CP不足，不提交')
        title=self.text([220,541,570,38])
        difficulty=normalized(self.text([113,561,104,33])).lower()
        if not self.title_matches(title,selection.song) or difficulty!=selection.difficulty:
            raise FlowError('挑战开演前歌曲或难度与谱面不符')
        self.verify_cp_cost()
        return before

    def settlement_destination(self):
        return bool(self.reco('CP_Header') and self.reco('CP_Select')) or super().settlement_destination()

    def result_modals(self):
        # Challenge rewards stack a receipt over the achievement summary. During
        # its scale-in animation the dimmed summary is visible but unreadable.
        # Wait for a readable notification; never click through that overlay.
        deadline=time.monotonic()+3
        while True:
            if super().result_modals():
                return True
            # These dialogs belong to the existing settlement state machine,
            # including a two-button story-skip confirmation after rewards.
            if any(self.reco(node) for node in ('LV_TalkSkipConfirm','LV_RankUp','LV_RewardModal','LV_RankReward')):
                return False
            if dialog_box(self.image) is None:
                return False
            if time.monotonic()>=deadline:
                self.require_clear_notification_overlay()
            self.pause(.2)
            self.snap()

    def run(self):
        configured=False
        while self.settings.max_rounds is None or self.report['completed_rounds']<self.settings.max_rounds:
            selections,charts=self.prepare_round()
            if not selections:
                self.navigate_menu()
                self.home()
                self.report['returned_home']=True
                return
            if not configured:
                self.configure_stage()
                configured=True
            selection=selections[0]
            song={**asdict(selection),'cp':self.settings.cp,'status':'preparing'}
            row={'songs':[song],'status':'running'}
            self.report['rounds'].append(row)
            self.play_chart(1,selection,charts[0][1],self.settings.cp,song)
            self.await_chart_result(1,song)
            self.settle_results()
            self.navigate_menu()
            self.open_page('CP_Entry','CP_Select')
            after=self.stable_integer([1200,133,67,34])
            song['cp_after']=after
            if song['cp_before']-after!=self.settings.cp:
                raise FlowError('结算后CP扣除数与设置不符，不重新开演')
            row['status']='finished'
            self.report['completed_rounds']+=1
        self.report['status']='max_rounds_reached'
        self.navigate_menu()
        self.home()
