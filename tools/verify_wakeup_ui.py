"""Run only StartWakeup through an extracted desktop package with disposable settings.

This performs real emulator/game startup. Other tasks are explicitly unchecked.
The user profile is not read or modified. The started UI is closed after the result.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def verify(package, work, user='0', timeout=240):
    app = Path(package).resolve() / 'app'
    work = Path(work).resolve()
    (work/'config/instances').mkdir(parents=True, exist_ok=False)
    interface = json.loads((app/'interface.json').read_text(encoding='utf-8'))
    history = [f'{t["name"]}<|||>{t["entry"]}' for t in interface['task']]
    config = dict(BeforeTask='StartupScriptOnly', AfterTask='None', RememberAdb=False,
                  AutoDetectOnConnectionFailed=False, AutoConnectAfterRefresh=False,
                  EnableAutoUpdateResource=False, MaaBanGLivePresetsFirstV1=True,
                  CurrentTasks=history, TaskItems=[dict(name='开始唤醒', entry='StartWakeup', default_check=True,
                      option=[dict(name='唤醒游戏应用', data=dict(package='com.bilibili.star.bili', user=user))])])
    (work/'config/config.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    (work/'config/instances/default.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    existing = set((app/'debug/wakeup').glob('*/report.json'))
    # Keep the project's proven non-AppData IPC default. A restricted test host
    # can explicitly supply another permitted short directory; do not silently
    # move sockets to AppData, where libzmq 4.3.5 has known initialization issues.
    ipc = Path(os.environ.get('MAABANG_IPC_DIR', Path.home()/'.maabang/temp'))
    ipc.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, MAABANG_DATA_DIR=str(work), MAABANG_IPC_DIR=str(ipc),
               PYTHONUTF8='1', TEMP=str(ipc), TMP=str(ipc))
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    with (work/'process.log').open('wb') as log:
        process = subprocess.Popen([str(app/'MFAAvalonia.exe')], cwd=app, env=env,
                                   stdout=log, stderr=log, startupinfo=startup)
        try:
            deadline = time.monotonic()+timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'UI exited with {process.returncode}; see {work}')
                for path in set((app/'debug/wakeup').glob('*/report.json'))-existing:
                    try:
                        report = json.loads(path.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        continue
                    if report.get('status') in ('finished', 'error'):
                        (work/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                        assert report['status'] == 'finished', report
                        assert report['user'] == int(user), report
                        print(f'Desktop StartWakeup succeeded: Android user {user}; {path}')
                        return
                time.sleep(.5)
            raise TimeoutError(f'UI did not finish StartWakeup within {timeout}s; see {work}')
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--user', default='0')
    parser.add_argument('--timeout', type=int, default=240)
    args = parser.parse_args()
    verify(args.package, args.work, args.user, args.timeout)
