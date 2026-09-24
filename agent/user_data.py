"""Version-independent user data and non-destructive migration from portable packages."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time


def data_root():
    override = os.environ.get('MAABANG_DATA_DIR')
    return Path(override).expanduser().resolve() if override else Path.home() / '.maabang'


def legacy_apps(app):
    """Prefer the launching package, then nearby MaaBanG packages by config recency."""
    app = Path(app).resolve()
    siblings = []
    if app.name == 'app' and app.parent.name.startswith('MaaBanG-'):
        for package in app.parent.parent.glob('MaaBanG-*'):
            candidate = package / 'app'
            if candidate != app and (candidate / 'interface.json').is_file() and (candidate / 'agent').is_dir():
                siblings.append(candidate)
    def modified(candidate):
        return max((p.stat().st_mtime for p in (candidate / 'config').rglob('*') if p.is_file()), default=0)
    return [app, *sorted(siblings, key=modified, reverse=True)]


@contextmanager
def migration_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.migration.lock').open('a+b') as lock:
        lock.seek(0, os.SEEK_END)
        if not lock.tell():
            lock.write(b'0')
            lock.flush()
        deadline = time.monotonic() + 60
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('用户数据迁移正被另一启动器使用，请稍后重试')
                time.sleep(.1)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def atomic_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.tmp', delete=False) as file:
        temporary = Path(file.name)
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_legacy(app, root=None):
    root = Path(root) if root is not None else data_root()
    sources = legacy_apps(app)
    report = {'config_source': None, 'charts_imported': 0, 'invalid_charts': 0}
    with migration_lock(root):
        config = root / 'config'
        # A coherent snapshot preserves instance IDs, task options and schedules.
        # Once any shared configuration exists it is authoritative, even when
        # launching an older package later. Never merge settings from old copies.
        if not config.exists() or not any(config.iterdir()):
            source = next((p for p in sources if (p / 'config/config.json').is_file()), None)
            if source:
                with tempfile.TemporaryDirectory(dir=root, prefix='.config-import-') as staging:
                    staged = Path(staging) / 'config'
                    shutil.copytree(source / 'config', staged)
                    for file in staged.rglob('*.json'):
                        json.loads(file.read_text(encoding='utf-8-sig'))
                    settings = source / 'appsettings.json'
                    if settings.is_file() and not (root / 'appsettings.json').exists():
                        json.loads(settings.read_text(encoding='utf-8-sig'))
                        atomic_copy(settings, root / 'appsettings.json')
                    if config.exists():
                        config.rmdir()  # Empty only; never remove existing settings.
                    staged.replace(config)
                report['config_source'] = str(source)
        cache = root / 'cache/charts'
        cache.mkdir(parents=True, exist_ok=True)
        for source in sources:
            for file in (source / 'cache/charts').glob('*.json'):
                if not re.fullmatch(r'\d+_(easy|normal|hard|expert|special)\.json', file.name):
                    continue
                destination = cache / file.name
                if destination.exists():
                    continue
                try:
                    chart = json.loads(file.read_bytes())
                    if not isinstance(chart, list) or not chart or not all(isinstance(n, dict) for n in chart):
                        raise ValueError('invalid chart')
                except (OSError, ValueError):
                    report['invalid_charts'] += 1
                    continue
                atomic_copy(file, destination)
                report['charts_imported'] += 1
        # Keep a small audit trail without copying private configuration contents.
        with (root / 'migration.jsonl').open('a', encoding='utf8') as log:
            log.write(json.dumps({'app': str(Path(app).resolve()), **report}, ensure_ascii=False) + '\n')
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--migrate', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate_legacy(args.migrate), ensure_ascii=False))
