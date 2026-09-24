"""Download and validate official Bestdori charts before any live is started."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
from threading import Event
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, CancelledError
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from chart_timing import compile_chart, first_anchor
from live_policy import DIFFICULTIES
from user_data import data_root


def note_count(chart):
    return sum(sum(not p.get('hidden', False) for p in n['connections'])
               if n['type'] in ('Long', 'Slide') else 1
               for n in chart if n['type'] not in ('BPM', 'System'))


class ChartStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else data_root() / 'cache/charts'

    def get(self, selection, check_stop=lambda: None, timeout=30):
        check_stop()
        key = f'{selection.song_id}_{selection.difficulty}'
        path = self.directory / (key+'.json')
        cache_hit = path.exists()
        if cache_hit:
            raw = path.read_bytes()
        else:
            url = f'https://bestdori.com/api/charts/{selection.song_id}/{selection.difficulty}.json'
            request = Request(url, headers={'User-Agent':'Mozilla/5.0 MaaBanG',
                                           'Referer':'https://bestdori.com/tool/chartsimulator'})
            for attempt in range(3):
                check_stop()
                try:
                    with urlopen(request, timeout=timeout) as response:
                        raw = response.read(8*1024*1024+1)
                    break
                except (URLError, TimeoutError, ConnectionError) as exc:
                    if attempt==2 or isinstance(exc,HTTPError) and exc.code not in (429,500,502,503,504):
                        raise
                    for _ in range((attempt+1)*10):
                        check_stop()
                        time.sleep(.1)
            if len(raw)>8*1024*1024:
                raise ValueError('谱面文件过大')
        chart = json.loads(raw)
        check_stop()
        if not isinstance(chart, list) or not chart:
            raise ValueError('谱面内容无效')
        count = note_count(chart)
        expected = selection.song['difficulties'][selection.difficulty].get('notes')
        if expected is not None and count != expected:
            raise ValueError(f'谱面音符数与目录不符：{key}，{count}/{expected}')
        events = compile_chart(chart)
        first_anchor(chart)
        if not all(0 <= 197+147.7*e.lane+e.dx < 1280 and 0 <= e.y < 720 for e in events):
            raise ValueError('谱面手势越出当前演出画面')
        check_stop()
        if not path.exists():
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory,suffix='.tmp',delete=False) as file:
                file.write(raw)
                temporary=Path(file.name)
            temporary.replace(path)
        return chart, {'path':str(path.resolve()), 'sha256':hashlib.sha256(raw).hexdigest(),
                       'notes':count, 'duration':events[-1].time, 'cache_hit':cache_hit}

    def prepare_online(self, difficulty, check_stop, progress=lambda done,total: None):
        """Validate the complete random-song pool before joining any online room."""
        from chart_policy import ChartSelection
        from song_catalog import BY_ID, available_difficulties
        selections = [ChartSelection(song['id'], name) for song in BY_ID.values()
                      for name in (('expert','special') if difficulty=='special' else (difficulty,))
                      if name in available_difficulties(song)]
        results = {}
        cancelled = Event()
        def worker_check():
            if cancelled.is_set():
                raise CancelledError('谱面准备已取消')
            check_stop()
        iterator = iter(selections)
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {}
            def submit():
                selection = next(iterator, None)
                if selection:
                    pending[pool.submit(self.get, selection, worker_check, 10)] = selection
            for _ in range(4):
                submit()
            progress(0,len(selections))
            try:
                while pending:
                    check_stop()
                    done,_ = wait(pending,timeout=.2,return_when=FIRST_COMPLETED)
                    for future in done:
                        selection = pending.pop(future)
                        _,metadata = future.result()
                        results[(selection.song_id,selection.difficulty)] = metadata
                        progress(len(results),len(selections))
                        submit()
            finally:
                cancelled.set()
                for future in pending:
                    future.cancel()
        return results
