"""Local low-latency playback worker. The UI Agent authorizes exactly one start."""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from dataclasses import asdict

# Embedded Python can pin sys.path to a different installed app via its _pth
# file. A worker must load the modules beside the script it was asked to run.
sys.path.insert(0,str(Path(__file__).resolve().parent))

from chart_policy import JITTER_PROFILES
from chart_timing import compile_chart, first_anchor, GestureRecovery
from online_errors import room_error
from playback_controller import playback_options
from playback_diagnostics import finalize_playback, timed_touch


def start_authorized_stage(controller, mode):
    if mode == 'online':
        return
    if mode not in ('click','online_click'):
        raise ValueError('未知开演方式')
    if not controller.post_click(1130,616).wait().succeeded:
        raise RuntimeError('开演输入未确认，不重试')


def check_overlay(tasker):
    # This recognition-only action needs no default 200 ms action delays.
    completed=tasker.post_task('ChartWorkerOverlayCheck',{
        'ChartWorkerOverlayCheck':{'action':'Custom','custom_action':'ChartWorkerOverlayCheck',
                                   'pre_delay':0,'post_delay':0}}).wait()
    if not completed.succeeded:
        raise RuntimeError('演出弹窗检查失败，停止触控')
    return completed


def capture_observer_frame(observer, online):
    if observer.post_screencap().wait().succeeded:
        return observer.cached_image
    if online:
        raise RuntimeError('联网演出监视截图失败，停止触控')
    return None


def prime_phase_recovery(recovery, trace):
    """Reuse only completed captures from this stall, with a fresh final frame."""
    eligible = []
    for row in trace:
        capture = row['capture_ms'] / 1000
        stamp = row['time']
        if (row['positions'] is not None and 0 <= capture < .040 and
                stamp-capture/2 >= recovery.evidence_since and
                stamp+capture/2 <= recovery.started):
            eligible.append(row)
    for index, row in enumerate(eligible):
        # Accumulate the entire batch before deciding. If it ends too early,
        # the observer must supply a new frame before any phase is accepted.
        recovery.observe_positions(row['time'], row['positions'],
                                   evaluate=index == len(eligible)-1,
                                   received_at=recovery.started)
    return len(eligible)


def initialize_playback_backend(controllers, resource, bundle, connection_records=None):
    """Overlap independent queues, validating every backend before arming."""
    resource_job=resource.post_bundle(bundle)
    pending=[]
    for role,controller in zip(('touch','observer'),controllers):
        controller.set_screenshot_target_short_side(720)
        record={'role':role,'queued_unix':time.time()}
        if connection_records is not None:
            connection_records.append(record)
        connection=controller.post_connection()
        record['job_id']=getattr(connection,'job_id',None)
        # Each controller preserves its own order: capture follows connection.
        capture=controller.post_screencap()
        pending.append((controller,connection,capture,record))
    for controller,connection,capture,record in pending:
        record['connected']=bool(connection.wait().succeeded)
        record['completion_observed_unix']=time.time()
        if not record['connected'] or not capture.wait().succeeded:
            raise RuntimeError('演奏控制器连接或增强截图失败，请检查当前设备的截图增强和多点触控支持')
        if controller.cached_image.shape != (720,1280,3):
            raise RuntimeError('演奏画面比例必须为 16:9')
    if not resource_job.wait().succeeded:
        raise RuntimeError('演出弹窗识别资源加载失败')


def check_playback_overlay(context, frame, report, observer_failed, online):
    """Check one captured frame and stop input on fatal, exit or pause dialogs."""
    def recognize(name, **override):
        result=context.run_recognition(name,frame,{name:override} if override else {})
        return result if result and result.hit else None

    waiting_members=False
    if online:
        error=room_error(context,recognize)
        if error:
            name,text=error
            if name=='OL_Fatal':
                report['fatal']=True
                report['observer_error']='联网演出无法继续：'+text
            else:
                report['interrupted']=True
                report['observer_error']='联网房间退出：'+text
            observer_failed.set()
            return False
        for node in ('CU_HomeBand','LV_Menu','OL_RoomPage','OL_TeamHome'):
            if recognize(node):
                report['observer_error']='联网房间退出：'+node
                report['interrupted']=True
                observer_failed.set()
                return False
        # Only the first-note loop consumes this flag; playback never uses it.
        if report['status']=='preparing':
            waiting_members=bool(recognize('OL_WaitingMembers'))
    if recognize('ChartWorkerOverlay',recognition='OCR',roi=[190,180,910,230],
                 expected=(['^暂停$','中断演出返回主页'] if online else
                           ['演出失败','^暂停$','中断演出返回主页'])):
        report['observer_error']='检测到暂停或演出失败，停止触控'
        observer_failed.set()
    return waiting_members


