"""Input timing and failure-safe teardown; no logging on the realtime path."""
import json
from pathlib import Path
import time


def timed_touch(controller, action, x, y, contact, record, clock=time.perf_counter):
    """Measure submission separately from completion, never retrying an input.

    A failed/raised submission may already have reached the backend. Keep its
    timing and propagate the failure so the caller stops and releases contacts.
    """
    started = clock()
    submitted = None
    record['ok'] = False
    try:
        if action == 'down':
            job = controller.post_touch_down(x, y, contact=contact)
        elif action == 'move':
            job = controller.post_touch_move(x, y, contact=contact)
        elif action == 'up':
            job = controller.post_touch_up(contact)
        else:
            raise ValueError(f'Unknown touch action: {action}')
        submitted = clock()
        record['ok'] = bool(job.wait().succeeded)
        return record['ok']
    finally:
        finished = clock()
        record['submit_ms'] = ((submitted if submitted is not None else finished)-started)*1000
        record['wait_ms'] = None if submitted is None else (finished-submitted)*1000
        record['api_ms'] = (finished-started)*1000


def finalize_playback(controller, active, report, report_path, *, allow_pause=False,
                      save_diagnostics=None, failure_status='error'):
    """Release each pending contact once and persist evidence even if cleanup fails.

    Callers remove an up from ``active`` before its first submission, so an
    uncertain up cannot be replayed here. An uncertain down/move may still need
    one release. A failed release forbids further navigation but does not prevent
    releasing other contacts or saving the original input failure and timings.
    """
    try:
        if controller is not None:
            for contact in sorted(active):
                active.discard(contact)
                measured={'action':'up','contact':contact}
                report.setdefault('cleanup_inputs',[]).append(measured)
                try:
                    if not timed_touch(controller,'up',0,0,contact,measured):
                        raise RuntimeError('Touch release was not confirmed')
                except Exception as exc:
                    report['input_failed']=True
                    report['status']=failure_status
                    report.setdefault('error','Cleanup input failed: '+str(exc))
                    report.setdefault('cleanup_errors',[]).append(
                        {'action':'up','contact':contact,'error':str(exc)})
            if allow_pause and not report.get('input_failed'):
                try:
                    if not controller.post_click(1240,50).wait().succeeded:
                        raise RuntimeError('Pause input was not confirmed')
                except Exception as exc:
                    report['input_failed']=True
                    report['status']=failure_status
                    report.setdefault('error','Cleanup input failed: '+str(exc))
                    report.setdefault('cleanup_errors',[]).append(
                        {'action':'pause','error':str(exc)})
        if save_diagnostics is not None:
            try:
                save_diagnostics()
            except Exception as exc:
                report.setdefault('diagnostic_errors',[]).append(str(exc))
    finally:
        Path(report_path).write_text(json.dumps(report,ensure_ascii=False),encoding='utf8')
