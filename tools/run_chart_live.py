"""Exercise the same ChartLive task used by the desktop UI."""
import argparse
import os
from pathlib import Path
import sys
import time
import subprocess

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))


def main():
    if os.name=='nt':
        ipc_temp=Path.home()/'.maabang/temp'
        ipc_temp.mkdir(parents=True,exist_ok=True)
        os.environ['TEMP']=os.environ['TMP']=str(ipc_temp)
    from chart_policy import ChartOptions, OPTION_NODES, JITTER_PROFILES
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb',required=True)
    parser.add_argument('--address',required=True)
    parser.add_argument('--mode',choices=['free','tour_free','tour_fixed'],default='free')
    for i in range(1,4):
        parser.add_argument(f'--song{i}',default='306')
        parser.add_argument(f'--difficulty{i}',default='expert')
    parser.add_argument('--jitter',choices=JITTER_PROFILES,default='small')
    parser.add_argument('--fire',type=int,choices=range(4),default=1)
    parser.add_argument('--shortage',choices=['stop','items'],default='stop')
    parser.add_argument('--max-rounds',default='1')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--package',type=Path,help='Packaged app directory; use its embedded Agent over IPC')
    args=parser.parse_args()
    if args.package and args.prepare_only:
        parser.error('--package tests the actual packaged task and cannot use --prepare-only')
    values={key:getattr(args,key) for key in OPTION_NODES}
    options=ChartOptions.parse(values)
    from maa.controller import AdbController
    from maa.custom_action import CustomAction
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from chart_live import ChartLive, ChartLiveFlow
    Toolkit.init_option('debug/chart_live_cli')
    controller=AdbController(args.adb,args.address)
    controller.set_screenshot_target_short_side(720)
    assert controller.post_connection().wait().succeeded
    resource=Resource()
    assert resource.post_bundle(args.package/'resource' if args.package else ROOT/'assets/resource').wait().succeeded
    tasker=Tasker();tasker.bind(resource,controller)
    class Prepare(CustomAction):
        def run(self,context,argv):
            import traceback
            flow=ChartLiveFlow(context,options,Path('debug/chart_live_prepare')/time.strftime('%Y%m%d-%H%M%S'))
            try:
                selections,charts=flow.prepare_round()
                flow.configure_stage()
                flow.snap();flow.save_frame('ready.png')
                print('Prepared',selections,flush=True)
                return True
            except Exception:
                traceback.print_exc()
                flow.snap();flow.save_frame('error.png')
                return False
    client=process=None
    if args.package:
        from maa.agent_client import AgentClient
        client=AgentClient();client.set_timeout(15000)
        assert client.bind(resource)
        package=args.package.resolve()
        process=subprocess.Popen([str(package/'python/python.exe'),str(package/'agent/main.py'),client.identifier],
                                 cwd=package,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert client.connect()
        assert 'ChartLive' in client.custom_action_list
    else:
        resource.register_custom_action('ChartLive',Prepare() if args.prepare_only else ChartLive())
    overrides={OPTION_NODES[key]:{'attach':{'value':value}} for key,value in values.items()}
    job=tasker.post_task('ChartLive',overrides)
    try:
        while not job.done:
            time.sleep(.2)
    except KeyboardInterrupt:
        tasker.post_stop().wait()
        return 130
    finally:
        if client:
            client.disconnect()
        if process:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate();process.wait(timeout=5)
    print('Task succeeded:',job.succeeded,flush=True)
    return 0 if job.succeeded else 1


if __name__=='__main__':
    sys.exit(main())
