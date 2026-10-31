"""Profile workspace and authenticated loopback API for the native desktop UI."""
import copy
import hmac
import json
import queue
import secrets
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bestdori_catalog import load, refresh, atomic_json, validate
from bestdori_client import BestdoriClient, CloudError
from bestdori_media import CardMedia
from bestdori_profiles import (ProfileError, empty_profile, parse_profile, export_profile,
                              merge_profile, fingerprint, validate_scope, validate_maximize, in_scope, MAX_BYTES)
from user_data import data_root


class Workspace:
    def __init__(self, root=None, client=None):
        self.root = Path(root) if root else data_root()/'bestdori'
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog = load()
        try:
            self.catalog = validate(json.loads((self.root/'catalog.json').read_text(encoding='utf-8')))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.base = empty_profile()
        self.observations = []
        self.result = None
        self.result_token = None
        self.cloud = []
        self.cloud_account = None
        self.cloud_target = None
        self.client = client or BestdoriClient()
        self.media = CardMedia(self.root/'public-cache', self.catalog)
        self.closed = threading.Event()
        self.cancel = threading.Event()
        self.jobs = queue.Queue()
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.busy = False
        self.message = '导入已有资料或从空白档案开始。'
        self.scan_status = None
        self.scan_errors = []
        self.scan_summary = {}
        self.cached_at = None
        self.cached_scope = None
        self.cached_scan_id = None
        self.cache_recovery = None
        try:
            saved = json.loads((self.root/'last-scan.json').read_text(encoding='utf8'))
            if not isinstance(saved,dict):
                raise ValueError('invalid scan cache')
            if isinstance(saved.get('observations'),list):
                self.observations = saved['observations']
                self.scan_status = saved.get('status','partial')
                self.scan_errors = saved.get('errors',[])
                self.scan_summary = saved.get('summary', {})
                self.cached_at = saved.get('saved_at')
                self.cached_scope = saved.get('scope')
                self.cached_scan_id = saved.get('scan_id')
                if saved.get('version', 0) < 2:
                    self.recover_originals()
        except (OSError,ValueError,TypeError):
            # Recover the last finished scan produced by older previews too.
            reports = sorted((self.root/'jobs').glob('*/scan.json'),key=lambda p:p.stat().st_mtime,reverse=True)
            for path in reports:
                if not (path.parent/'finished').is_file():
                    continue
                try:
                    saved=json.loads(path.read_text(encoding='utf8'))
                    request=json.loads((path.parent/'request.json').read_text(encoding='utf8'))
                    if not isinstance(saved.get('observations'),list):
                        continue
                    self.observations=saved['observations']
                    for row in self.observations:
                        row['original'] = {k: row[k] for k in ('level','skill','master','train') if type(row.get(k)) is int}
                        if row.get('id') is not None:
                            row['maximize']=request.get('maximize',{})
                    self.scan_status=saved.get('status','partial')
                    self.scan_errors=saved.get('errors',[])
                    self.scan_summary=saved.get('summary', {})
                    self.cached_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds')
                    self.cached_scope=request.get('scope')
                    self.cached_scan_id=path.parent.name
                    break
                except (OSError,ValueError,TypeError,KeyError):
                    continue
        self.run_id = datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)

    def recover_originals(self):
        """Migrate legacy review caches only against the matching latest scan.

        Old UI max flags describe adjustments, not which fields the scan read.
        Never reconstruct readings from the adjusted values or from a base profile.
        """
        reports = sorted((self.root/'jobs').glob('*/scan.json'), key=lambda p:p.stat().st_mtime, reverse=True)
        path = next((p for p in reports if (p.parent/'finished').is_file()), None)
        if path is None:
            return
        try:
            report = json.loads(path.read_text(encoding='utf8'))
            request = json.loads((path.parent/'request.json').read_text(encoding='utf8'))
            rows = report['observations']
            raw = {r['id']: r for r in rows if type(r.get('id')) is int}
            ids = [r.get('id') for r in self.observations]
            if (not ids or len(set(ids)) != len(ids) or len(raw) != len(rows)
                    or set(ids) != set(raw)
                    or validate_scope(request['scope'], self.catalog) != validate_scope(self.cached_scope, self.catalog)
                    or not self.cached_at or datetime.fromisoformat(self.cached_at).timestamp() < request['created']):
                return
            fields = ('level','skill','master','train')
            for row in self.observations:
                original = row.get('original', {})
                if not isinstance(original, dict):
                    return
            count = 0
            for row in self.observations:
                original = row.setdefault('original', {})
                for field in fields:
                    if type(raw[row['id']].get(field)) is int and original.get(field) != raw[row['id']][field]:
                        # Older clients also called already-edited values originals.
                        # Only the matching raw report is authoritative; edits stay in row[field].
                        original[field] = raw[row['id']][field]
                        # Preserve edits; only repair missing targets from the old cache.
                        if field not in row:
                            row[field] = original[field]
                        count += 1
            self.cached_scan_id = path.parent.name
            if count:
                self.cache_recovery = {'scan_id': self.cached_scan_id, 'fields': count}
        except (OSError, ValueError, TypeError, KeyError):
            return

    def state(self):
        with self.lock:
            return copy.deepcopy({'base_name': self.base['name'], 'base_count': len(self.base['cards']),
                    'base_cards': self.base['cards'],
                    'server': self.base['server'], 'username': self.client.username,
                    'cloud_target': self.cloud_target,
                    'observations': self.observations, 'result': self.result,
                    'result_token': self.result_token, 'busy': self.busy,
                    'message': self.message, 'scan_status': self.scan_status, 'scan_errors': self.scan_errors,
                    'scan_summary': self.scan_summary,
                    'cached_at':self.cached_at,'cached_scope':self.cached_scope,
                    'cache_recovery':self.cache_recovery})

    def cache_scan(self, scope=None):
        self.cached_at = datetime.now().isoformat(timespec='seconds')
        self.cached_scope = scope or self.cached_scope
        if self.cache_recovery and (self.root/'last-scan.json').is_file():
            backup = self.root/'backups'/('review-before-recovery-'+self.run_id+'.json')
            if not backup.exists():
                atomic_json(backup, json.loads((self.root/'last-scan.json').read_text(encoding='utf8')))
        atomic_json(self.root/'last-scan.json',{'version':2,'scan_id':self.cached_scan_id,'observations':self.observations,'status':self.scan_status,
            'errors':self.scan_errors,'saved_at':self.cached_at,'scope':self.cached_scope,'summary':self.scan_summary})

    def invalidate(self):
        self.result = self.result_token = None

    def execute(self, action, body):
        if not isinstance(body, dict):
            raise ProfileError('请求格式无效')
        if action == 'card':
            return self.media.get(body.get('id'), body.get('trained', True), body.get('both',False) is True)
        if action == 'cancel':
            self.cancel.set()
            return {'message': '正在停止扫描，已读取的记录将保留'}
        if not self.operation.acquire(blocking=False):
            raise ProfileError('上一项操作尚未完成，请稍候')
        try:
            if self.closed.is_set():
                raise ProfileError('管理会话已经结束，请重新打开工具')
            if action == 'close':
                self.cancel.set()
                self.closed.set()
                return {'message': '管理会话已结束'}
            with self.lock:
                if self.busy:
                    raise ProfileError('扫描进行中，请先完成或取消扫描')
            if action == 'import':
                base = parse_profile(body.get('text', ''))
                with self.lock:
                    self.base = base
                    self.cloud_target = None
                    self.invalidate()
                atomic_json(self.root/'snapshots'/self.run_id/'base.json', base)
                return {'message': f'已读取底稿：{base["name"]}，{len(base["cards"])} 张卡牌'}
            if action == 'empty':
                with self.lock:
                    self.base = empty_profile()
                    self.cloud_target = None
                    self.invalidate()
                return {'message': '已切换为空白中国服档案'}
            if action == 'refresh':
                catalog = refresh()
                with self.lock:
                    self.catalog = catalog
                    self.media.catalog = catalog
                    self.invalidate()
                atomic_json(self.root/'catalog.json', catalog)
                return {'message': '目录更新完成', 'catalog': catalog}
            if action == 'cache_review':
                rows = body.get('observations')
                if not isinstance(rows,list) or len(rows)>10000 or any(not isinstance(r,dict) for r in rows):
                    raise ProfileError('缓存记录无效')
                with self.lock:
                    self.observations = rows
                    self.cache_scan(validate_scope(body.get('scope'),self.catalog))
                return {'message':'核对记录已缓存'}
            if action == 'scan':
                if self.base['server'] != 3:
                    raise ProfileError('游戏扫描仅支持中国服，请更换中国服底稿')
                scope = validate_scope(body.get('scope'), self.catalog)
                maximize = validate_maximize(body.get('maximize'))
                skip_existing = body.get('skip_existing', False)
                if type(skip_existing) is not bool:
                    raise ProfileError('跳过已存在选项无效')
                existing_ids = sorted({card['id'] for card in self.base['cards']
                                       if in_scope(card['id'], scope, self.catalog)}) if skip_existing else []
                with self.lock:
                    self.busy = True
                    self.cancel.clear()
                    self.invalidate()
                    self.observations = []
                    self.scan_status = 'running'
                    self.scan_errors = []
                    self.scan_summary = {}
                    self.message = '正在读取游戏成员列表……'
                    self.jobs.put({'scope': scope, 'maximize': maximize, 'existing_ids': existing_ids})
                return {'message': '扫描已开始'}
            if action == 'preview':
                rows = body.get('observations')
                if not isinstance(rows, list):
                    raise ProfileError('扫描记录无效')
                result = merge_profile(self.base, rows, body.get('scope'), body.get('policy'),
                                       self.catalog, name=body.get('name'), maximize=body.get('maximize'))
                with self.lock:
                    self.result = result
                    self.result_token = fingerprint(result)
                atomic_json(self.root/'snapshots'/self.run_id/'merged.json', result)
                return {'message': f'新增 {result["added"]} 张，更新 {result["updated"]} 张，待处理 {len(result["skipped"])} 张'}
            if action in ('export', 'upload', 'replace'):
                if not self.result or body.get('token') != self.result_token:
                    raise ProfileError('请先生成并查看当前设置的合并预览')
                profile = copy.deepcopy(self.result['profile'])
                if action == 'export':
                    text = export_profile(profile)
                    atomic_json(self.root/'exports'/(self.run_id+'.json'), profile)
                    return {'text': text, 'filename': 'MaaBanG-'+self.run_id+'.json',
                            'message': '资料已导出；尚未保存到 Bestdori 云端'}
                backup = self.root/'backups'/(self.run_id+'-'+secrets.token_hex(6)+'.json')
                if action == 'replace':
                    target = self.cloud_target
                    if not target or body.get('target') != target:
                        raise ProfileError('请先选择云端档案作为底稿，再合并并替换该档案')
                    try:
                        value = self.client.replace(profile, backup, index=target['index'],
                                                    key=target['key'], account=target['account'])
                    finally:
                        # A timeout may have committed. A new attempt must start
                        # from a fresh cloud base, not repeat the old write.
                        self.cloud_target = None
                        self.cloud = []
                        self.cloud_account = None
                    return {**value, 'message': '已回读确认替换云端档案：'+value['name']}
                value = self.client.append(profile, backup)
                return {**value, 'message': '已回读确认云端新档案：'+value['name']}
            if action == 'login':
                self.cloud = []
                self.cloud_account = self.cloud_target = None
                username = self.client.login(body.get('username'), body.get('password'))
                return {'message': '已登录：'+username}
            if action == 'logout':
                self.cloud = []
                self.cloud_account = self.cloud_target = None
                self.client.logout()
                return {'message': '已退出登录'}
            if action == 'cloud':
                self.cloud = []
                self.cloud_account = None
                self.cloud = self.client.profiles()
                self.cloud_account = self.client.username
                return {'profiles': [{'name': p.get('name', '未命名档案'), 'key': fingerprint(p), 'index': i}
                                     for i, p in enumerate(self.cloud)], 'message': '已读取云端档案'}
            if action == 'cloud_base':
                if not self.cloud_account or self.cloud_account != self.client.username:
                    raise ProfileError('请登录并重新读取云端档案列表')
                index = body.get('index')
                if type(index) is not int or not 0 <= index < len(self.cloud):
                    raise ProfileError('请重新读取云端档案列表')
                source = self.cloud[index]
                if body.get('key') != fingerprint(source):
                    raise ProfileError('所选档案已变化，请重新选择')
                base = parse_profile(source)
                with self.lock:
                    self.base = base
                    self.cloud_target = {'index': index, 'key': fingerprint(source),
                                         'account': self.cloud_account, 'name': base['name']}
                    self.invalidate()
                atomic_json(self.root/'snapshots'/self.run_id/'base.json', base)
                return {'message': '已使用云端档案作为底稿：'+base['name']}
            raise ProfileError('未知操作')
        finally:
            self.operation.release()

    def process_scan(self, context, job):
        try:
            if context is None:
                raise ProfileError('未连接游戏，请从 MaaBanG 工具页开始扫描')
            from bestdori_scan import BestdoriScan
            def progress(message):
                with self.lock:
                    self.message = message
            report = BestdoriScan(context, self.catalog, job['scope'],
                         self.root/'snapshots'/self.run_id/'scan', cancel=self.cancel, progress=progress,
                         maximize=job['maximize'], existing_ids=job.get('existing_ids', []),
                         portraits_root=self.root/'public-cache').run()
            with self.lock:
                self.observations = report['observations']
                self.scan_status = report['status']
                self.scan_errors = report['errors']
                self.scan_summary = report.get('summary', {})
                self.message = ('列表扫描完成' if report['status'] == 'complete' else '扫描未全部完成，已保留读到的记录')
                self.cache_scan(job['scope'])
        except Exception as exc:
            with self.lock:
                self.scan_status = 'partial'
                self.scan_errors = [str(exc)]
                self.message = '扫描未完成'
        finally:
            with self.lock:
                self.busy = False


