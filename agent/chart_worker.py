"""Local low-latency playback worker. The UI Agent authorizes exactly one start."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from dataclasses import asdict

from chart_policy import JITTER_PROFILES
from chart_timing import compile_chart, first_anchor


def start_authorized_stage(controller, mode):
    if mode == 'online':
        return
    if mode != 'click':
        raise ValueError('未知开演方式')
    if not controller.post_click(1130,616).wait().succeeded:
        raise RuntimeError('开演输入未确认，不重试')


def controller_config(info):
    if info.get('type') != 'adb':
        raise ValueError('谱面演出目前需要 MuMu 安卓模拟器')
    adb, address = info['adb_path'], info['adb_serial']
    config = info.get('config', {})
    mumu = config.get('extras', {}).get('mumu', {})
    root = Path(mumu.get('path', Path(adb).parent.parent))
    manager = root/'nx_main/MuMuManager.exe'
    result = subprocess.run([str(manager),'info','-v','all'], capture_output=True,
                            check=True, encoding='utf8', timeout=15,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    devices = json.loads(result.stdout)
    matching = [(key, value) for key,value in devices.items()
                if value.get('is_android_started') and
                f"{value.get('adb_host_ip')}:{value.get('adb_port')}" == address]
    if len(matching) != 1:
        raise ValueError('未能将当前连接地址匹配到唯一 MuMu 实例')
    index, _ = matching[0]
    lib = Path(mumu.get('lib', root/'nx_device/15.0/shell/sdk/external_renderer_ipc.dll'))
    if not lib.is_file():
        raise ValueError('未找到 MuMu 快速截图组件；当前版本支持 MuMu 12/安卓15')
    return adb, address, {'extras':{'mumu':{'enable':True,'path':str(root),
            'lib':str(lib),'index':int(index),'app_package':'com.bilibili.star.bili'}}}


def play(config_path):
    from maa.controller import AdbController
    from maa.custom_action import CustomAction
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from chart_sync import FirstNoteLock, locate_note_y, stage_state, ChartPhaseTracker, SlewedClock
    import numpy as np

    config_path = Path(config_path)
    cfg = json.loads(config_path.read_text(encoding='utf8'))
    online = cfg.get('start_mode') == 'online'
    output = config_path.parent
    Toolkit.init_option(str(output))
    report = {'status':'preparing','events':[], 'phase_updates':[]}
    active = set()
    observer_stop = threading.Event()
    observer_failed = threading.Event()
    thread = None
    controller = None
    started = False
    lock = None
    last_start_frame = None
    start_trace = []

    def stopped():
        if (output/'stop').exists():
            raise RuntimeError('任务已停止')
        if observer_failed.is_set():
            raise RuntimeError(report.get('observer_error','演出画面丢失'))

    try:
        raw = Path(cfg['chart']['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != cfg['chart']['sha256']:
            raise ValueError('开演前谱面校验失败')
        chart = json.loads(raw)
        jitter, position = JITTER_PROFILES[cfg['jitter']]
        events = compile_chart(chart,seed=cfg['seed'],jitter_ms=jitter,position_jitter=position)
        anchor_time, lane, color = first_anchor(chart)
        report.update(seed=cfg['seed'],jitter_ms=jitter,position_jitter=position,
                      chart=cfg['chart'],anchor=[anchor_time,lane,color],
                      jitter_distribution='truncated_normal',jitter_sigma_divisor=3)
        adb,address,settings = controller_config(cfg['controller'])
        controller = AdbController(adb,address,screencap_methods=64,input_methods=4,config=settings)
        observer = AdbController(adb,address,screencap_methods=64,input_methods=1,config=settings)
        for c in (controller,observer):
            c.set_screenshot_target_short_side(720)
            if not c.post_connection().wait().succeeded or not c.post_screencap().wait().succeeded:
                raise RuntimeError('MuMu 演奏控制器连接失败')
            if c.cached_image.shape != (720,1280,3):
                raise RuntimeError('演奏画面比例必须为 16:9')
        # Only recognize actual interruption dialogs. Skill effects can recolor the
        # judgment line for several seconds and must not be treated as a lost stage.
        resource=Resource()
        root=Path(__file__).resolve().parent.parent
        bundle=root/'resource' if (root/'resource').is_dir() else root/'assets/resource'
        if not resource.post_bundle(bundle).wait().succeeded:
            raise RuntimeError('演出弹窗识别资源加载失败')
        popup_frame=None
        waiting_members=False
        class CheckOverlay(CustomAction):
            def run(self,context,argv):
                nonlocal waiting_members
                if online:
                    waiting=context.run_recognition('OL_WaitingMembers',popup_frame)
                    waiting_members=bool(waiting and waiting.hit)
                    for node in ('OL_Disconnected','CU_HomeBand','LV_Menu','OL_RoomPage','OL_TeamHome'):
                        result=context.run_recognition(node,popup_frame)
                        if result and result.hit:
                            report['observer_error']='联网房间退出：'+node
                            report['interrupted']=True
                            observer_failed.set()
                            return True
                hit=context.run_recognition('ChartWorkerOverlay',popup_frame,{
                    'ChartWorkerOverlay':{'recognition':'OCR','roi':[190,180,910,230],
                       'expected':(['^暂停$','中断演出返回主页'] if online else
                                   ['演出失败','^暂停$','中断演出返回主页'])}})
                if hit and hit.hit:
                    report['observer_error']='检测到暂停或演出失败，停止触控'
                    observer_failed.set()
                return True
        resource.register_custom_action('ChartWorkerOverlayCheck',CheckOverlay())
        popup_tasker=Tasker();popup_tasker.bind(resource,observer)
        (output/'armed').write_text('ready')
        deadline = time.perf_counter()+60
        while not (output/'start').exists():
            stopped()
            if time.perf_counter()>deadline:
                raise RuntimeError('等待开演确认超时')
            time.sleep(.05)
        stopped()
        lock = FirstNoteLock(travel_scale=.245)
        tracker = ChartPhaseTracker(chart)
        clock = SlewedClock()
        stage_seen = origin = initial_health = None
        start = time.perf_counter()
        started = True
        start_authorized_stage(controller,cfg.get('start_mode','click'))
        # Account for songs with a long lead-in; chart time is independent of fall speed.
        last_start_check=0.
        while time.perf_counter()-start < (180 if online else max(40,anchor_time+30)):
            stopped()
            before = time.perf_counter()
            if not controller.post_screencap().wait().succeeded:
                raise RuntimeError('首键截图失败')
            after = time.perf_counter()
            frame = controller.cached_image
            last_start_frame = frame
            state = stage_state(frame)
            if online and state is not None and np.mean(np.all(frame[425:525,385:895]>220,axis=2))>.65:
                popup_frame=frame
                popup_tasker.post_task('ChartWorkerOverlayCheck',{
                    'ChartWorkerOverlayCheck':{'action':'Custom','custom_action':'ChartWorkerOverlayCheck'}}).wait()
                if waiting_members:
                    continue
            if state is None:
                if online and after-last_start_check>.5:
                    popup_frame=frame
                    last_start_check=after
                    popup_tasker.post_task('ChartWorkerOverlayCheck',{
                        'ChartWorkerOverlayCheck':{'action':'Custom','custom_action':'ChartWorkerOverlayCheck'}}).wait()
                continue
            if stage_seen is None:
                stage_seen = after
                (output/'stage_started').write_text('started')
            health=frame[36:47,980:1170].astype(float)
            health_ratio=float(np.mean((health[:,:,1]>130) & (health[:,:,1]>health[:,:,2]*1.2)))
            if initial_health is None:
                initial_health=health_ratio
            elif health_ratio<initial_health-.025:
                raise RuntimeError('首键锁定前生命已减少，拒绝把后续音符当成首键')
            # Tour health carries over from the previous song, so it may start below full.
            if after-stage_seen > max(5,anchor_time+2):
                raise RuntimeError('首键识别超时')
            y = locate_note_y(frame,lane,color)
            start_trace.append({'time':(before+after)/2-start,'y':y,
                                'capture_ms':(after-before)*1000})
            if len(start_trace)>512:
                del start_trace[0]
            fitted = lock.observe((before+after)/2-start,y)
            if fitted is not None:
                report['lock'] = fitted
                origin = start+fitted['crossing']-anchor_time-.025
                break
        if origin is None:
            raise RuntimeError('未锁定首键')
        report['status'] = 'playing'

        def observe():
            nonlocal popup_frame
            last_popup_check=0.
            try:
                while not observer_stop.is_set():
                    before = time.perf_counter()
                    if observer.post_screencap().wait().succeeded:
                        after = time.perf_counter()
                        frame=observer.cached_image
                        if after-last_popup_check>.5 and (online or np.mean(np.all(frame[260:385,350:930]>220,axis=2))>.65):
                            popup_frame=frame
                            last_popup_check=after
                            popup_tasker.post_task('ChartWorkerOverlayCheck',{
                                'ChartWorkerOverlayCheck':{'action':'Custom',
                                                         'custom_action':'ChartWorkerOverlayCheck'}}).wait()
                            if observer_failed.is_set():
                                from PIL import Image
                                Image.fromarray(frame[:,:,::-1]).save(output/'interruption.png')
                                return
                        if after-before < .040:
                            estimate = tracker.observe((before+after)/2-origin,
                                                       frame,clock.value)
                            if estimate and clock.update(estimate['correction']):
                                report['phase_updates'].append(estimate)
                    observer_stop.wait(.12)
            except Exception as exc:
                report['observer_error'] = str(exc)
                observer_failed.set()

        thread = threading.Thread(target=observe,daemon=True)
        thread.start()
        for event in events:
            while True:
                stopped()
                now = time.perf_counter()
                correction = clock.advance(now)
                target = origin+event.time+correction
                if now>=target:
                    break
                time.sleep(min(target-now,.005))
            sent = time.perf_counter()
            if sent-target>.15:
                raise RuntimeError('演奏调度延迟过大，已停止发送过期音符')
            x,y = round(197+147.7*event.lane+event.dx),round(event.y)
            if event.action=='down':
                active.add(event.contact)
                job=controller.post_touch_down(x,y,contact=event.contact)
            elif event.action=='move':
                job=controller.post_touch_move(x,y,contact=event.contact)
            else:
                job=controller.post_touch_up(event.contact)
            ok=job.wait().succeeded
            report['events'].append({**asdict(event),'late_ms':(sent-target)*1000,
                                     'phase_ms':correction*1000,'ok':ok})
            if not ok:
                raise RuntimeError('触控输入失败')
            if event.action=='up':
                active.discard(event.contact)
        report['status']='input_complete'
    except Exception as exc:
        report.update(status='error',error=str(exc))
        if last_start_frame is not None and 'lock' not in report:
            from PIL import Image
            Image.fromarray(last_start_frame[:,:,::-1]).save(output/'first_note_failure.png')
    finally:
        report['start_trace']=start_trace
        if lock is not None:
            report['start_points']=lock.points
        observer_stop.set()
        if controller:
            for contact in tuple(active):
                controller.post_touch_up(contact).wait()
        if thread:
            thread.join(timeout=3)
        if controller and started and report['status']=='error' and not observer_failed.is_set() and not online:
            controller.post_click(1240,50).wait()
        (output/'playback.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf8')
    return 0 if report['status']=='input_complete' else 1


if __name__=='__main__':
    sys.exit(play(sys.argv[1]))
