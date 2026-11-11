"""Replay the ONLY_UNFC worker-preparation branch with existing ready screenshots.

This calls the real play_chart flow with an immediately armed, inert worker.
The baseline retains the former two fresh FC captures on each check. The
candidate uses current production code, including its one-use selection proof.
No device is connected, no subprocess is launched, and all inputs are rejected.
Use --inspect-only to validate screenshots without loading OCR or running it.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))

from chart_policy import ChartOptions
from full_combo import ONLY_UNFC, ready_star_row
from live_policy import DIFFICULTIES
from online_live import OnlineLiveFlow
from online_policy import RoomClock

SOURCE_HASHES = {name: hashlib.sha256((ROOT / 'agent' / name).read_bytes()).hexdigest()
                 for name in ('online_live.py', 'chart_live.py', 'frame_recognition.py')}


def load_frame(path):
    path = path.resolve(strict=True)
    frame = np.asarray(Image.open(path).convert('RGB'))[:, :, ::-1].copy()
    if frame.shape != (720, 1280, 3):
        raise ValueError(f'{path}: requires an existing 1280x720 screenshot')
    return frame, {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                   'star_row': ready_star_row(frame)}


def historical_fc_read(flow, selection):
    """Frozen former FC loop, ignoring any production selection proof."""
    previous = None
    for attempt in range(4):
        flow.snap()
        stars = ready_star_row(flow.image)
        if stars is not None:
            first, states = stars
            rect = [220, 541, 570, 38] if first == 317 else [111, 541, 420, 38]
            if not flow.title_matches(flow.text(rect), selection.song):
                stars = None
            elif stars == previous:
                return states[DIFFICULTIES.index(selection.difficulty)]
        previous = stars
        if attempt < 3:
            flow.pause(.12)
    return 'unknown'


def benchmark(cases, *, repeats=3, device='cpu',
              resource_path=None, mode='coop', difficulty='expert', fire=0):
    from maa.controller import CustomController
    from maa.custom_action import CustomAction
    from maa.resource import Resource
    from maa.tasker import Tasker

    loaded = []
    for final_path, ready_path in cases:
        final_frame, final_meta = load_frame(final_path)
        ready_frame, ready_meta = load_frame(ready_path)
        loaded.append((final_frame, ready_frame, {'final': final_meta, 'ready': ready_meta}))
    current = loaded[0][0]
    captures = 0

    class ReplayController(CustomController):
        def connect(self): return True
        def request_uuid(self): return 'only-unfc-preparation-offline'
        def screencap(self):
            nonlocal captures
            captures += 1
            return current.copy()
        def forbidden(self, *args):
            raise AssertionError('Read-only benchmark cannot send game inputs')
        click = swipe = touch_down = touch_move = touch_up = key_down = key_up = forbidden
        start_app = stop_app = forbidden

    class CountingContext:
        def __init__(self, context):
            self.context = context
            self.timings = []
            self.phase = 'setup'
        def __getattr__(self, name): return getattr(self.context, name)
        def run_recognition(self, entry, image, pipeline_override=None):
            started = time.perf_counter()
            result = self.context.run_recognition(entry, image, pipeline_override)
            elapsed = (time.perf_counter() - started) * 1000
            algorithm = getattr(result, 'algorithm', 'unavailable')
            algorithm = getattr(algorithm, 'value', algorithm)
            params = (pipeline_override or {}).get(entry, {})
            self.timings.append({'phase': self.phase, 'node': entry, 'algorithm': algorithm,
                                 'roi': params.get('roi'), 'milliseconds': elapsed})
            return result

    resource, controller, tasker = Resource(), ReplayController(), Tasker()
    if device == 'cpu' and not resource.use_cpu():
        raise RuntimeError('Unable to select CPU inference')
    if not resource.post_bundle(resource_path or ROOT / 'assets/resource').wait().succeeded:
        raise RuntimeError('Local OCR models/resources are required')
    if not controller.post_connection().wait().succeeded or not tasker.bind(resource, controller):
        raise RuntimeError('Unable to initialize offline fixture controller')
    rows, errors = [], []

    with tempfile.TemporaryDirectory(prefix='only-unfc-preparation-') as folder:
        class Replay(CustomAction):
            def run(self, context, argv):
                nonlocal current
                try:
                    for case_index, (final_frame, ready_frame, provenance) in enumerate(loaded):
                        current = final_frame
                        # Do not charge the first model initialization to one
                        # variant's selection/end-to-end interval.
                        context.run_recognition('OL_Fatal', current)
                        outcomes = []
                        for repeat in range(repeats):
                            variants = ('baseline', 'production') if repeat % 2 == 0 else ('production', 'baseline')
                            for variant in variants:
                                current = final_frame
                                counted = CountingContext(context)
                                destination = Path(folder) / f'{case_index}-{repeat}-{variant}'
                                flow = OnlineLiveFlow(counted, ChartOptions.parse({
                                    'mode': mode, 'difficulty1': difficulty, 'fire': fire,
                                    'avoid_full_combo': ONLY_UNFC}), destination)
                                flow.room_active = True
                                flow.room_clock = RoomClock(time.monotonic())
                                flow.report['attempts'] = 1
                                flow.current_attempt = {'attempt': 1}
                                flow.foreground_package = lambda: 'com.bilibili.star.bili'
                                flow.state = lambda value: None
                                # Selection/warm-up precede the measured play_chart boundary,
                                # just as they do in the live run. No chart download is replayed.
                                end_to_end_started = time.perf_counter()
                                selection_first_capture = captures
                                flow.snap()
                                selection = flow.read_final_selection(fresh=False)
                                selection_milliseconds = (time.perf_counter() - end_to_end_started) * 1000
                                selection_captures = captures - selection_first_capture
                                selection_calls = counted.timings[:]
                                counted.timings.clear()
                                fc_checks = []
                                worker_armed = False
                                original_fc = flow.read_full_combo_state

                                def read_fc(owner, chosen, **kwargs):
                                    counted.phase = 'armed_fc' if worker_armed else 'initial_fc'
                                    before = captures
                                    if variant == 'baseline':
                                        state = historical_fc_read(owner, chosen)
                                    else:
                                        state = original_fc(chosen, **kwargs)
                                    fc_checks.append({'phase': counted.phase, 'state': state,
                                                      'captures': captures - before})
                                    return state

                                flow.read_full_combo_state = MethodType(read_fc, flow)
                                original_verify = flow.verify_chart_start
                                def verify(*args, **kwargs):
                                    counted.phase = 'armed_identity_and_cost'
                                    return original_verify(*args, **kwargs)
                                flow.verify_chart_start = verify
                                original_effects = flow.disable_mv
                                def effects(*args, **kwargs):
                                    counted.phase = 'effects'
                                    return original_effects(*args, **kwargs)
                                flow.disable_mv = effects
                                configurations = []
                                def worker(command, **kwargs):
                                    nonlocal current, worker_armed
                                    config_path = Path(command[-1])
                                    configurations.append(json.loads(config_path.read_text(encoding='utf8')))
                                    current = ready_frame
                                    worker_armed = True
                                    (config_path.parent / 'armed').write_text('ready', encoding='utf8')
                                    (config_path.parent / 'playback.json').write_text(
                                        '{"status":"input_complete"}', encoding='utf8')
                                    return SimpleNamespace(poll=lambda: 0, returncode=0)
                                # The target interval ends at worker authorization, before
                                # submitting the online Ready button. Verify the marker only.
                                def submitted(chosen):
                                    if not (destination / 'attempt1_playback/start').is_file():
                                        raise AssertionError('Worker authorization marker missing')
                                flow.submit_online_ready = submitted
                                row = {}
                                before = captures
                                started = time.perf_counter()
                                with patch('chart_live.subprocess.Popen', side_effect=worker):
                                    flow.play_chart(1, selection, {'duration': 1}, fire, row)
                                elapsed = (time.perf_counter() - started) * 1000
                                end_to_end_elapsed = (time.perf_counter() - end_to_end_started) * 1000
                                if len(configurations) != 1:
                                    raise AssertionError('Expected exactly one inert worker')
                                config = configurations[0]
                                outcome = (asdict(selection), row['full_combo_check'], config['avoid_full_combo'],
                                           row.get('fire_before'), row['status'])
                                outcomes.append(outcome)
                                ocr = [call for call in counted.timings if call['algorithm'] == 'OCR']
                                templates = [call for call in counted.timings if call['algorithm'] == 'TemplateMatch']
                                rows.append({'case': case_index, 'fixtures': provenance, 'repeat': repeat,
                                    'variant': variant, 'selection': asdict(selection),
                                    'captures': captures - before, 'fc_checks': fc_checks,
                                    'guard_ocr_calls': sum(call['node'] == 'OL_Fatal' for call in ocr),
                                    'recognitions': len(counted.timings), 'ocr_calls': len(ocr),
                                    'ocr_milliseconds': sum(call['milliseconds'] for call in ocr),
                                    'template_milliseconds': sum(call['milliseconds'] for call in templates),
                                    'milliseconds': elapsed, 'startup_timings_ms': row['startup_timings_ms'],
                                    'selection_milliseconds': selection_milliseconds,
                                    'selection_captures': selection_captures,
                                    'selection_ocr_calls': sum(call['algorithm'] == 'OCR' for call in selection_calls),
                                    'selection_recognition_timings': selection_calls,
                                    'end_to_end_milliseconds': end_to_end_elapsed,
                                    'end_to_end_captures': captures - selection_first_capture,
                                    'end_to_end_ocr_calls': len(ocr) + sum(call['algorithm'] == 'OCR' for call in selection_calls),
                                    'avoid_full_combo': config['avoid_full_combo'],
                                    'recognition_timings': counted.timings})
                                print(f'case={case_index + 1}/{len(loaded)} repeat={repeat + 1}/{repeats} '
                                      f'{variant}: preparation={elapsed:.1f}ms '
                                      f'captures={captures - before} ocr={len(ocr)}',
                                      file=sys.stderr, flush=True)
                        if any(outcome != outcomes[0] for outcome in outcomes):
                            raise AssertionError(f'Case {case_index}: candidate changed verified outcome')
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action('OnlyUnfcPreparationBenchmark', Replay())
        job = tasker.post_task('OnlyUnfcPreparationBenchmark', {'OnlyUnfcPreparationBenchmark': {
            'action': 'Custom', 'custom_action': 'OnlyUnfcPreparationBenchmark'}}).wait()
        if not job.succeeded:
            raise RuntimeError(errors)

    metrics = ('captures', 'guard_ocr_calls', 'recognitions', 'ocr_calls',
               'ocr_milliseconds', 'template_milliseconds', 'milliseconds',
               'selection_milliseconds', 'selection_captures', 'selection_ocr_calls',
               'end_to_end_milliseconds', 'end_to_end_captures', 'end_to_end_ocr_calls')
    summary = []
    for case_index in range(len(loaded)):
        item = {'case': case_index, 'fixtures': loaded[case_index][2]}
        for variant in ('baseline', 'production'):
            samples = [row for row in rows if row['case'] == case_index and row['variant'] == variant]
            item[variant] = {key: statistics.median(sample[key] for sample in samples) for key in metrics}
        summary.append(item)
    return {'scope': 'Real play_chart ONLY_UNFC branch; offline fixed screenshots and local Context. '
                     'Worker is inert and immediately armed; no device, network, Agent RPC or real worker startup. '
                     'OCR timing includes framework recognition and result construction, not pure model inference. '
                     'Do not interpret this local wall-clock saving as a measured live-game improvement.',
            'candidate': 'production', 'device': device, 'repeats': repeats,
            'source_sha256': SOURCE_HASHES, 'summary': summary, 'samples': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', nargs=2, type=Path, action='append', required=True,
                        metavar=('FINAL_SCREENSHOT', 'ARMED_READY_SCREENSHOT'))
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--device', choices=('cpu', 'auto'), default='cpu')
    parser.add_argument('--mode', choices=('coop', 'versus'), default='coop')
    parser.add_argument('--difficulty', choices=DIFFICULTIES, default='expert')
    parser.add_argument('--fire', type=int, choices=range(4), default=0)
    parser.add_argument('--resource-path', type=Path)
    parser.add_argument('--inspect-only', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    if args.inspect_only:
        result = {'ocr_executed': False, 'cases': [
            {'final': load_frame(final)[1], 'ready': load_frame(ready)[1]}
            for final, ready in args.case]}
    else:
        result = benchmark(args.case, repeats=args.repeats,
                           device=args.device, resource_path=args.resource_path, mode=args.mode,
                           difficulty=args.difficulty, fire=args.fire)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf8')
    print(rendered)


if __name__ == '__main__':
    main()
