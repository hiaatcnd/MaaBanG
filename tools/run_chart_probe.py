"""Experimental chart player from a verified ready page; explicit 0/1-fire tests."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
import subprocess
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', required=True)
    parser.add_argument('--address', required=True)
    parser.add_argument('--mumu-path', required=True)
    parser.add_argument('--mumu-lib', required=True)
    parser.add_argument('--mumu-index', type=int, required=True)
    parser.add_argument('--chart', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    parser.add_argument('--offset-ms', type=float, default=0)
    parser.add_argument('--song', default='306')
    parser.add_argument('--difficulty', default='easy', choices=['easy','normal','hard','expert','special'])
    parser.add_argument('--jitter-ms', type=float, default=0)
    parser.add_argument('--position-jitter', type=float, default=0)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--slide-motion-delay-ms', type=float, default=0,
                        help='Experimental slide movement calibration (0..40 ms), preserving head timing')
    parser.add_argument('--fire', type=int, choices=(0,1), default=0)
    parser.add_argument('--execute-test', action='store_true')
    parser.add_argument('--observe-seconds', type=float, default=0,
                        help='Diagnostic capture using a separate controller (0..180)')
    parser.add_argument('--continuous-sync', action='store_true',
                        help='Experimental multi-note clock correction calibrated ONLY at speed 9.80')
    parser.add_argument('--execute-zero-fire', action='store_true')
    args = parser.parse_args()
    if not (args.execute_zero_fire or args.execute_test):
        parser.error('Explicit --execute-test or --execute-zero-fire is required')
    if args.execute_zero_fire and args.fire != 0:
        parser.error('--execute-zero-fire cannot consume fire; use --execute-test for 1 fire')
    if not -100 <= args.offset_ms <= 100:
        parser.error('Offset must be finite and within +/-100 ms')
    if not 0 <= args.observe_seconds <= 180:
        parser.error('Diagnostic observation must be within 0..180 seconds')
    if not 0 <= args.slide_motion_delay_ms <= 40:
        parser.error('Slide motion delay must be within 0..40 ms')
    raw = args.chart.read_bytes()
    manifest = json.loads((ROOT/'docs/data/chart_probe_manifest.json').read_text(encoding='utf8'))
    selected = next((c for c in manifest['charts'] if c['song_id'] == args.song and c['difficulty'] == args.difficulty), None)
    if selected is None:
        parser.error('Chart must be in the reviewed probe manifest')
    expected = selected['sha256']
    if hashlib.sha256(raw).hexdigest() != expected:
        parser.error('Chart snapshot does not match the reviewed manifest')
    manager = Path(args.mumu_path)/'nx_main'/'MuMuManager.exe'
    devices = json.loads(subprocess.run([str(manager), 'info', '-v', 'all'],
                         check=True, capture_output=True, encoding='utf-8', timeout=15).stdout)
    device = devices.get(str(args.mumu_index), {})
    address = f"{device.get('adb_host_ip')}:{device.get('adb_port')}"
    if not device.get('is_android_started') or address != args.address:
        parser.error('MuMu screenshot instance and ADB input address must match')
    from chart_timing import compile_chart, first_anchor
    chart = json.loads(raw)
    events = compile_chart(chart, seed=args.seed, jitter_ms=args.jitter_ms,
                           position_jitter=args.position_jitter,
                           slide_motion_delay=args.slide_motion_delay_ms/1000)
    anchor_time, anchor_lane, anchor_color = first_anchor(chart)
    if anchor_color not in ('cyan', 'green'):
        parser.error('This first-note detector requires a tap or hold/slide head in the opening chord')
    args.output.mkdir(parents=True, exist_ok=False)

    from PIL import Image
    from maa.controller import AdbController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from maa.custom_action import CustomAction
    from auto_live import LiveFlow
    from live_policy import LiveOptions
    from chart_sync import locate_note_y, FirstNoteLock, stage_state, ChartPhaseTracker, SlewedClock

    Toolkit.init_option(args.output)
    cfg = {'extras': {'mumu': {
        'enable': True, 'path': args.mumu_path, 'lib': args.mumu_lib,
        'index': args.mumu_index, 'app_package': 'com.bilibili.star.bili'}}}
    controller = AdbController(args.adb, args.address, screencap_methods=64,
                               input_methods=4, config=cfg)
    controller.set_screenshot_target_short_side(720)
    if not controller.post_connection().wait().succeeded:
        raise RuntimeError('Controller connection failed')
    observer = None
    observer_thread = None
    observer_stop = threading.Event()
    if args.observe_seconds or args.continuous_sync:
        observer = AdbController(args.adb, args.address, screencap_methods=64,
                                 input_methods=1, config=cfg)
        observer.set_screenshot_target_short_side(720)
        if not observer.post_connection().wait().succeeded:
            raise RuntimeError('Diagnostic controller connection failed')
    resource = Resource()
    if not resource.post_bundle(ROOT/'assets/resource').wait().succeeded:
        raise RuntimeError('Resource load failed')
    tasker = Tasker(); tasker.bind(resource, controller)
    preflight = {}

    class Verify(CustomAction):
        def run(self, context, argv):
            try:
                options = LiveOptions.parse(dict(mode='free',song=args.song,difficulty=args.difficulty,
                                                  fire=args.fire,shortage='stop',max_rounds='1'))
                flow = LiveFlow(context, options)
                flow.wait_ready()
                from costume_unlock import normalized
                if normalized(flow.text([220,541,440,38])) != normalized(selected['title']):
                    raise RuntimeError('Unexpected song')
                if flow.text([113,561,104,33]).strip().lower() != args.difficulty:
                    raise RuntimeError('Unexpected difficulty')
                if not flow.reco('LV_AutoOff'):
                    raise RuntimeError('Game auto must already be OFF')
                if args.continuous_sync:
                    flow.tap(960,650); flow.pause(.6)
                    flow.tap(527,155); flow.pause(.3); flow.snap()
                    preflight['light_mode'] = bool(flow.pink(flow.image[309:326,897:914]))
                    Image.fromarray(flow.image[:,:,::-1]).save(args.output/'effects.png')
                    flow.tap(295,155); flow.pause(.3); flow.snap()
                    speed = flow.text([365,288,110,55]).strip()
                    Image.fromarray(flow.image[:,:,::-1]).save(args.output/'speed.png')
                    if speed != '9.80':
                        raise RuntimeError(f'Continuous sync calibration requires speed 9.80, got {speed!r}')
                    flow.tap(640,601); flow.wait_ready()
                flow.configure_fire(args.fire); flow.wait_ready()
                preview = flow.fire_preview()
                time.sleep(.15); flow.snap()
                if preview != flow.fire_preview() or preview[0]-preview[1] != args.fire:
                    raise RuntimeError('Requested fire consumption not confirmed')
                if not flow.reco('LV_AutoOff'):
                    raise RuntimeError('Game auto changed')
                Image.fromarray(flow.image[:,:,::-1]).save(args.output/'ready.png')
                return True
            except Exception as exc:
                print(f'Preflight failed: {exc}', flush=True)
                return False

    resource.register_custom_action('ChartProbeVerify', Verify())
    verified = tasker.post_task('ChartProbeVerify', {'ChartProbeVerify': {
        'action':'Custom', 'custom_action':'ChartProbeVerify'}}).wait().succeeded
    if not verified:
        return 1
    report = {'status':'starting', 'chart_sha256':expected, 'offset_ms':args.offset_ms,
              'song':selected, 'fire':args.fire, 'jitter_ms':args.jitter_ms, 'seed':args.seed,
              'position_jitter':args.position_jitter,
              'gesture_config':{'tap_ms':25,'flick_ms':56,'move_interval_ms':8,
                                'slide_motion_delay_ms':args.slide_motion_delay_ms},
              'continuous_sync':args.continuous_sync,'phase_updates':[],
              'preflight':preflight,
              'sync_config':({'speed':9.8,'travel_scale':.245,'phase_reference_ms':37,
                              'correction_rate_ms_per_second':10,'correction_limit_ms':80,
                              'capture_limit_ms':40,'sample_window_seconds':2.5}
                             if args.continuous_sync else None),
              'anchor':{'time':anchor_time,'lane':anchor_lane,'color':anchor_color},
              'observations':[], 'events':[], 'lock':None}
    recent_images = []
    active = set()
    lock = FirstNoteLock(travel_scale=.245 if args.continuous_sync else None)
    phase_tracker = ChartPhaseTracker(chart) if args.continuous_sync else None
    clock = SlewedClock()
    origin = None
    stage_seen = None
    start = time.perf_counter()
    try:
        if not controller.post_click(1130,616).wait().succeeded:
            raise RuntimeError('Start input failed')
        while time.perf_counter()-start < 25:
            before = time.perf_counter()
            if not controller.post_screencap().wait().succeeded:
                raise RuntimeError('Screencap failed')
            after = time.perf_counter(); frame = controller.cached_image
            state = stage_state(frame)
            if state is None:
                continue
            if not state:
                raise RuntimeError('Health lost before sync; refusing a later-note lock')
            if stage_seen is None:
                stage_seen = after
            if after-stage_seen > max(5, anchor_time+2):
                raise RuntimeError('First-note acquisition window expired')
            y = locate_note_y(frame, anchor_lane, anchor_color)
            stamp = (before+after)/2-start
            report['observations'].append({'t':stamp, 'capture_ms':(after-before)*1000, 'y':y})
            if y is not None:
                recent_images.append((len(report['observations']), frame.copy()))
                recent_images = recent_images[-30:]
            fitted = lock.observe(stamp, y)
            if fitted is not None:
                report['lock'] = fitted
                origin = start+fitted['crossing']-anchor_time+args.offset_ms/1000
                print('First-note lock:', fitted, flush=True)
                break
        if origin is None:
            raise RuntimeError('First-note lock timed out')
        if observer is not None:
            def observe():
                deadline = (origin+events[-1].time+1 if args.continuous_sync
                            else time.perf_counter()+args.observe_seconds)
                observations = []
                while time.perf_counter() < deadline and not observer_stop.is_set():
                    before_capture = time.perf_counter()
                    if observer.post_screencap().wait().succeeded:
                        after_capture = time.perf_counter()
                        stamp = after_capture-origin
                        frame = observer.cached_image
                        name = f'watch_{stamp:08.3f}.jpg'
                        if args.observe_seconds and stamp <= args.observe_seconds:
                            Image.fromarray(frame[:,:,::-1]).save(args.output/name, quality=85)
                            observations.append({'chart_time':stamp,'file':name})
                        if phase_tracker is not None and after_capture-before_capture < .040:
                            estimate = phase_tracker.observe((before_capture+after_capture)/2-origin,
                                                             frame,clock.value)
                            if estimate is not None and clock.update(estimate['correction']):
                                report['phase_updates'].append({'chart_time':stamp,**estimate,
                                                               'applied_ms':clock.value*1000})
                    observer_stop.wait(.12)
                (args.output/'watch.json').write_text(json.dumps(observations),encoding='utf8')
            observer_thread = threading.Thread(target=observe, daemon=True)
            observer_thread.start()
        report['status'] = 'playing'
        for event in events:
            while True:
                now = time.perf_counter()
                correction = clock.advance(now) if args.continuous_sync else 0.
                target = origin+event.time+correction
                delay = target-now
                if delay <= 0:
                    break
                time.sleep(min(delay,.005) if args.continuous_sync else delay)
            sent = time.perf_counter()
            if sent-target > .15:
                raise RuntimeError('Timing overrun; refusing to replay stale notes')
            if event.action == 'down':
                active.add(event.contact)
                job = controller.post_touch_down(round(197+147.7*event.lane+event.dx),round(event.y),contact=event.contact)
            elif event.action == 'move':
                job = controller.post_touch_move(round(197+147.7*event.lane+event.dx),round(event.y),contact=event.contact)
            else:
                job = controller.post_touch_up(event.contact)
            ok = job.wait().succeeded
            report['events'].append({**asdict(event),'late_ms':(sent-target)*1000,
                                     'phase_ms':correction*1000,
                                     'api_ms':(time.perf_counter()-sent)*1000,'ok':ok})
            if not ok:
                raise RuntimeError('Touch input failed')
            if event.action == 'up':
                active.discard(event.contact)
        report['status'] = 'input_complete_unverified'
        print('Input finished; result still requires visual verification.', flush=True)
    except (Exception, KeyboardInterrupt) as exc:
        report['status'] = 'failed'
        report['error'] = repr(exc)
        print('Probe stopped:', repr(exc), flush=True)
    finally:
        observer_stop.set()
        # Include attempted downs with uncertain acknowledgments, but never send
        # an extra up for an already released contact on the Android backend.
        for contact in tuple(active):
            controller.post_touch_up(contact).wait()
        if observer_thread is not None:
            observer_thread.join(timeout=5)
        if report['status'] == 'failed':
            controller.post_click(1240,50).wait()  # stage pause; never a paid recovery button
        for index, frame in recent_images:
            Image.fromarray(frame[:,:,::-1]).save(args.output/f'note_{index:04}.png')
        report['track'] = lock.points
        (args.output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
        if controller.post_screencap().wait().succeeded:
            Image.fromarray(controller.cached_image[:,:,::-1]).save(args.output/'after.png')
    if report['status'] == 'failed':
        return 1
    time.sleep(12)
    if controller.post_screencap().wait().succeeded:
        Image.fromarray(controller.cached_image[:,:,::-1]).save(args.output/'result.png')
    class ReadResult(CustomAction):
        def run(self, context, argv):
            try:
                options = LiveOptions.parse(dict(mode='free',song=args.song,difficulty=args.difficulty,
                                                  fire=args.fire,shortage='stop',max_rounds='1'))
                flow = LiveFlow(context, options)
                previous = None
                deadline = time.monotonic()+45
                while time.monotonic() < deadline:
                    flow.snap()
                    if flow.dismiss_daily_reward():
                        continue
                    if flow.hit_text([350,70,320,110], '达成(?:奖励|报酬)'):
                        button = flow.hit_text([510,500,260,145], '^OK$|^确定$')
                        if button:
                            flow.tap_hit(button); flow.pause(1)
                            continue
                    if flow.hit_text([175,48,700,54], '达成(?:奖励|报酬)一览'):
                        button = flow.hit_text([510,580,260,80], '^关闭$')
                        if button:
                            flow.tap_hit(button); flow.pause(1)
                            continue
                    if flow.hit_text([190,200,350,90], '演出失败'):
                        report['status'] = 'game_failed'
                        Image.fromarray(flow.image[:,:,::-1]).save(args.output/'judgment.png')
                        return True
                    reward = bool(flow.reco('LV_Rewards') or flow.hit_text([100,420,145,43], '演出报酬'))
                    if reward:
                        button = flow.hit_text([940,602,274,100], '^下一步$')
                        report.setdefault('result_navigation', []).append({
                            'reward':reward,'button':str(button),
                            'button_text':flow.text([940,602,274,100])})
                        if button:
                            flow.tap_hit(button); flow.pause(2)
                            flow.snap()
                            Image.fromarray(flow.image[:,:,::-1]).save(args.output/'after_result_next.png')
                            continue
                    elif 'result_unknown_text' not in report:
                        report['result_unknown_text'] = flow.text([100,420,145,43])
                    if flow.hit_text([680,314,170,160], 'GREAT|GOOD|BAD|MISS'):
                        def number(rect):
                            value = flow.text(rect).strip().replace(' ', '')
                            if not value.isdecimal():
                                raise ValueError(f'Uncertain result number: {value!r}')
                            return int(value)
                        values = {key:number([850,y,80,35]) for key,y in
                                  zip(('perfect','great','good','bad','miss'),(277,316,356,394,432))}
                        values['max_combo'] = number([1032,369,82,40])
                        values['score'] = number([1000,188,177,46])
                        if sum(values[k] for k in ('perfect','great','good','bad','miss')) == selected['notes'] and values == previous:
                            report['judgment'] = values
                            report['status'] = ('verified_full_combo_great_or_better'
                                                if values['max_combo'] == selected['notes'] and
                                                not any(values[k] for k in ('good','bad','miss'))
                                                else 'verified_below_target')
                            Image.fromarray(flow.image[:,:,::-1]).save(args.output/'judgment.png')
                            print('Game result:', values, flush=True)
                            return True
                        previous = values
                    flow.pause(.5)
                raise RuntimeError('Result page not verified before timeout')
            except Exception as exc:
                report['result_error'] = repr(exc)
                print('Result remains unverified:', repr(exc), flush=True)
                return False
    resource.register_custom_action('ChartProbeResult', ReadResult())
    tasker.post_task('ChartProbeResult', {'ChartProbeResult': {
        'action':'Custom','custom_action':'ChartProbeResult'}}).wait()
    (args.output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    return 0 if report['status'] == 'verified_full_combo_great_or_better' else 2


if __name__ == '__main__':
    sys.exit(main())
