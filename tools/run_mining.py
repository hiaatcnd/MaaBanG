"""Run the same mining actions as the desktop UI against an ADB device."""
import argparse
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task',choices=['MineFullCombo','MineStories','MineChallenges'])
    parser.add_argument('--adb',required=True)
    parser.add_argument('--address',default='127.0.0.1:16416')
    parser.add_argument('--max-rounds',default='1')
    parser.add_argument('--fire',type=int,choices=range(4),default=0)
    parser.add_argument('--shortage',choices=['stop','items'],default='stop')
    parser.add_argument('--practice',action='store_true')
    parser.add_argument('--unlock',action='store_true')
    parser.add_argument('--stars',default='1,2,3')
    parser.add_argument('--stage',choices=['main','special'],default='main')
    parser.add_argument('--difficulties',nargs='*',choices=['easy','normal','hard','expert','special'],
                        default=['easy','normal','hard','expert','special'])
    parser.add_argument('--no-stories',action='store_true')
    parser.add_argument('--no-memories',action='store_true')
    parser.add_argument('--inspect-only',action='store_true',help='Only inspect next FC candidate or member filters; no spending/playback')
    args = parser.parse_args()
    from mining_policy import MiningOptions,NODES,DIFFICULTY_NODES
    values = dict(max_rounds=args.max_rounds,practice=args.practice,unlock=args.unlock,
                  stars=args.stars,stage=args.stage,stories=not args.no_stories,memories=not args.no_memories,
                  difficulties=args.difficulties)
    values.update(fire=args.fire,shortage=args.shortage)
    options = MiningOptions.parse(values)
    from maa.resource import Resource
    from maa.controller import AdbController
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from maa.custom_action import CustomAction
    import mining
    Toolkit.init_option(ROOT/'debug/mining_cli')
    controller = AdbController(args.adb,args.address)
    controller.set_screenshot_target_short_side(720)
    assert controller.post_connection().wait().succeeded
    resource = Resource()
    assert resource.post_bundle(ROOT/'assets/resource').wait().succeeded
    tasker = Tasker()
    tasker.bind(resource,controller)
    class Inspect(CustomAction):
        def run(self,context,argv):
            import traceback
            output = ROOT/'debug/mining_inspect'
            output.mkdir(exist_ok=True)
            flow = getattr(mining,args.task).flow_type(context,options,output)
            try:
                if args.task == 'MineFullCombo':
                    print('Candidates:',flow.next_song(set()),flush=True)
                elif args.task == 'MineStories':
                    flow.member_list()
                    flow.filter_unread(options.memories)
                    print('Member filters verified',flush=True)
                else:
                    flow.navigate_menu()
                    flow.click('MN_ChallengeEntry')
                    flow.wait('MN_ChallengeSelect')
                    print('Challenge entry verified',flush=True)
                flow.save_frame('inspected.png')
                return True
            except Exception:
                traceback.print_exc()
                if flow.image is not None:
                    flow.save_frame('error.png')
                return False
    resource.register_custom_action(args.task,Inspect() if args.inspect_only else getattr(mining,args.task)())
    overrides = {NODES[key]:{'attach':{'value':value}} for key,value in values.items() if key in NODES}
    overrides.update({node:{'attach':{'enabled':d in options.difficulties}} for d,node in DIFFICULTY_NODES.items()})
    job = tasker.post_task(args.task,overrides)
    try:
        while not job.done:
            time.sleep(.2)
    except KeyboardInterrupt:
        tasker.post_stop().wait()
        raise
    print('Mining task succeeded:',job.succeeded,flush=True)
    return 0 if job.succeeded else 1


if __name__ == '__main__':
    sys.exit(main())
