"""Private desktop service and credential-free bridge to the game Agent."""
import json
import re
import secrets
import sys
import threading
import time
from pathlib import Path

from bestdori_catalog import atomic_json, validate
from bestdori_portal import Workspace, make_server
from bestdori_profiles import ProfileError, validate_scope, validate_maximize
from user_data import data_root


def job_directory(job_id, root=None):
    if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ProfileError('扫描会话无效，请从工具页重新开始')
    return (Path(root) if root else data_root()/'bestdori')/'jobs'/job_id


class DesktopWorkspace(Workspace):
    def __init__(self, root=None, client=None):
        super().__init__(root, client)
        self.scan_id = None

    def state(self):
        with self.lock:
            if self.busy and self.scan_id:
                try:
                    progress = json.loads((job_directory(self.scan_id, self.root)/'progress.json').read_text(encoding='utf-8'))
                    self.message = progress['message']
                except (OSError, ValueError, KeyError):
                    pass
            return super().state()

    def cancel_scan(self):
        self.cancel.set()
        if self.scan_id:
            (job_directory(self.scan_id, self.root)/'cancel').touch()

    def execute(self, action, body):
        if action in ('cancel', 'close'):
            self.cancel_scan()
        if action == 'scan_finish':
            if not isinstance(body, dict) or body.get('scan_id') != self.scan_id or not self.busy:
                raise ProfileError('扫描会话已经变化')
            folder = job_directory(self.scan_id, self.root)
            with self.lock:
                # Called only after the native task has actually stopped. A failed
                # connection must release the workspace; partial scans stay useful.
                try:
                    report = json.loads((folder/'scan.json').read_text(encoding='utf-8'))
                    self.observations = report['observations']
                    for row in self.observations:
                        row['original'] = {k: row[k] for k in ('level','skill','master','train') if type(row.get(k)) is int}
                    self.cached_scan_id = self.scan_id
                    self.cache_recovery = None
                    self.scan_errors = report['errors']
                    self.scan_status = report['status'] if report['status'] == 'complete' else 'partial'
                except (OSError, ValueError, KeyError):
                    self.scan_status = 'partial'
                    self.scan_errors = ['未读到游戏资料，请检查设备连接和游戏页面后重试']
                if body.get('error'):
                    self.scan_status = 'partial'
                    self.scan_errors.append(str(body['error']))
                self.busy = False
                self.message = '列表扫描完成' if self.scan_status == 'complete' else '扫描未全部完成，已保留读到的记录'
                self.invalidate()
                request = json.loads((folder/'request.json').read_text(encoding='utf8'))
                self.cache_scan(request['scope'])
            (folder/'finished').touch()
            return {'message': self.message}
        result = super().execute(action, body)
        if action == 'scan':
            try:
                job = self.jobs.get_nowait()
                with self.lock:
                    self.scan_id = secrets.token_hex(16)
                    folder = job_directory(self.scan_id, self.root)
                    atomic_json(folder/'request.json', {**job, 'created': time.time(), 'catalog': self.catalog})
                return {**result, 'scan_id': self.scan_id}
            except Exception:
                self.busy = False
                raise
        return result


def run_scan(context, job_id, root=None):
    """The Agent receives only a one-use job ID, never login/session secrets."""
    folder = job_directory(job_id, root)
    try:
        request = json.loads((folder/'request.json').read_text(encoding='utf-8'))
        age = time.time() - request['created']
        if not 0 <= age <= 600 or (folder/'finished').exists():
            raise ValueError('expired')
        catalog = validate(request['catalog'])
        scope = validate_scope(request['scope'], catalog)
        maximize = validate_maximize(request['maximize'])
        # A replay cannot start a second scan, even if the first one failed.
        with (folder/'claimed').open('x', encoding='utf-8') as marker:
            marker.write('claimed')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ProfileError('扫描请求已失效，请从工具页重新开始') from exc
    from bestdori_scan import BestdoriScan
    class Cancel:
        def is_set(self):
            return (folder/'cancel').exists()
    if Cancel().is_set():
        report = {'status': 'partial', 'observations': [], 'errors': ['已取消扫描']}
        atomic_json(folder/'scan.json', report)
        return report
    return BestdoriScan(context, catalog, scope, folder, cancel=Cancel(), maximize=maximize,
            progress=lambda message: atomic_json(folder/'progress.json', {'message': message})).run()


def serve_desktop():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    workspace = DesktopWorkspace()
    server, url = make_server(workspace)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    def parent_closed():
        # EOF on the private pipe also handles application crashes. Cancel only
        # our scan and allow any already-started cloud save/readback to finish.
        sys.stdin.read()
        workspace.cancel_scan()
        workspace.closed.set()
    threading.Thread(target=parent_closed, daemon=True).start()
    try:
        # Consumed by redirected stdout, never a console or task log.
        print(json.dumps({'url': url}), flush=True)
        workspace.closed.wait()
    finally:
        workspace.cancel_scan()
        server.shutdown()
        with workspace.operation:
            workspace.client.cookies.clear()
            workspace.client.username = None
        server.server_close()
        worker.join(timeout=2)


if __name__ == '__main__':
    serve_desktop()
