"""Compare local OCR models on reviewed fixture checks, without game input.

Each model runs in a separate process; the production resource is never changed.
Timings cover uncached framework recognition calls on fixed images, with CPU
inference and one complete warm-up pass before measured repetitions.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
MODELS = {
    'v6_small': 'ppocr_v6/small',
    'v5_mobile': 'ppocr_v5/zh_cn',
    'v6_medium': 'ppocr_v6/medium',
    'v6_tiny': 'ppocr_v6/tiny',
    'v4_zh_cn': 'ppocr_v4/zh_cn',
    'v5_server': 'ppocr_v5/zh_cn-server',
    'v4_det_v6_rec': {
        'det.onnx': 'ppocr_v4/zh_cn/det.onnx',
        'rec.onnx': 'ppocr_v6/small/rec.onnx',
        'keys.txt': 'ppocr_v6/small/keys.txt',
    },
}


def model_sources(name):
    """Keep recognition weights and their ordered dictionary paired explicitly."""
    specification = MODELS[name]
    root = ROOT / 'assets/MaaCommonAssets/OCR'
    return {filename: root / (specification[filename] if isinstance(specification, dict)
                             else f'{specification}/{filename}')
            for filename in ('det.onnx', 'rec.onnx', 'keys.txt')}


def model_metadata(name):
    return {filename: {'bytes': source.stat().st_size,
                       'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                       'source': source.relative_to(ROOT).as_posix()}
            for filename, source in model_sources(name).items()}


def run_model(name, cases, repeats, progress_path):
    import numpy as np
    from PIL import Image
    from maa.controller import CustomController
    from maa.custom_action import CustomAction
    from maa.resource import Resource
    from maa.tasker import Tasker
    from costume_unlock import normalized
    from online_policy import final_song
    from song_catalog import RECOGNITION_BY_ID

    def progress(stage, **detail):
        progress_path.write_text(json.dumps({'model': name, 'stage': stage, **detail}), encoding='utf-8')

    progress('loading_images')

    def key(text):
        return normalized(text).casefold()

    def judge(case, hits):
        texts = [h.text for h in hits]
        text = ' '.join(texts)
        target = case.get('target')
        kind = case['kind']
        if kind == 'absent':
            return not hits
        if kind == 'song':
            try:
                return final_song(text)['id'] == str(target)
            except ValueError:
                return False
        if kind == 'text':
            return key(text) == key(str(target))
        if kind == 'set':
            return {key(t) for t in target} <= {key(t) for t in texts}
        raise ValueError(f'Unknown check kind: {kind}')

    images = {c['image']: np.asarray(Image.open(ROOT / 'tests/fixtures' / c['image']).convert('RGB'))[:, :, ::-1].copy()
              for c in cases}
    sources = model_sources(name)
    metadata = model_metadata(name)
    observed = []
    errors = []
    current = next(iter(images.values()))

    class Screens(CustomController):
        def connect(self): return True
        def request_uuid(self): return 'ocr-model-comparison-offline'
        def screencap(self): return current
        def click(self, x, y): raise AssertionError('No device input is allowed')

    with tempfile.TemporaryDirectory(prefix='maabang-ocr-model-') as folder:
        overlay = Path(folder)
        destination = overlay / 'model/ocr/benchmark'
        destination.mkdir(parents=True)
        for filename in metadata:
            shutil.copy2(sources[filename], destination / filename)
        resource = Resource()
        if not resource.use_cpu():
            raise RuntimeError('Unable to select CPU inference')
        for bundle in (ROOT / 'assets/resource', overlay):
            progress('loading_resource', bundle=str(bundle))
            if not resource.post_bundle(bundle).wait().succeeded:
                raise RuntimeError(f'Unable to load resource: {bundle}')
        controller = Screens()
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError('Unable to initialize offline controller')
        tasker = Tasker()
        if not tasker.bind(resource, controller):
            raise RuntimeError('Unable to bind offline tasker')

        class Replay(CustomAction):
            def run(self, context, argv):
                try:
                    for repeat in range(-1, repeats):
                        for case in cases:
                            node = case.get('node', 'CU_OCR')
                            params = {'model': 'benchmark'}
                            for field in ('roi', 'expected', 'only_rec', 'threshold'):
                                if field in case:
                                    params[field] = case[field]
                            if node == 'CU_OCR':
                                params.setdefault('expected', '.+')
                            progress('recognizing', repeat=repeat, case=case['id'])
                            started = time.perf_counter()
                            result = context.run_recognition(node, images[case['image']], {node: params})
                            elapsed = (time.perf_counter() - started) * 1000
                            hits = list(result.filtered_results) if result is not None else []
                            if result is None:
                                raise RuntimeError(f'Recognition did not start: {case["id"]}')
                            if repeat >= 0:
                                text = ' '.join(h.text for h in hits)
                                literal_target = (RECOGNITION_BY_ID[str(case['target'])]['title']
                                                  if case['kind'] == 'song' else case.get('target'))
                                observed.append({'id': case['id'], 'kind': case['kind'], 'repeat': repeat,
                                    'milliseconds': elapsed, 'correct': judge(case, hits), 'text': text,
                                    'literal_equal': key(text) == key(str(literal_target))
                                                     if case['kind'] in ('song', 'text') else None,
                                    'hits': [{'text': h.text, 'score': h.score, 'box': h.box} for h in hits]})
                        progress('repeat_complete', repeat=repeat)
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action('CompareOCRModels', Replay())
        job = tasker.post_task('CompareOCRModels', {'CompareOCRModels': {
            'action': 'Custom', 'custom_action': 'CompareOCRModels'}}).wait()
        if not job.succeeded:
            raise RuntimeError(errors)

    by_case = defaultdict(list)
    for row in observed:
        by_case[row['id']].append(row)
    summary = {'model': name, 'case_count': len(cases),
               'cases_correct_every_repeat': sum(all(r['correct'] for r in rs) for rs in by_case.values()),
               'median_total_ms': statistics.median(sum(r['milliseconds'] for r in observed if r['repeat'] == i)
                                                     for i in range(repeats)),
               'model_megabytes': sum(m['bytes'] for m in metadata.values()) / 1e6,
               'failed_cases': [cid for cid, rs in by_case.items() if not all(r['correct'] for r in rs)],
               'by_kind': {}}
    for kind in dict.fromkeys(c['kind'] for c in cases):
        matching = [c for c in cases if c['kind'] == kind]
        summary['by_kind'][kind] = {'total': len(matching), 'correct_every_repeat':
            sum(all(r['correct'] for r in by_case[c['id']]) for c in matching)}
    progress('complete')
    return {'summary': summary, 'models': metadata, 'samples': observed}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', choices=list(MODELS), default=list(MODELS))
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--cases', type=Path, default=ROOT / 'tools/ocr_benchmark_cases.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--child', choices=list(MODELS))
    parser.add_argument('--inspect-only', action='store_true',
                        help='Report model file sources and hashes without loading OCR or images')
    parser.add_argument('--low-priority', action='store_true',
                        help='Run owned Windows probe processes below normal priority')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    manifest = json.loads(args.cases.read_text(encoding='utf-8'))
    cases = manifest['cases'] if isinstance(manifest, dict) else manifest
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.inspect_only:
        report = {'ocr_executed': False, 'case_count': len(cases),
                  'models': {name: model_metadata(name) for name in args.models}}
        print(json.dumps(report, ensure_ascii=False), flush=True)
    elif args.child:
        import faulthandler
        faulthandler.dump_traceback_later(90, repeat=True)
        report = run_model(args.child, cases, args.repeats, args.output.with_suffix('.progress.json'))
        faulthandler.cancel_dump_traceback_later()
    else:
        reports = []
        for model in args.models:
            output = args.output.with_name(args.output.stem + '-' + model + '.json')
            process = subprocess.Popen([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()),
                '--child', model, '--cases', str(args.cases.resolve()), '--repeats', str(args.repeats),
                '--output', str(output.resolve())], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) |
                (getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0) if args.low_priority else 0))

            def stop_probe():
                if process.poll() is None:
                    if os.name == 'nt':
                        # Windows venv python.exe can launch the real interpreter
                        # as a child. Stop this owned probe tree, not only its stub.
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                            capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    else:
                        process.kill()

            try:
                stdout, stderr = process.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                stop_probe()
                stdout, stderr = process.communicate(timeout=30)
                reports.append({'summary': {'model': model, 'error': 'offline replay exceeded 300 seconds'}})
            except BaseException:
                stop_probe()
                process.communicate(timeout=30)
                raise
            else:
                if process.returncode:
                    reports.append({'summary': {'model': model, 'error': stdout + stderr,
                                                'exit_code': process.returncode}})
                else:
                    reports.append(json.loads(output.read_text(encoding='utf-8')))
            print(json.dumps(reports[-1]['summary'], ensure_ascii=False), flush=True)
        report = {'scope': 'MaaFramework 5.12.3 CPU, fixed real fixture ROIs; framework call timings, '
                           'warm models, no result cache; case pass rate is not general OCR accuracy',
                  'repeats': args.repeats, 'cases': cases, 'results': reports}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