def play(config_path):
    startup_started=time.perf_counter()
    from maa.controller import AdbController
    from maa.custom_action import CustomAction
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from maa.library import Library
    from maa.define import MaaAdbScreencapMethodEnum, MaaAdbInputMethodEnum
    from chart_sync import (FirstNoteLock, locate_note_y, stage_state,
                            ChartPhaseTracker, PhaseReacquirer, SlewedClock)
    import chart_sync
    import numpy as np

    config_path = Path(config_path)
    cfg = json.loads(config_path.read_text(encoding='utf8'))
    online = cfg.get('start_mode') in ('online','online_click')
    output = config_path.parent
    Toolkit.init_option(str(output))
    report = {'status':'preparing','events':[], 'phase_updates':[], 'startup_timings_ms':{}}
    report['implementation']={name:{'path':str(path),
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        for name,path in (('worker',Path(__file__).resolve()),
                          ('chart_sync',Path(chart_sync.__file__).resolve()))}
    active = set()
    observer_stop = threading.Event()
    observer_failed = threading.Event()
    thread = None
    controller = None
    stage_confirmed = False
    lock = None
    first_note_lock_frame = None
    last_start_frame = None
    start_trace = []
    phase_trace = []

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
        avoid_full_combo = cfg.get('avoid_full_combo', False)
        compile_started=time.perf_counter()
        events = compile_chart(chart,seed=cfg['seed'],jitter_ms=jitter,position_jitter=position,
                               avoid_full_combo=avoid_full_combo)
        anchor_time, lane, color = first_anchor(chart, online=online)
        report['startup_timings_ms']['chart_compile']=(time.perf_counter()-compile_started)*1000
        report.update(seed=cfg['seed'],jitter_ms=jitter,position_jitter=position,
                      avoid_full_combo=avoid_full_combo,
                      chart=cfg['chart'],anchor=[anchor_time,lane,color],
                      jitter_distribution='truncated_normal',jitter_sigma_divisor=3)
        options = playback_options(cfg['controller'])
        report['backend']={'allowed_input_methods':options.input_methods,
            'input_source':options.input_source,
            'configured_input_methods':options.configured_input_methods,
            'discovered_input_methods':options.discovered_input_methods,
            'active_backend':None,'selection_evidence':'not_exposed_by_framework',
            'runtime_fallback':False,'pid':os.getpid(),
            'framework_version':Library.version(),
            'framework_library':str(Library.framework_libpath),
            'toolkit_library':str(Library.toolkit_libpath),
            'native_log':str(output/'debug/maafw.log'),'connections':[]}
        controller = AdbController(options.adb_path,options.address,
                                   screencap_methods=MaaAdbScreencapMethodEnum.EmulatorExtras,
                                   input_methods=options.input_methods,config=options.config)
        observer = AdbController(options.adb_path,options.address,
                                 screencap_methods=MaaAdbScreencapMethodEnum.EmulatorExtras,
                                 input_methods=MaaAdbInputMethodEnum.AdbShell,config=options.config)
        # Only recognize actual interruption dialogs. Skill effects can recolor the
        # judgment line for several seconds and must not be treated as a lost stage.
        resource=Resource()
        root=Path(__file__).resolve().parent.parent
        bundle=root/'resource' if (root/'resource').is_dir() else root/'assets/resource'
        backend_started=time.perf_counter()
        try:
            initialize_playback_backend((controller,observer),resource,bundle,report['backend']['connections'])
        finally:
            report['startup_timings_ms']['controllers_and_resource']=(time.perf_counter()-backend_started)*1000
        popup_frame=None
        waiting_members=False
        class CheckOverlay(CustomAction):
            def run(self,context,argv):
                nonlocal waiting_members
                waiting_members=check_playback_overlay(context,popup_frame,report,observer_failed,online)
                return True
        resource.register_custom_action('ChartWorkerOverlayCheck',CheckOverlay())
        popup_tasker=Tasker();popup_tasker.bind(resource,observer)
        # Total from entering play, including its imports; no real-time timing work.
        report['startup_timings_ms']['ready_total']=(time.perf_counter()-startup_started)*1000
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
        try:
            start_authorized_stage(controller,cfg.get('start_mode','click'))
        except Exception:
            report['input_failed']=True
            raise
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
                check_overlay(popup_tasker)
                if waiting_members:
                    continue
            if state is None:
                if online and after-last_start_check>.5:
                    popup_frame=frame
                    last_start_check=after
                    check_overlay(popup_tasker)
                continue
            if stage_seen is None:
                stage_seen = after
                stage_confirmed = True
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
                first_note_lock_frame = frame.copy()
                origin = tracker.origin_from_anchor(start+fitted['crossing'],anchor_time)
                report['initial_touch_lead_ms']=tracker.phase_reference*1000
                break
        if origin is None:
            raise RuntimeError('未锁定首键')
        report['status'] = 'playing'
        phase_guard = threading.Lock()
        phase_recovery = None
        phase_reset_at = 0.

        def observe():
            nonlocal popup_frame
            last_popup_check=0.
            observer_ready=False
            try:
                while not observer_stop.is_set():
                    before = time.perf_counter()
                    frame=capture_observer_frame(observer,online)
                    if frame is not None:
                        after = time.perf_counter()
                        if after-last_popup_check>.5 and (online or np.mean(np.all(frame[260:385,350:930]>220,axis=2))>.65):
                            popup_frame=frame
                            last_popup_check=after
                            check_overlay(popup_tasker)
                            if observer_failed.is_set():
                                from PIL import Image
                                Image.fromarray(frame[:,:,::-1]).save(output/'interruption.png')
                                return
                            if online and not observer_ready:
                                # Hand over only after the observer has checked a real frame.
                                (output/'observer_ready').write_text('ready')
                                observer_ready=True
                        stamp = (before+after)/2
                        positions = ([(lane,locate_note_y(frame,lane)) for lane in range(7)]
                                     if after-before < .040 else None)
                        with phase_guard:
                            # A capture already in flight when the origin changes
                            # cannot contribute to the new phase history.
                            if before >= phase_reset_at:
                                phase_trace.append({'time':stamp-origin,
                                    'capture_ms':(after-before)*1000, 'positions':positions,
                                    'recovering':phase_recovery is not None})
                                del phase_trace[:-64]
                                if positions is not None:
                                    if phase_recovery is not None:
                                        phase_recovery.observe_positions(stamp-origin,positions,
                                            received_at=time.perf_counter()-origin)
                                    else:
                                        estimate = tracker.observe_positions(stamp-origin,positions,clock.value)
                                        if estimate and clock.update(estimate['correction']):
                                            report['phase_updates'].append({**estimate,
                                                'observed_at':stamp-origin,
                                                'clock_ms':clock.value*1000})
                    observer_stop.wait(.04 if phase_recovery is not None else .12)
            except Exception as exc:
                report['observer_error'] = str(exc)
                observer_failed.set()

        thread = threading.Thread(target=observe,daemon=True)
        thread.start()
        recovery = GestureRecovery()
        report['recoveries'] = []
        recovery_times = []

        def wait_event(event):
            while True:
                stopped()
                now = time.perf_counter()
                correction = clock.advance(now)
                target = origin+event.time+correction
                if now>=target:
                    return now,target,correction
                time.sleep(min(target-now,.005))

        for event in events:
            if recovery.skip(event):
                report['recoveries'][-1]['skipped_events'] += 1
                continue
            sent,target,correction = wait_event(event)
            if sent-target>.15:
                missed={'event':asdict(event),'late_ms':(sent-target)*1000,
                        'skipped_events':0,'released_contacts':sorted(active)}
                report['recoveries'].append(missed)
                recovery_times = [t for t in recovery_times if sent-t < 10]
                if sent-target>2 or len(recovery_times)>=3:
                    report['deadline_miss']=missed
                    raise RuntimeError('持续或过长的调度阻塞，无法安全恢复演奏')
                recovery_times.append(sent)
                # Release first, then measure whether the game advanced with
                # the host clock. Neither the stall duration nor stale phase
                # samples are evidence that it is safe to resume blind input.
                released = set(active)
                missed['release_inputs']=[]
                for contact in sorted(active):
                    stopped()
                    # Never replay an up whose submission/completion is unknown.
                    active.discard(contact)
                    measured={'action':'up','contact':contact}
                    missed['release_inputs'].append(measured)
                    try:
                        if not timed_touch(controller,'up',0,0,contact,measured):
                            raise RuntimeError('恢复时释放触点失败')
                    except Exception:
                        report['input_failed']=True
                        raise
                with phase_guard:
                    # The independent observer can keep capturing while a touch
                    # blocks. Reuse only this stall's captures, in this origin.
                    requested = time.perf_counter()
                    evidence_since = max(target, phase_reset_at)-origin
                    phase_recovery = PhaseReacquirer(tracker,requested-origin,clock.value,
                                                     sent-target,evidence_since=evidence_since)
                    missed['evidence_since'] = evidence_since
                    missed['seeded_frames'] = prime_phase_recovery(phase_recovery,phase_trace)
                while True:
                    stopped()
                    with phase_guard:
                        synced = phase_recovery.result
                        if synced is not None:
                            missed['resync'] = {**synced, 'requested_at':requested-origin,
                                'wait_ms':(time.perf_counter()-requested)*1000}
                            # All contacts are up and past events are discarded.
                            # Rebase once from fresh visual evidence; normal small
                            # corrections retain the original bounded slewed clock.
                            origin += synced['correction']
                            tracker = ChartPhaseTracker(chart)
                            clock = SlewedClock()
                            phase_reset_at = time.perf_counter()
                            phase_trace.clear()  # Cached timestamps belonged to the old origin.
                            phase_recovery = None
                            break
                    if time.perf_counter()-requested > PhaseReacquirer.timeout:
                        raise RuntimeError('卡顿后无法重新确认谱面同步，停止触控并保留现场')
                    time.sleep(.005)
                now = time.perf_counter()
                correction = clock.advance(now)
                recovery.begin(now-origin-correction,released)
                if recovery.skip(event):
                    missed['skipped_events'] += 1
                    continue
                sent,target,correction = wait_event(event)
            x,y = round(197+147.7*event.lane+event.dx),round(event.y)
            if event.action=='down':
                active.add(event.contact)
            elif event.action=='up':
                active.discard(event.contact)
            measured={**asdict(event),'late_ms':(sent-target)*1000,'phase_ms':correction*1000}
            report['events'].append(measured)
            try:
                if not timed_touch(controller,event.action,x,y,event.contact,measured):
                    raise RuntimeError('触控输入失败')
            except Exception:
                report['input_failed']=True
                raise
            if report['recoveries'] and 'resumed_at' not in report['recoveries'][-1]:
                report['recoveries'][-1]['resumed_at']=event.time
                report['recoveries'][-1]['resume_late_ms']=(sent-target)*1000
        # Keep observing through the original chart end even if the omitted
        # final tap/flick leaves no remaining input before the result wait.
        if avoid_full_combo:
            while True:
                stopped()
                now = time.perf_counter()
                remaining = origin+cfg['chart']['duration']+clock.advance(now)-now
                if remaining <= 0:
                    break
                time.sleep(min(remaining,.05))
        report['status']='input_complete'
    except Exception as exc:
        report.update(status='error',error=str(exc))
    finally:
        report['start_trace']=start_trace
        if lock is not None:
            report['start_points']=lock.points
        observer_stop.set()
        def save_diagnostics():
            if thread:
                thread.join(timeout=3)
            if report['status']=='error' or report.get('recoveries'):
                report['phase_trace'] = phase_trace
            # Defer encoding until all touches are released; image failures must
            # not suppress the report containing the original input failure.
            if last_start_frame is not None and 'lock' not in report:
                from PIL import Image
                Image.fromarray(last_start_frame[:,:,::-1]).save(output/'first_note_failure.png')
            if first_note_lock_frame is not None:
                from PIL import Image
                Image.fromarray(first_note_lock_frame[:,:,::-1]).save(output/'first_note_lock.png')
        finalize_playback(controller,active,report,output/'playback.json',
            allow_pause=stage_confirmed and report['status']=='error'
                        and not observer_failed.is_set() and not online,
            save_diagnostics=save_diagnostics)
    return 0 if report['status']=='input_complete' else 1


if __name__=='__main__':
    sys.exit(play(sys.argv[1]))
