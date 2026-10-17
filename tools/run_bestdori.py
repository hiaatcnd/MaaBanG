"""Development-only limited game scan; normal use is MaaBanG's Tools page."""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb')
    parser.add_argument('--address', default='127.0.0.1:16416')
    parser.add_argument('--scan-limit', type=int, help='Development: inspect only N cards and write a partial scan')
    parser.add_argument('--stars', default='1,2,3,4,5')
    parser.add_argument('--maximize', default='', help='Development scan: comma-separated level,skill,master to skip reading')
    args = parser.parse_args()
    if not args.adb or not args.scan_limit or args.scan_limit < 1:
        parser.error('日常使用请打开 MaaBanG 的工具页；局部验证须指定 --adb 和 --scan-limit')
    from maa.resource import Resource
    from maa.controller import AdbController
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from maa.custom_action import CustomAction
    from bestdori_scan import BestdoriScan
    from bestdori_catalog import load
    Toolkit.init_option(ROOT/'debug/bestdori-cli')
    resource = Resource()
    assert resource.post_bundle(ROOT/'assets/resource').wait().succeeded
    controller = AdbController(args.adb, args.address)
    controller.set_screenshot_target_short_side(720)
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    tasker.bind(resource, controller)
    class Inspect(CustomAction):
        def run(self, context, argv):
            catalog = load()
            scope = {'bands': list(map(int, catalog['bands'])),
                     'members': list(map(int, catalog['characters'])),
                     'rarities': list(map(int, args.stars.split(',')))}
            maximize = {field: True for field in args.maximize.split(',') if field}
            report = BestdoriScan(context, catalog, scope, ROOT/'debug/bestdori-inspect', maximize=maximize).run(limit=args.scan_limit)
            print(report, flush=True)
            return bool(report['observations'])
    resource.register_custom_action('BestdoriProfiles', Inspect())
    job = tasker.post_task('BestdoriProfiles')
    try:
        while not job.done:
            time.sleep(.2)
    except KeyboardInterrupt:
        tasker.post_stop().wait()
        raise
    return 0 if job.succeeded else 1


if __name__ == '__main__':
    sys.exit(main())
