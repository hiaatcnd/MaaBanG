"""Compare separate and combined room-error guards on existing screenshots.

Uses the production model, ROI and thresholds. Each variant is replayed in
alternating order after warm-up. No real controller is constructed, no game
input is permitted, and private screenshots remain at their existing paths.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))


def separate_guards(context, recognize):
    """Former inactive snap's fatal check, then the caller's disconnect check."""
    for name in ('OL_Fatal', 'OL_Disconnected'):
        result = recognize(name)
        if result:
            return name, str(result.best_result.text)
    return None


def replay(manifest, resource_path, repeats, progress_path):
    import numpy as np
    from PIL import Image
    from maa.controller import CustomController
    from maa.custom_action import CustomAction
    from maa.resource import Resource
    from maa.tasker import Tasker
    from online_errors import room_error

    started_at = datetime.now(timezone.utc).isoformat()
    cases = manifest['cases']
    frames, provenance = {}, {}
    for case in cases:
        path = Path(case['image']).resolve(strict=True)
        frame = np.asarray(Image.open(path).convert('RGB'))[:, :, ::-1].copy()
        if frame.shape != (720, 1280, 3):
            raise ValueError(f'{path}: expected a 1280x720 screenshot')
        frames[case['id']] = frame
        provenance[case['id']] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                  'expected_category': case['expected_category'], 'origin': case['origin']}

    class Screens(CustomController):
        def connect(self): return True
        def request_uuid(self): return 'room-guards-offline-benchmark'
        def screencap(self): return next(iter(frames.values())).copy()
        def forbidden(self, *args): raise AssertionError('Offline guard replay cannot send input')
        click = swipe = touch_down = touch_move = touch_up = key_down = key_up = forbidden
        start_app = stop_app = forbidden

    resource, controller, tasker = Resource(), Screens(), Tasker()
    if not resource.use_cpu():
        raise RuntimeError('Unable to select CPU inference')
    if not resource.post_bundle(resource_path).wait().succeeded:
        raise RuntimeError('Unable to load local resource')
    if not controller.post_connection().wait().succeeded or not tasker.bind(resource, controller):
        raise RuntimeError('Unable to bind the offline-only controller')
    rows, errors, nodes = [], [], {}

    class Replay(CustomAction):
        def run(self, context, argv):
            try:
                nodes.update({name: context.get_node_data(name) for name in ('OL_Fatal', 'OL_Disconnected')})
                for name, node in nodes.items():
                    if node['recognition']['type'] != 'OCR':
                        raise AssertionError(f'{name}: this benchmark requires production OCR nodes')
                for case_index, case in enumerate(cases):
                    frame = frames[case['id']]
                    for repeat in range(-1, repeats):
                        # -1 warms both branches. Measured order is AB / BA / AB.
                        variants = ('separate', 'combined') if repeat % 2 == 0 else ('combined', 'separate')
                        for variant in variants:
                            progress_path.write_text(json.dumps({'case': case['id'], 'repeat': repeat,
                                                                 'variant': variant}), encoding='utf8')
                            calls = []
                            def recognize(name, **override):
                                started = time.perf_counter()
                                result = context.run_recognition(name, frame, {name: override} if override else None)
                                elapsed = (time.perf_counter() - started) * 1000
                                if result is None:
                                    raise RuntimeError(f'Recognition did not execute: {name}')
                                calls.append({'node': name, 'override': override, 'milliseconds': elapsed,
                                              'hit': result.hit})
                                return result if result.hit else None
                            started = time.perf_counter()
                            result = (separate_guards if variant == 'separate' else room_error)(context, recognize)
                            elapsed = (time.perf_counter() - started) * 1000
                            if repeat >= 0:
                                actual = result[0] if result else None
                                rows.append({'case': case['id'], 'repeat': repeat, 'variant': variant,
                                    'milliseconds': elapsed, 'ocr_calls': len(calls), 'calls': calls,
                                    'result': result, 'correct_category': actual == case['expected_category'],
                                    'false_negative': case['expected_category'] is not None and actual is None,
                                    'false_positive': case['expected_category'] is None and actual is not None})
                        if repeat >= 0:
                            print(f'case={case_index + 1}/{len(cases)} {case["id"]} '
                                  f'repeat={repeat + 1}/{repeats}', file=sys.stderr, flush=True)
                return True
            except Exception as exc:
                errors.append(repr(exc))
                return False

    resource.register_custom_action('OnlineGuardsBenchmark', Replay())
    job = tasker.post_task('OnlineGuardsBenchmark', {'OnlineGuardsBenchmark': {
        'action': 'Custom', 'custom_action': 'OnlineGuardsBenchmark'}}).wait()
    if not job.succeeded:
        raise RuntimeError(errors)

    summary = []
    mismatches = []
    for case in cases:
        item = {'case': case['id'], **provenance[case['id']]}
        matching = [row for row in rows if row['case'] == case['id']]
        for variant in ('separate', 'combined'):
            samples = [row for row in matching if row['variant'] == variant]
            item[variant] = {'median_ms': statistics.median(row['milliseconds'] for row in samples),
                             'ocr_calls': sorted(set(row['ocr_calls'] for row in samples)),
                             'all_categories_correct': all(row['correct_category'] for row in samples),
                             'false_negatives': sum(row['false_negative'] for row in samples),
                             'false_positives': sum(row['false_positive'] for row in samples)}
        for repeat in range(repeats):
            pair = {row['variant']: row['result'] for row in matching if row['repeat'] == repeat}
            if pair['separate'] != pair['combined']:
                mismatches.append({'case': case['id'], 'repeat': repeat, **pair})
        item['saved_median_ms'] = item['separate']['median_ms'] - item['combined']['median_ms']
        summary.append(item)
    totals = {variant: statistics.median(sum(row['milliseconds'] for row in rows
                                            if row['variant'] == variant and row['repeat'] == repeat)
                                         for repeat in range(repeats))
              for variant in ('separate', 'combined')}
    totals['saved_ms'] = totals['separate'] - totals['combined']
    totals['saved_percent'] = 100 * (1 - totals['combined'] / totals['separate'])
    models = {name: {'path': str(resource_path / 'model/ocr' / name),
                    'sha256': hashlib.sha256((resource_path / 'model/ocr' / name).read_bytes()).hexdigest()}
              for name in ('det.onnx', 'rec.onnx', 'keys.txt')}
    progress_path.write_text('{"stage":"complete"}', encoding='utf8')
    return {'scope': 'Offline MaaFramework CPU, existing real screenshots, same model/ROI/threshold. '
                     'One full warm-up per case, then alternating separate/combined order. '
                     'Framework-call timings exclude real Agent IPC, game waits and input. '
                     'Fatal-positive maintenance/account-restriction real screenshots are unavailable; '
                     'these results do not prove coverage of every possible error.',
            'repeats': repeats, 'case_count': len(cases), 'model_files': models, 'nodes': nodes,
            'framework_version': importlib.metadata.version('MaaFw'),
            'started_at_utc': started_at, 'completed_at_utc': datetime.now(timezone.utc).isoformat(),
            'environment': {'device': 'CPU', 'child_priority': 'BelowNormal on Windows',
                            'background_applications': 'Not stopped; absolute time may include contention'},
            'implementation_sha256': hashlib.sha256((ROOT / 'agent/online_errors.py').read_bytes()).hexdigest(),
            'equivalent_results': not mismatches, 'mismatches': mismatches,
            'all_expected_categories_correct': all(row['correct_category'] for row in rows),
            'summary': summary, 'totals_median_ms': totals, 'samples': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resource-path', type=Path, default=ROOT / 'assets/resource')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error('--repeats must be at least 3 for alternating-order comparisons')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.child:
        report = replay(json.loads(args.cases.read_text(encoding='utf8')),
                        args.resource_path.resolve(), args.repeats, args.output.with_suffix('.progress.json'))
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
        print(json.dumps({'totals': report['totals_median_ms'],
                          'equivalent': report['equivalent_results'],
                          'all_expected_categories_correct': report['all_expected_categories_correct']},
                         ensure_ascii=False), flush=True)
        return
    command = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--child',
               '--cases', str(args.cases.resolve()), '--output', str(args.output.resolve()),
               '--resource-path', str(args.resource_path.resolve()), '--repeats', str(args.repeats)]
    process = subprocess.Popen(command, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) |
                               getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0))
    try:
        status = process.wait(timeout=300)
    except BaseException:
        if process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, timeout=30,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            else:
                process.kill()
            process.wait(timeout=30)
        raise
    if status:
        raise SystemExit(status)


if __name__ == '__main__':
    main()
