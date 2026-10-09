"""Verify task migration using the actual Windows UI and disposable user data.

Pass a disposable extracted package whose app/libs/MFAAvalonia.Core.dll has
been replaced with the freshly built core. No game tasks are started.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import time

from song_interface import load_interface


MARKER = 'MaaBanGLivePresetsFirstV1'
PRESET = 'LivePresets'


def verify(package, work):
    package, work = Path(package).resolve(), Path(work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    preset = dict(name='演出预先设置', entry=PRESET, default_check=False,
                  option=[dict(name='预设火数', index=3)])
    old = [dict(name='每日免费招募', entry='DailyFreeRecruit', default_check=True),
           dict(name='Maa代打演出', entry='ChartLive', default_check=False)]
    interface = json.loads((package / 'app/interface.json').read_text(encoding='utf8'))
    if (package / 'app/interface.songs.json').is_file():
        interface = load_interface(package / 'app/interface.json')
    options = interface['option']
    def song_selection(name, node, value, difficulty):
        cases = options[name]['cases']
        index = next(i for i, case in enumerate(cases)
                     if case['pipeline_override'][node]['attach']['value'] == value)
        profile = cases[index]['option'][0]
        di = next(i for i, case in enumerate(options[profile]['cases']) if case['name'] == difficulty)
        return dict(name=name, index=index, sub_options=[dict(name=profile, index=di)])
    # Stored nested indices from the expanded interface must survive loading the
    # shared-catalog UI, including three independent tour songs/difficulties.
    old[1]['option'] = [dict(name='谱面演出模式', index=1, sub_options=[
        song_selection(f'谱面第{i}首歌曲', f'CL_song{i}', sid, difficulty)
        for i, sid, difficulty in ((1,'306','EASY'),(2,'359','HARD'),(3,'186','EXPERT'))])]
    old.append(dict(name='指定谱面直接演出', entry='DirectChartLive', default_check=False,
                    option=[song_selection('直接演出歌曲','DL_song1','306','SPECIAL')]))
    def assert_selections(original, current, label):
        for selection in original:
            saved = next(o for o in current if o['name'] == selection['name'])
            assert saved['index'] == selection['index'], (label, selection, saved)
            assert_selections(selection.get('sub_options', []), saved.get('sub_options', []), label)
    history = [f'{t["name"]}<|||>{t["entry"]}' for t in interface['task']]
    cases = [
        ('missing', dict(TaskItems=old, CurrentTasks=history), True),
        ('last', dict(TaskItems=[*old, preset], CurrentTasks=history), True),
        ('fresh', {}, True),
        ('reordered', dict(TaskItems=[*old, preset], CurrentTasks=history, **{MARKER: True}), False),
        ('deleted-after-migration', dict(TaskItems=old, CurrentTasks=history, **{MARKER: True}), False),
    ]
    results = []
    for name, initial, migrate in cases:
        data = work / name
        config = data / 'config'
        (config / 'instances').mkdir(parents=True)
        (config / 'config.json').write_text(json.dumps(dict(
            EnableAutoUpdateResource=False, BeforeTask=False,
            RememberAdb=False, AutoConnectAfterRefresh=False)), encoding='utf8')
        path = config / 'instances/default.json'
        initial = copy.deepcopy(initial)
        initial.update(BeforeTask=False, RememberAdb=False, AutoConnectAfterRefresh=False)
        path.write_text(json.dumps(initial, ensure_ascii=False), encoding='utf8')
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
        with (data / 'process.log').open('wb') as log:
            process = subprocess.Popen([str(package / 'app/MFAAvalonia.exe')],
                cwd=package / 'app', env=dict(os.environ, MAABANG_DATA_DIR=str(data),
                PYTHONUTF8='1'), stdout=log, stderr=log, startupinfo=info)
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f'{name}: UI exited: {process.returncode}')
                    try:
                        saved = json.loads(path.read_text(encoding='utf-8-sig'))
                        if saved.get(MARKER) and 'Resource' in saved:
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(.25)
                else:
                    raise TimeoutError(f'{name}: task loading did not finish')
                time.sleep(1)
                saved = json.loads(path.read_text(encoding='utf-8-sig'))
                tasks = saved['TaskItems']
                entries = [t['entry'] for t in tasks]
                if migrate:
                    assert entries[0] == PRESET, (name, entries)
                    assert entries.count(PRESET) == 1, (name, entries)
                else:
                    assert entries == [t['entry'] for t in initial['TaskItems']], (name, entries)
                for original in initial.get('TaskItems', []):
                    current = next(t for t in tasks if t['entry'] == original['entry'])
                    assert current['default_check'] == original['default_check'], name
                    if original['entry'] == PRESET:
                        fire = next(o for o in current['option'] if o['name'] == '预设火数')
                        assert fire['index'] == 3, (name, fire)
                    assert_selections(original.get('option', []), current.get('option', []), name)
                if name in ('missing', 'last'):
                    assert entries[1:1+len(old)] == [t['entry'] for t in old], (name, entries)
                results.append(dict(case=name, entries=entries, passed=True))
            finally:
                process.terminate()
                process.wait(timeout=15)
    (work / 'report.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    verify(args.package, args.work)
