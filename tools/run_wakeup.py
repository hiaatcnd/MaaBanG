"""Run the same bootstrap and StartWakeup action as the desktop, with isolated logs."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emulator', choices=['mumu', 'custom', 'none'], default='mumu')
    parser.add_argument('--emulator-path', default='')
    parser.add_argument('--instance', default='0')
    parser.add_argument('--arguments', default='[]')
    parser.add_argument('--adb-path', default='')
    parser.add_argument('--address', default='')
    parser.add_argument('--user', default='0')
    parser.add_argument('--package', default='com.bilibili.star.bili')
    parser.add_argument('--boot-timeout', default='180')
    parser.add_argument('--game-timeout', default='1200')
    parser.add_argument('--unknown-timeout', default='60')
    parser.add_argument('--no-download', action='store_true')
    parser.add_argument('--connect-only', action='store_true')
    args = parser.parse_args()
    from wakeup_host import bootstrap
    connection = bootstrap(vars(args))
    if args.connect_only:
        print(json.dumps(vars(connection), ensure_ascii=False))
        return 0
    from maa.controller import AdbController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from wakeup import StartWakeup
    Toolkit.init_option(ROOT / 'debug/wakeup-cli')
    controller = AdbController(connection.adb_path, connection.address)
    controller.set_screenshot_target_short_side(720)
    if not controller.post_connection().wait().succeeded:
        raise RuntimeError('ADB 控制器连接失败')
    resource = Resource()
    if not resource.post_bundle(ROOT / 'assets/resource').wait().succeeded:
        raise RuntimeError('加载资源失败')
    resource.register_custom_action('StartWakeup', StartWakeup())
    tasker = Tasker()
    tasker.bind(resource, controller)
    values = vars(args) | {'download': not args.no_download}
    job = tasker.post_task('StartWakeup', {'SW_Game': {'attach': values}})
    try:
        while not job.done:
            time.sleep(.2)
    except KeyboardInterrupt:
        tasker.post_stop().wait()
        return 130
    print('Task succeeded:', job.succeeded)
    return 0 if job.succeeded else 1


if __name__ == '__main__':
    sys.exit(main())
