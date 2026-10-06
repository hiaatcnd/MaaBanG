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
    from chart_policy import ChartOptions, OPTION_NODES, JITTER_PROFILES, COOP_ROOMS, COOP_ROOM_GROUPS
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb',required=True)
    parser.add_argument('--address',required=True)
    parser.add_argument('--mode',choices=['free','tour_free','tour_fixed','team','coop','challenge'],default='free')
    parser.add_argument('--direct',action='store_true',help='从最后准备页直接按指定谱面打一首，不选歌或识别曲名')
    for i in range(1,4):
        parser.add_argument(f'--song{i}',default='306')
        parser.add_argument(f'--difficulty{i}',default='expert')
    parser.add_argument('--jitter',choices=JITTER_PROFILES,default='small')
    parser.add_argument('--cp',type=int,choices=[200,400,800,1600],default=200)
    parser.add_argument('--cp-song',default='',help='挑战活动歌曲ID或完整歌名；留空沿用当前活动选曲')
    parser.add_argument('--coop-room',choices=COOP_ROOMS,default='free')
    parser.add_argument('--coop-room-group',choices=COOP_ROOM_GROUPS,default='normal')
    parser.add_argument('--coop-song',default='',help='提交的中国服歌曲ID或歌名；留空不指定歌曲')
    parser.add_argument('--shortage',choices=['stop','items'],default='stop')
    parser.add_argument('--max-rounds',default='')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--package',type=Path,help='Packaged app directory; use its embedded Agent over IPC')
    args=parser.parse_args()
    task_name='DirectChartLive' if args.direct else 'ChartLive'
    if args.direct:
        from direct_chart_live import OPTION_NODES
        if args.mode!='free':
            parser.error('--direct uses the current prepared page; omit --mode')
    if args.package and args.prepare_only:
        parser.error('--package tests the actual packaged task and cannot use --prepare-only')
    values={key:getattr(args,key) for key in OPTION_NODES if hasattr(args,key)}
    options=ChartOptions.parse(values)
    if args.prepare_only and options.mode in ('team','coop'):
        parser.error('--prepare-only is not supported for automatically starting online rooms')
    from maa.controller import AdbController
    from maa.custom_action import CustomAction
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from chart_live import ChartLive, ChartLiveFlow
    from direct_chart_live import DirectChartLive, DirectChartLiveFlow
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
            from cp_live import CPLiveFlow
            flow_type=DirectChartLiveFlow if args.direct else CPLiveFlow if options.mode=='challenge' else ChartLiveFlow
            flow=flow_type(context,options,Path('debug/chart_live_prepare')/time.strftime('%Y%m%d-%H%M%S'))
            try:
                selections,charts=flow.prepare_round()
                if not selections:
                    print('Preparation stopped:',flow.report['status'],flush=True)
                    return True
                flow.disable_mv()
                if options.mode=='challenge':
                    balance=flow.verify_chart_start(1,selections[0],options.cp)
                    print(f'CP verified before start: {balance}, cost: {options.cp}',flush=True)
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
        assert task_name in client.custom_action_list
    else:
        resource.register_custom_action(task_name,Prepare() if args.prepare_only else
                                        DirectChartLive() if args.direct else ChartLive())
    overrides={OPTION_NODES[key]:{'attach':{'value':value}} for key,value in values.items()}
    job=tasker.post_task(task_name,overrides)
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
