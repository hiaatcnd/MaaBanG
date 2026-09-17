"""Run or inspect the built-in auto-live flow on a connected emulator."""
import argparse
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))


def main():
    from live_policy import LiveOptions, SONGS, DIFFICULTIES
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb',required=True)
    parser.add_argument('--address',default='127.0.0.1:16416')
    parser.add_argument('--mode',choices=['free','tour'],default='free')
    parser.add_argument('--song',choices=SONGS,default=SONGS[0])
    parser.add_argument('--difficulty',choices=DIFFICULTIES,default='expert')
    parser.add_argument('--fire',type=int,choices=range(4),default=3)
    parser.add_argument('--shortage',choices=['stop','lower'],default='stop')
    parser.add_argument('--max-rounds',default='')
    parser.add_argument('--prepare-only',action='store_true',help='只选歌和难度并读取准备页，不开演')
    args=parser.parse_args()
    values={k:getattr(args,k) for k in ('mode','song','difficulty','fire','shortage','max_rounds')}
    options=LiveOptions.parse(values)
    from maa.resource import Resource
    from maa.controller import AdbController
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from maa.custom_action import CustomAction
    from auto_live import AutoLive,LiveFlow,OPTION_NODES
    Toolkit.init_option(ROOT/'debug/auto_live')
    resource=Resource()
    assert resource.post_bundle(ROOT/'assets/resource').wait().succeeded
    controller=AdbController(args.adb,args.address)
    controller.set_screenshot_target_short_side(720)
    assert controller.post_connection().wait().succeeded
    tasker=Tasker(); tasker.bind(resource,controller)
    class Prepare(CustomAction):
        def run(self,context,argv):
            import traceback
            try:
                flow=LiveFlow(context,options)
                difficulty=flow.prepare_round()
                print(f'Prepared: {options.song}, {difficulty}, auto={flow.remaining()}, fire={flow.fire_balance()}',flush=True)
                return True
            except Exception:
                traceback.print_exc()
                return False
    resource.register_custom_action('AutoLive',Prepare() if args.prepare_only else AutoLive())
    overrides={OPTION_NODES[key]:{'attach':{'value':value}} for key,value in values.items()}
    job=tasker.post_task('AutoLive',overrides)
    try:
        while not job.done: time.sleep(.2)
    except KeyboardInterrupt:
        tasker.post_stop().wait()
        return 130
    print('Task succeeded:',job.succeeded,flush=True)
    return 0 if job.succeeded else 1


if __name__=='__main__':
    sys.exit(main())
