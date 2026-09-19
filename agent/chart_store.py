"""Download and validate official Bestdori charts before any live is started."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from chart_timing import compile_chart, first_anchor
from live_policy import DIFFICULTIES


def note_count(chart):
    return sum(sum(not p.get('hidden', False) for p in n['connections'])
               if n['type'] in ('Long', 'Slide') else 1
               for n in chart if n['type'] not in ('BPM', 'System'))


class ChartStore:
    def __init__(self, directory='cache/charts'):
        self.directory = Path(directory)

    def get(self, selection):
        key = f'{selection.song_id}_{selection.difficulty}'
        path = self.directory / (key+'.json')
        if path.exists():
            raw = path.read_bytes()
        else:
            url = f'https://bestdori.com/api/charts/{selection.song_id}/{selection.difficulty}.json'
            request = Request(url, headers={'User-Agent':'Mozilla/5.0 MaaBanG',
                                           'Referer':'https://bestdori.com/tool/chartsimulator'})
            for attempt in range(3):
                try:
                    with urlopen(request, timeout=30) as response:
                        raw = response.read(8*1024*1024+1)
                    break
                except (URLError, TimeoutError, ConnectionError) as exc:
                    if attempt==2 or isinstance(exc,HTTPError) and exc.code not in (429,500,502,503,504):
                        raise
                    time.sleep(attempt+1)
            if len(raw)>8*1024*1024:
                raise ValueError('谱面文件过大')
        chart = json.loads(raw)
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
        if not path.exists():
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory,suffix='.tmp',delete=False) as file:
                file.write(raw)
                temporary=Path(file.name)
            temporary.replace(path)
        return chart, {'path':str(path.resolve()), 'sha256':hashlib.sha256(raw).hexdigest(),
                       'notes':count, 'duration':events[-1].time}
