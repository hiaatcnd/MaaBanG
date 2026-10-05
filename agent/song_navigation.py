"""Shared song selection for built-in auto live and chart-driven play."""
import numpy as np
from costume_unlock import FlowError, normalized
from song_catalog import needs_band_check, recognition_titles
from live_policy import DIFFICULTIES

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
    def reset_inherited_song_filters(self):
        """Clear persistent filters once before a task's ordinary song selection."""
        if getattr(self,'_inherited_filters_cleared',False):
            return
        self.wait('LV_SongPage')
        self.tap(1116,55)
        self.snap()
        if not self.hit_text([690,20,350,50],'乐曲筛选'):
            raise FlowError('未打开歌曲筛选，不能清理上次任务的筛选条件')
        self.tap(1165,44)
        self.tap(963,652)
        self.wait('LV_SongPage')
        self._inherited_filters_cleared=True

    def filter_expert_level(self,level):
        self.set_song_level_range(level,level,tolerance=1)

    def set_song_level_range(self,minimum,maximum,tolerance=0):
        reset_scroll = False
        for _ in range(6):
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
            if not hit and not reset_scroll:
                # The panel remembers its scroll position. If the level row is
                # above the viewport, scrolling farther down never finds it.
                self.swipe(1200,200,550)
                self.swipe(1200,200,550)
                reset_scroll = True
                continue
            self.swipe(1200,550,290)
        else:
            raise FlowError('未定位乐曲等级筛选滑块')
        corrections = [0, 0]
        last_values = last_index = None
        for _ in range(20):
            if _:
                self.snap()
            values=[]
            for x,width in ((695,52),(1183,65)):
                text=normalized(self.text([x,y-24,width,48]))
                if not text.isdigit():
                    raise FlowError(f'无法读取乐曲等级范围：{text}')
                values.append(int(text))
            self.report.setdefault('level_filter_steps',[]).append({'range':values,'target':[minimum,maximum],'y':y})
            if not 5<=values[0]<=values[1]<=30 or not 5<=minimum<=maximum<=30:
                raise FlowError('乐曲等级范围超出当前游戏筛选范围')
            # Level filtering only narrows the search. Accept a small outward
            # margin, but never hide the target level or accept a broad range.
            lower_ok=max(5,minimum-tolerance)<=values[0]<=minimum
            upper_ok=maximum<=values[1]<=min(30,maximum+tolerance)
            if lower_ok and upper_ok:
                row={'verified_range':values}
                if values!=[minimum,maximum]:
                    row.update(target_range=[minimum,maximum],tolerance=tolerance)
                self.report.setdefault('song_filters',[]).append(row)
                return
            handles=level_slider_handles(self.image,y)
            # Narrow the upper bound first when possible. The lower handle
            # then clamps to it instead of overshooting a single-level range.
            index=1 if not upper_ok and maximum>=values[0] else 0
            delta=(minimum,maximum)[index]-values[index]
            # Short drags can be swallowed by the game's touch slop. Increase
            # a stalled correction instead of repeating an ineffective gesture.
            if index==last_index and values==last_values:
                corrections[index] += 8 if delta>0 else -8
            else:
                corrections[index] = 0
            # Corrections near 6/29 also need room past the nominal rail ends;
            # clamping to 772/1152 would make every stalled retry identical.
            target=int(round(np.clip(handles[index]+delta*15.2+(5 if delta>0 else -5)+corrections[index],735,1185)))
            if (minimum,maximum)[index] in (5,30):
                # Drag beyond the rail; stopping at its nominal end can leave
                # the selected range at 6 or 29. OCR still verifies the result.
                target=735 if (minimum,maximum)[index]==5 else 1185
            last_values,last_index=values,index
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
        if title_key(text) in {title_key(v) for v in recognition_titles(song)}:
            return True
        # Resolve against the whole recognition catalog, not just the requested
        # song, so a near tie or a better match cannot select the wrong chart.
        # Import at call time: online_policy also uses this module's title_key.
        from online_policy import final_song
        try:
            return final_song(text)['id'] == song['id']
        except ValueError:
            return False

    def selected_song_matches(self,song):
        if not self.title_matches(self.text([210,332,356,32]),song):
            return False
        return (not needs_band_check(song['id']) or
                normalized(self.text([201,365,374,35])) in
                {normalized(v) for v in song['band_aliases']})

    def all_songs_category(self,quick=False):
        """Leave the separate Favorites list; its 'All' only means all favorites."""
        for _ in range(8):
            hit=self.hit_text([0,106,180,75],'^所有$')
            if hit:
                if quick:
                    x,y,w,h=hit.box
                    self.quick_tap(x+w//2,y+h//2)
                    self.pause(.25)
                else:
                    self.tap_hit(hit)
                self.wait('LV_SongPage')
                return
            # Scroll the category sidebar itself. Favorites has another All
            # entry below its heading; never treat it as the main All category.
            if self.hit_text([0,630,115,75],'^范围$'):
                self.tap(590,55)
            else:
                # Gaps between category rows do not accept a drag. Start on a
                # visible label so a partially scrolled sidebar still moves.
                anchor=self.hit_text([0,180,180,300],'.+')
                y=anchor.box[1]+anchor.box[3]//2 if anchor else 300
                (self.quick_swipe if quick else self.swipe)(90,y,650)
            self.wait('LV_SongPage')
        raise FlowError('无法找到全部歌曲分类')

    def all_songs(self,song,quick=False,from_favorites=False):
        def tap(x,y):
            if quick:
                self.quick_tap(x,y)
                self.pause(.25)
            else:
                self.tap(x,y)
        if from_favorites:
            self.favorites_category(quick=quick)
        else:
            self.all_songs_category(quick=quick)
        tap(1116,55)
        self.snap()
        if not self.hit_text([690,20,350,50],'乐曲筛选'):
            raise FlowError('未打开歌曲筛选')
        for _ in range(4):
            header=self.hit_text([690,90,170,85],'^乐队$')
            if header and 130<=header.box[1]<=155:
                break
            # A partially scrolled panel can already show the band heading,
            # while all button coordinates are still shifted upward.
            self.swipe(1200,200,550)
            self.snap()
        else:
            raise FlowError('未定位乐队筛选区域')
        tap(1165,44)
        # The current CN filter puts collaborations and bands without a dedicated
        # button (including Ave Mujica) under Other, rather than All.
        x,y=BAND_BUTTONS.get(song['band_id'],OTHER_BAND_BUTTON)
        tap(x,y)
        # All selectable catalog songs have EXPERT. Do not retain a SPECIAL
        # filter that would hide an otherwise selectable target song.
        tap(711,540)
        self.snap()
        if not self.pink(self.image[y-25:y-17,x-42:x+42]):
            raise FlowError(f"未确认乐队筛选：{song['band']}")
        if not self.pink(self.image[532:549,703:720]):
            raise FlowError('未确认选歌搜索难度为 EXPERT')
        if quick:
            self.quick_swipe(1200,550,290)
        self.filter_expert_level(int(song['difficulties']['expert']['level']))
        tap(963,652)
        self.wait('LV_SongPage')

    def favorites_category(self,quick=False):
        """Choose All under the verified Favorites heading, across all folders."""
        def tap_hit(hit):
            if quick:
                x,y,w,h=hit.box
                self.quick_tap(x+w//2,y+h//2)
                self.pause(.25)
            else:
                self.tap_hit(hit)
        for _ in range(8):
            self.wait('LV_SongPage')
            header=self.hit_text([0,103,187,605],'^收藏$')
            if header:
                y,h=header.box[1],header.box[3]
                if not self.pink(self.image[y:y+h,150:183]):
                    tap_hit(header)
                self.wait('LV_SongPage')
                header=self.hit_text([0,103,187,605],'^收藏$')
                if not header or not self.pink(self.image[header.box[1]:header.box[1]+header.box[3],150:183]):
                    raise FlowError('未确认收藏分类已选中')
                y=header.box[1]+header.box[3]
                # The selected entry displays a star before its label.
                all_hit=self.hit_text([0,y,187,min(100,720-y)],'^[★☆⭐*]?\\s*所有$')
                if not all_hit:
                    raise FlowError('收藏分类中未找到所有收藏')
                tap_hit(all_hit)
                self.wait('LV_SongPage')
                return
            if self.hit_text([0,630,115,75],'^范围$'):
                self.tap(590,55)
            else:
                (self.quick_swipe if quick else self.swipe)(90,620,245)
        raise FlowError('未找到收藏分类')

    def find_song(self,song,quick=False,max_steps=1200,forward_first=False,difficulty=None):
        """Return whether this search applied an EXPERT level filter."""
        self.wait('LV_SongPage')
        if difficulty is not None:
            # Check the current song only after requesting the intended difficulty:
            # the game's persistent filters may change which song is visible.
            centers=(714,826,939,1051,1185)
            if self.selected_difficulty(centers,540)!=difficulty:
                self.tap(centers[DIFFICULTIES.index(difficulty)],540)
                self.wait('LV_SongPage')
        if self.selected_song_matches(song):
            return False
        settings=getattr(self,'settings',getattr(self,'options',None))
        favorites=getattr(settings,'from_favorites',False)
        self.all_songs(song,quick=quick,from_favorites=favorites)
        self.find_filtered_song(song,max_steps=max_steps,forward_first=forward_first,
                                quick=quick,from_favorites=favorites)
        return True

    def step_song_list(self,forward,quick=False):
        """Select an adjacent card and let the carousel center it itself."""
        # The selected card occupies y=300..394; adjacent cards stay outside it.
        x,y=380,428 if forward else 270
        if quick:
            self.quick_tap(x,y)
            self.pause(.35)
        else:
            self.tap(x,y)
        self.wait('LV_SongPage')

    def find_filtered_song(self,song,max_steps=1200,forward_first=False,quick=False,from_favorites=False):
        """Search an already configured list without rebuilding its filters."""
        if self.selected_song_matches(song):
            return
        directions=(True,False) if forward_first else (False,True)
        for forward in directions:
            previous=None
            for _ in range(max_steps):
                hits=self.ocr([201,105,365,590])
                self.report.setdefault('song_search',[]).append(
                    {'target':song['id'],'direction':'next' if forward else 'previous',
                     'visible':[hit.text for hit in hits]})
                for hit in hits:
                    if not self.title_matches(hit.text,song):
                        continue
                    self.tap_hit(hit)
                    self.wait('LV_SongPage')
                    if self.selected_song_matches(song):
                        return
                area=self.image[300:398,200:575].astype(float)
                if previous is not None and np.mean(np.abs(area-previous))<1:
                    break
                previous=area
                self.step_song_list(forward,quick=quick)
        scope='请确认目标歌曲已收藏并解锁' if from_favorites else '已按乐队筛选全部歌曲'
        raise FlowError(f"未找到或未解锁歌曲：{song['title']}；{scope}")
