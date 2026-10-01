"""Exercise an actual packaged-runtime upgrade in an isolated directory (no game)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile


def extract(archive, destination):
    with zipfile.ZipFile(archive) as source:
        assert source.testzip() is None
        source.extractall(destination)
    roots = list(destination.iterdir())
    assert len(roots) == 1 and (roots[0] / 'MaaBanG.exe').is_file()
    return roots[0]


def verify(package, work, old_package=None):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    target = extract(old_package or package, work / 'installed')
    incoming = extract(package, work / 'incoming')
    shared = work / 'user-data'
    (shared / 'config/instances').mkdir(parents=True)
    (shared / 'cache/charts').mkdir(parents=True)
    (shared / 'config/config.json').write_text('{"EnableAutoUpdateResource":true}')
    (shared / 'config/instances/default.json').write_text('{"id":"update-test","tasks":[]}')
    (shared / 'cache/charts/1_easy.json').write_text('[{"type":"Single"}]')
    def snapshot():
        return {p.relative_to(shared).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in shared.rglob('*') if p.is_file()}
    before = snapshot()
    obsolete = target / 'app/old-dependency-sentinel.txt'
    obsolete.write_text('must disappear from new installation')
    updater = work / 'MaaBanGUpdater.exe'
    shutil.copy2(incoming / 'app/tools/MaaBanGUpdater.exe', updater)
    shutil.copy2(incoming / 'app/tools/MaaBanGUpdater.exe.config', str(updater) + '.config')
    env = dict(os.environ, MAABANG_DATA_DIR=str(shared))
    prepared = subprocess.run([str(updater), '--prepare', str(Path(package).resolve()), str(target)],
                              env=env, capture_output=True, encoding='utf-8-sig')
    (work / 'prepare-output.txt').write_text(prepared.stdout + prepared.stderr, encoding='utf-8')
    prepared.check_returncode()
    stage = Path(prepared.stdout.strip())
    assert obsolete.is_file()
    applied = subprocess.run([str(stage / 'MaaBanGUpdater.exe'), '--apply', str(stage), '0', '0', '--no-restart'],
                             env=env, capture_output=True, encoding='utf-8-sig', timeout=180)
    (work / 'updater-output.txt').write_text(applied.stdout + applied.stderr, encoding='utf-8')
    applied.check_returncode()
    manifest = json.loads((incoming / 'package-manifest.json').read_text(encoding='utf-8'))
    for relative, digest in manifest['files'].items():
        assert hashlib.sha256((target / relative).read_bytes()).hexdigest() == digest, relative
    assert not obsolete.exists()
    assert (stage / 'previous/app/old-dependency-sentinel.txt').is_file()
    assert snapshot() == before
    assert (stage / 'status.txt').read_text(encoding='utf-8-sig') == 'complete'
    result = {'version': manifest['version'], 'verified_files': len(manifest['files']),
              'shared_data_unchanged': True, 'obsolete_removed': True, 'backup_retained': True,
              'packaged_agent_smoke': 'passed', 'target': str(target), 'stage': str(stage)}
    (work / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--old-package', type=Path)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    verify(args.package, args.work, args.old_package)