def make_server(workspace, port=0):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never record credentials, session tokens or imported content.

        def send(self, code, value, content_type='application/json; charset=utf-8'):
            raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            return (self.headers.get('Host') == origin.removeprefix('http://')
                    and self.headers.get('Origin', origin) == origin
                    and self.headers.get('Sec-Fetch-Site', 'same-origin') in ('same-origin', 'none')
                    and hmac.compare_digest(self.headers.get('X-MaaBanG-Token', ''), token))

        def do_GET(self):
            if not self.authorized():
                return self.send(403, {'error': '会话无效，请从 MaaBanG 重新打开工具'})
            if self.path == '/api/state':
                return self.send(200, workspace.state())
            if self.path == '/api/catalog':
                return self.send(200, workspace.catalog)
            return self.send(404, {'error': '页面不存在'})

        def do_POST(self):
            if not self.authorized():
                return self.send(403, {'error': '请求来源或会话无效'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send(415, {'error': '需要 JSON 请求'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_BYTES:
                    return self.send(413, {'error': '请求大小无效或超过 4 MB'})
                self.connection.settimeout(10)
                body = json.loads(self.rfile.read(size))
                result = workspace.execute(self.path.removeprefix('/api/'), body)
                self.send(200, result)
            except (ProfileError, CloudError) as exc:
                self.send(400, {'error': str(exc)})
            except (ValueError, TypeError, KeyError):
                self.send(400, {'error': '数据格式无效，请检查输入'})
            except Exception:
                self.send(500, {'error': '操作未完成，请保留资料并重试'})
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server, f'http://127.0.0.1:{server.server_port}/#{token}'
