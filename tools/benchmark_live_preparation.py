"""Replay online preparation with local OCR; never connect to a game/device.

Compare redundant captures/recognition with frame reuse, holding fixtures,
identity checks, room guards and the two-sample stability rule constant.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker
from chart_policy import ChartOptions
from online_live import OnlineLiveFlow
from online_policy import RoomClock


def benchmark(repeats=3):
    def load(name):
        return np.asarray(Image.open(ROOT / 'tests/fixtures' / name).convert('RGB'))[:, :, ::-1].copy()

    fixtures = {'coop': load('coop/ready.png'), 'versus': load('versus/ready_effects_off.png')}
    covered = fixtures['versus'].copy()
    loading = load('versus/song_loading.png')
    covered[600:700, 870:1255] = loading[600:700, 870:1255]
    fixtures['versus_loading'] = covered
    captures = 0
    current = fixtures['coop']

    class ReplayController(CustomController):
        def connect(self): return True
        def request_uuid(self): return 'preparation-benchmark-offline'
        def screencap(self):
            nonlocal captures
            captures += 1
            return current
        def click(self, x, y):
            raise AssertionError('Read-only preparation benchmark must not send input')

    class CountingContext:
        def __init__(self, context):
            self.context = context
            self.calls = 0
            self.timings = []
        def __getattr__(self, name): return getattr(self.context, name)
        def run_recognition(self, *args, **kwargs):
            self.calls += 1
            started = time.perf_counter()
            result = self.context.run_recognition(*args, **kwargs)
            elapsed = (time.perf_counter() - started) * 1000
            algorithm = getattr(result, 'algorithm', 'unavailable')
            algorithm = getattr(algorithm, 'value', algorithm)
            override = args[2] if len(args) > 2 else kwargs.get('pipeline_override')
            params = (override or {}).get(args[0], {})
            self.timings.append({'node': args[0], 'algorithm': algorithm,
                'roi': params.get('roi'), 'only_rec': params.get('only_rec', False),
                'milliseconds': elapsed})
            return result

    resource = Resource()
    if not resource.post_bundle(ROOT / 'assets/resource').wait().succeeded:
        raise RuntimeError('Local OCR models/resources are required')
    controller = ReplayController()
    if not controller.post_connection().wait().succeeded:
        raise RuntimeError('Unable to initialize fixture controller')
    tasker = Tasker()
    if not tasker.bind(resource, controller):
        raise RuntimeError('Unable to bind fixture resources')
    rows = []
    errors = []

    with tempfile.TemporaryDirectory() as folder:
        class Replay(CustomAction):
            def run(self, context, argv):
                nonlocal current
                try:
                    # Warm up the same model before measuring either path.
                    context.run_recognition('OL_CoopFinalConfirm', current)
                    for fixture, frame in fixtures.items():
                        current = frame
                        mode = fixture.split('_')[0]
                        outcomes = []
                        for repeat in range(repeats):
                            # Alternate order to reduce warm-up/order bias.
                            for reuse in ((False, True) if repeat % 2 == 0 else (True, False)):
                                counted = CountingContext(context)
                                flow = OnlineLiveFlow(counted, ChartOptions.parse({'mode': mode, 'fire': 0}), folder)
                                flow.room_active = True
                                flow.room_clock = RoomClock(time.monotonic())
                                flow.foreground_package = lambda: 'com.bilibili.star.bili'
                                flow.state = lambda value: None
                                if not reuse:
                                    recognize = flow.reco
                                    def uncached(*args, **kwargs):
                                        flow.invalidate_frame_recognition()
                                        return recognize(*args, **kwargs)
                                    flow.reco = uncached
                                first_capture = captures
                                started = time.perf_counter()
                                flow.await_final()
                                selection = flow.read_final_selection(fresh=not reuse)
                                balance = flow.verify_chart_start(1, selection, 0)
                                effects = flow.disable_mv(fresh=False)
                                outcomes.append((asdict(selection), balance, asdict(effects)))
                                ocr = [r for r in counted.timings if r['algorithm'] == 'OCR']
                                templates = [r for r in counted.timings if r['algorithm'] == 'TemplateMatch']
                                rows.append({'fixture': fixture, 'reuse': reuse, 'repeat': repeat,
                                    'captures': captures - first_capture, 'recognitions': counted.calls,
                                    'milliseconds': round((time.perf_counter() - started) * 1000, 2),
                                    'ocr_calls': len(ocr),
                                    'ocr_milliseconds': sum(r['milliseconds'] for r in ocr),
                                    'template_milliseconds': sum(r['milliseconds'] for r in templates),
                                    'recognition_timings': counted.timings})
                        if any(outcome != outcomes[0] for outcome in outcomes):
                            raise AssertionError(f'{fixture}: reuse changed verified selection, balance or effects')
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action('PreparationBenchmark', Replay())
        job = tasker.post_task('PreparationBenchmark', {'PreparationBenchmark': {
            'action': 'Custom', 'custom_action': 'PreparationBenchmark'}}).wait()
        if not job.succeeded:
            raise RuntimeError(errors)
    summaries = []
    for fixture in fixtures:
        row = {'fixture': fixture}
        for reuse, label in ((False, 'baseline'), (True, 'optimized')):
            samples = [r for r in rows if r['fixture'] == fixture and r['reuse'] == reuse]
            row[label] = {key: statistics.median(r[key] for r in samples)
                          for key in ('captures', 'recognitions', 'milliseconds', 'ocr_calls',
                                      'ocr_milliseconds', 'template_milliseconds')}
        summaries.append(row)
    return {'scope': 'offline fixed screenshots, local OCR; no worker startup, network or device latency; '
                     'OCR timings include the full framework call and result construction, not only model inference',
            'repeats': repeats, 'summary': summaries, 'samples': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    result = benchmark(args.repeats)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
