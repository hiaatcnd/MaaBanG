"""Dedicated in-memory Bestdori session. Never logs or persists credentials."""
import copy
import http.cookiejar
import json
from urllib.error import URLError
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler

from bestdori_catalog import atomic_json
from bestdori_profiles import ProfileError, export_profile, fingerprint


class CloudError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CloudError('账号接口发生跳转，已停止请求，请检查网站状态')


class BestdoriClient:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(NoRedirect(), HTTPCookieProcessor(self.cookies))
        self.username = None

    def request(self, endpoint, data=None):
        if endpoint not in ('user/login', 'user/logout', 'user/me', 'user/profiles'):
            raise CloudError('不支持的账号操作')
        raw = None if data is None else json.dumps(data).encode()
        req = Request('https://bestdori.com/api/'+endpoint, data=raw,
                      headers={'Content-Type': 'application/json', 'User-Agent': 'MaaBanG/BestdoriProfiles',
                               'Origin': 'https://bestdori.com', 'Referer': 'https://bestdori.com/profile/manager'})
        try:
            with self.opener.open(req, timeout=20) as response:
                body = response.read(8*1024*1024+1)
            if len(body) > 8*1024*1024:
                raise CloudError('云端档案响应过大')
            result = json.loads(body)
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise CloudError('Bestdori 请求未确认完成，请检查网络后重试') from exc
        if not isinstance(result, dict) or result.get('result') is not True:
            code = result.get('code') if isinstance(result, dict) else None
            messages = {'LOGIN_REQUIRED': '登录已失效，请重新登录',
                        'CREDENTIALS_INVALID': '用户名或密码不正确',
                        'BLOCKED': '尝试次数过多，请稍后再试',
                        'PROFILES_INVALID': 'Bestdori 未接受档案格式，请保留导出文件'}
            if code == 'LOGIN_REQUIRED':
                self.username = None
            raise CloudError(messages.get(code, 'Bestdori 未接受本次操作'))
        return result

    def login(self, username, password):
        if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
            raise CloudError('请输入用户名和密码')
        self.cookies.clear()
        self.username = None
        self.request('user/login', {'username': username, 'password': password})
        me = self.request('user/me')
        if not isinstance(me.get('username'), str):
            raise CloudError('登录后无法确认账号')
        self.username = me['username']
        return self.username

    def logout(self):
        try:
            if self.username:
                self.request('user/logout', {})
        finally:
            self.cookies.clear()
            self.username = None

    def profiles(self):
        response = self.request('user/profiles')
        # Missing is not empty: a changed response schema must never erase data.
        if 'profiles' not in response:
            raise CloudError('云端档案列表无法确认，已禁止写入')
        profiles = response['profiles']
        # The official retrieve action explicitly handles a null collection.
        if profiles is None:
            return []
        if not isinstance(profiles, list) or any(not isinstance(p, dict) for p in profiles):
            raise CloudError('云端档案列表无法确认，已禁止写入')
        return profiles

    def append(self, profile, backup_path):
        if not self.username:
            raise CloudError('请先登录 Bestdori')
        account = self.username
        encoded = json.loads(export_profile(profile, compressed=True))
        original = self.profiles()
        identical = [p for p in original if p.get('name') == encoded['name']]
        if identical:
            if len(identical) == 1 and identical[0] == encoded:
                return {'already_saved': True, 'name': encoded['name']}
            raise CloudError('云端已有同名档案，请更换新档案名称')
        desired = copy.deepcopy(original) + [encoded]
        return self._save(original, desired, backup_path, account, encoded['name'])

    def replace(self, profile, backup_path, *, index, key, account):
        if not self.username or self.username != account:
            raise CloudError('账号已变化，请重新选择云端底稿')
        encoded = json.loads(export_profile(profile, compressed=True))
        original = self.profiles()
        if type(index) is not int or not 0 <= index < len(original) or fingerprint(original[index]) != key:
            raise CloudError('云端底稿已变化或被移动，请重新读取底稿并合并')
        if original[index] == encoded:
            return {'already_saved': True, 'name': encoded['name']}
        if any(i != index and p.get('name') == encoded['name'] for i, p in enumerate(original)):
            raise CloudError('另一个云端档案已使用此名称，请修改名称后重新预览')
        desired = copy.deepcopy(original)
        desired[index] = encoded
        return self._save(original, desired, backup_path, account, encoded['name'])

    def _save(self, original, desired, backup_path, account, name):
        atomic_json(backup_path, {'account': account, 'profiles': original})
        if fingerprint(self.profiles()) != fingerprint(original):
            raise CloudError('云端档案发生变化，请重新读取底稿并合并')
        if self.username != account:
            raise CloudError('账号已变化，请重新选择云端底稿')
        write_error = None
        try:
            self.request('user/profiles', {'profiles': desired})
        except CloudError as exc:
            write_error = exc
        # A timed-out POST can already be committed. Read first; never replay it.
        try:
            actual = self.profiles()
        except CloudError as exc:
            raise CloudError('保存结果尚未确认；请重新读取云端，不要重复保存') from exc
        if fingerprint(actual) != fingerprint(desired):
            raise CloudError('云端回读与预期不符；已保留备份，请检查后再操作') from write_error
        return {'already_saved': False, 'name': name}
