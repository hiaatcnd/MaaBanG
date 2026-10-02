"""Run the standalone live preset task used by the desktop UI; never starts a song."""
import argparse
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb',required=True)
    parser.add_argument('--address',default='127.0.0.1:16416')
    parser.add_argument('--stage',choices=['recommended','keep'],default='recommended')
    parser.add_argument('--fever',choices=['off','on'],default='off')
    parser.add_argument('--fire',type=int,choices=range(4),default=1)
    args=parser.parse_args()
    from maa.controller import AdbController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from live_presets import LivePresets, NODES
    Toolkit.init_option(ROOT/'debug/live_presets_cli')
    controller=AdbController(args.adb,args.address)
    controller.set_screenshot_target_short_side(720)
    assert controller.post_connection().wait().succeeded
    resource=Resource()
    assert resource.post_bundle(ROOT/'assets/resource').wait().succeeded
    resource.register_custom_action('LivePresets',LivePresets())
    tasker=Tasker();tasker.bind(resource,controller)
    values=dict(stage=args.stage=='recommended',fever=args.fever,fire=args.fire)
    job=tasker.post_task('LivePresets',{NODES[key]:{'attach':{'value':value}} for key,value in values.items()})
    try:
        while not job.done:time.sleep(.2)
    except KeyboardInterrupt:
        tasker.post_stop().wait()
        return 130
    print('Task succeeded:',job.succeeded,flush=True)
    return 0 if job.succeeded else 1

if __name__=='__main__':sys.exit(main())
