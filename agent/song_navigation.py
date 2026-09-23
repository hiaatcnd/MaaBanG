"""Shared song selection for built-in auto live and chart-driven play."""
import numpy as np
from costume_unlock import FlowError, normalized
from song_catalog import needs_band_check, recognition_titles

BAND_BUTTONS={1:(877,208),2:(1000,208),4:(1120,208),5:(754,284),3:(877,284),
              21:(1000,284),18:(1120,284),45:(754,360)}
OTHER_BAND_BUTTON=(877,360)


def level_slider_handles(image,y):
    strip=image[y-20:y+21,740:1180].astype(int)
    gray=(strip.min(2)>215)&(strip.max(2)<248)&(np.ptp(strip,axis=2)<8)
    ids=np.flatnonzero(gray.mean(0)>.8)
    groups=np.split(ids,np.where(np.diff(ids)>1)[0]+1)
    centers=[int(round((g[0]+g[-1])/2))+740 for g in groups if 6<=len(g)<=30]
    if len(centers)!=2:
        raise FlowError('无法确认乐曲等级的两个滑块')
    return centers


def title_key(text):
    key = normalized(text).casefold().translate(str.maketrans(
        'ぁぃぅぇぉゃゅょっァィゥェォャュョッ〜へべぺ',
        'あいうえおやゆよつアイウエオヤユヨツ~ヘベペ'))
    # OCR varies between straight/curly quotes and may repeat boundary quotes.
    # Keep the title body and punctuation exact; never accept a fuzzy prefix.
    return key.translate(str.maketrans({'‘':"'", '’':"'", '“':'"', '”':'"'})).strip("\"'")


class SongNavigationMixin:
    def filter_expert_level(self,level):
        self.set_song_level_range(level,level)

    def set_song_level_range(self,minimum,maximum):
        for _ in range(5):
            self.snap()
            hit=self.hit_text([690,90,400,475],'^乐曲等级$')
            if hit and hit.box[1]+hit.box[3]<450:
                top=hit.box[1]+hit.box[3]
                strip=self.image[top:top+120,740:1180].astype(int)
                gray=(strip.min(2)>215)&(strip.max(2)<248)&(np.ptp(strip,axis=2)<8)
                rows=np.flatnonzero(gray.sum(1)>30)
                if len(rows):
                    y=top+int(np.median(rows))
                    break
            self.swipe(1200,550,290)
        else:
            raise FlowError('未定位乐曲等级筛选滑块')
        for _ in range(10):
            self.snap()
            values=[]
            for x,width in ((695,52),(1183,65)):
                text=normalized(self.text([x,y-24,width,48]))
                if not text.isdigit():
                    raise FlowError(f'无法读取乐曲等级范围：{text}')
                values.append(int(text))
            self.report.setdefault('level_filter_steps',[]).append({'range':values,'target':[minimum,maximum],'y':y})
            if values==[minimum,maximum]:
                self.report.setdefault('song_filters',[]).append({'verified_range':values})
                return
            if not 5<=values[0]<=values[1]<=30 or not 5<=minimum<=maximum<=30:
                raise FlowError('乐曲等级范围超出当前游戏筛选范围')
            handles=level_slider_handles(self.image,y)
            index=0 if values[0]!=minimum else 1
            delta=(minimum,maximum)[index]-values[index]
            target=int(round(np.clip(handles[index]+delta*15.2+(5 if delta>0 else -5),772,1152)))
            self.report['level_filter_steps'][-1].update(handles=handles,index=index,x=target)
            self.check_stop()
            try:
                if not self.controller.post_touch_down(handles[index],y).wait().succeeded:
                    raise FlowError('乐曲等级滑块按下失败')
                self.pause(.08)
                # The game ignores short drags until touch slop is exceeded.
                # Activate the drag first, then settle back at the exact target.
                direction=1 if target>handles[index] else -1
                waypoint=int(np.clip(handles[index]+direction*max(40,abs(target-handles[index])),735,1185))
                for x in [*np.linspace(handles[index],waypoint,9)[1:],target]:
                    if not self.controller.post_touch_move(int(round(x)),y).wait().succeeded:
                        raise FlowError('乐曲等级筛选拖动失败')
                    self.pause(.025)
                self.pause(.12)
            finally:
                self.controller.post_touch_up().wait()
            self.pause(.35)
        raise FlowError('未能将乐曲等级范围设为目标 EXPERT 等级')

    def clear_song_level_filter(self,song):
        self.tap(1116,55)
        self.snap()
        if not self.hit_text([690,20,350,50],'乐曲筛选'):
            raise FlowError('未打开歌曲筛选')
        self.set_song_level_range(5,30)
        self.tap(963,652)
        self.wait('LV_SongPage')
        if not self.selected_song_matches(song):
            raise FlowError('解除等级筛选后选中歌曲发生变化')

    def title_matches(self,text,song):
        return title_key(text) in {title_key(v) for v in recognition_titles(song)}

    def selected_song_matches(self,song):
        if not self.title_matches(self.text([210,332,356,32]),song):
            return False
        return (not needs_band_check(song['id']) or
                normalized(self.text([201,365,374,35])) in
                {normalized(v) for v in song['band_aliases']})

    def all_songs(self,song):
        self.wait('LV_SongPage')
        # Leave favorites/genre before applying the narrow band filter.
        for _ in range(12):
            self.snap()
            hit=self.hit_text([0,106,180,95],'^所有$')
            if hit:
                self.tap_hit(hit)
                break
            self.swipe(85,210,650)
        else:
            raise FlowError('无法找到全部歌曲分类')
        self.tap(1116,55)
        self.snap()
        if not self.hit_text([690,20,350,50],'乐曲筛选'):
            raise FlowError('未打开歌曲筛选')
        for _ in range(4):
            if self.hit_text([690,110,170,65],'^乐队$'):
                break
            self.swipe(1200,200,550)
            self.snap()
        else:
            raise FlowError('未定位乐队筛选区域')
        self.tap(1165,44)
        # The current CN filter puts collaborations and bands without a dedicated
        # button (including Ave Mujica) under Other, rather than All.
        x,y=BAND_BUTTONS.get(song['band_id'],OTHER_BAND_BUTTON)
        self.tap(x,y)
        # All selectable catalog songs have EXPERT. Do not retain a SPECIAL
        # filter that would hide an otherwise selectable target song.
        self.tap(711,540)
        self.snap()
        if not self.pink(self.image[y-25:y-17,x-42:x+42]):
            raise FlowError(f"未确认乐队筛选：{song['band']}")
        if not self.pink(self.image[532:549,703:720]):
            raise FlowError('未确认选歌搜索难度为 EXPERT')
        self.filter_expert_level(int(song['difficulties']['expert']['level']))
        self.tap(963,652)
        self.wait('LV_SongPage')

    def find_song(self,song):
        self.wait('LV_SongPage')
        if self.selected_song_matches(song):
            return
        self.all_songs(song)
        for start,end in ((230,650),(650,200)):
            previous=None
            for _ in range(250):
                self.wait('LV_SongPage')
                for hit in self.ocr([201,105,365,590]):
                    if not self.title_matches(hit.text,song):
                        continue
                    self.tap_hit(hit)
                    self.wait('LV_SongPage')
                    if self.selected_song_matches(song):
                        return
                area=self.image[105:703,201:565].astype(float)
                if previous is not None and np.mean(np.abs(area-previous))<1:
                    break
                previous=area
                self.swipe(385,start,end)
        raise FlowError(f"未找到或未解锁歌曲：{song['title']}；已按乐队筛选全部歌曲")
