"""Public card artwork and skill metadata, isolated from account sessions."""
import base64
import io
import json
import re
import threading
import time
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError

from bestdori_catalog import atomic_json
from bestdori_client import NoRedirect
from bestdori_profiles import ProfileError


def local_name(values):
    return next((values[i] for i in (3, 1, 0) if len(values) > i and values[i]), '')


def skill_descriptions(skill):
    """Match the official skillDescription filter at each actual skill level."""
    template = local_name(skill.get('description', []))
    descriptions = []
    for level in range(5):
        try:
            values = []
            once = skill.get('onceEffect', {})
            if once.get('onceEffectType') == 'life':
                values.append(once['onceEffectValue'][level])
            values.append(skill['duration'][level])
            text = template
            for i, value in enumerate(values):
                if type(value) not in (int, float):
                    raise ValueError('missing skill value')
                text = text.replace('{'+str(i)+'}', format(value, 'g'))
            descriptions.append(text if not re.search(r'\{\d+\}', text) else '')
        except (KeyError, IndexError, TypeError, ValueError):
            descriptions.append('')
    return descriptions


class CardMedia:
    def __init__(self, root, catalog):
        self.root = Path(root)
        self.catalog = catalog
        self.lock = threading.Lock()
        self.card_locks = {}
        self.opener = build_opener(NoRedirect())  # No account CookieJar or session headers.

    def fetch(self, path):
        request = Request('https://bestdori.com/'+path, headers={'User-Agent': 'MaaBanG/BestdoriProfiles'})
        for attempt in range(2):
            try:
                with self.opener.open(request, timeout=10) as response:
                    raw = response.read(6*1024*1024+1)
                break
            except HTTPError as exc:
                if exc.code not in (429,500,502,503,504) or attempt:
                    raise
                time.sleep(.3)
            except OSError:
                if attempt:
                    raise
                time.sleep(.3)
        if len(raw) > 6*1024*1024:
            raise ProfileError('卡牌资料超过大小限制')
        return raw

    def cached_json(self, name, endpoint):
        path = self.root/name
        try:
            value = json.loads(path.read_text(encoding='utf8'))
            if not isinstance(value,dict):
                raise ValueError('invalid cache')
            return value
        except (OSError, ValueError):
            value = json.loads(self.fetch(endpoint))
            if not isinstance(value,dict):
                raise ProfileError('卡牌资料格式无效')
            atomic_json(path, value)
            return value

    def get(self, id_, trained=True, both=False):
        if type(id_) is not int or str(id_) not in self.catalog['cards'] or type(trained) is not bool:
            raise ProfileError('请先确认卡牌身份')
        with self.lock:
            card_lock = self.card_locks.setdefault(id_,threading.Lock())
        with card_lock:
            detail = self.cached_json(f'cards/{id_}.json', f'api/cards/{id_}.json')
            meta = self.catalog['cards'][str(id_)]
            if any(detail.get(k) != meta[k] for k in ('characterId', 'rarity')):
                raise ProfileError('线上卡牌资料与目录不一致，请更新卡牌目录')
            try:
                with self.lock:
                    skill = self.cached_json('skills.json', 'api/skills/all.10.json').get(str(detail.get('skillId')), {})
            except (OSError, ValueError):
                skill = {}
            result = {'skill_name': local_name(detail.get('skillName', [])),
                      'skill_summary': local_name(skill.get('simpleDescription', [])),
                      'skill_descriptions': skill_descriptions(skill), 'image': None}
            resource = detail.get('resourceSetName', '')
            if not re.fullmatch(r'[A-Za-z0-9_]{1,80}', resource):
                raise ProfileError('卡面资源名称无效')
            # Kirafes cards have no extra training levels, but use the trained
            # thumbnail too. Training stat bonuses do not describe art variants.
            trained = trained and meta['rarity'] >= 3
            image_path = self.root/f'thumbnails/{id_}-{int(trained)}.png'
            try:
                result['image'] = base64.b64encode(self.thumbnail(image_path,id_,resource,trained)).decode('ascii')
            except (OSError, ValueError):
                result['image_error'] = '卡面暂时无法加载，可重试'
            result['images'] = []
            if both:
                variants = ((True,) if detail.get('type') == 'kirafes' else
                            (False,True) if meta['rarity'] >= 3 else (False,))
                for variant in variants:
                    if variant == trained:
                        image = result['image']
                    else:
                        try:
                            image = base64.b64encode(self.thumbnail(self.root/f'thumbnails/{id_}-{int(variant)}.png',id_,resource,variant)).decode('ascii')
                        except (OSError,ValueError):
                            image = None
                    if image:
                        result['images'].append({'trained':variant,'image':image})
                    elif detail.get('type') != 'kirafes':
                        result['image_error'] = '部分小图暂时无法加载，可重试'
            elif result['image']:
                result['images'].append({'trained':trained,'image':result['image']})
            if not result['image'] and result['images']:
                result['image'] = result['images'][-1]['image']
            return result

    def thumbnail(self,path,id_,resource,trained):
        from PIL import Image
        def valid(raw):
            with Image.open(io.BytesIO(raw)) as image:
                if image.size != (180,180):
                    raise ValueError('卡牌小图尺寸不匹配')
                image.load()
            return raw
        try:
            return valid(path.read_bytes())
        except (OSError,ValueError):
            pass
        art = 'after_training' if trained else 'normal'
        # Same thumbnail layout as Bestdori's public card list. Some animated
        # cards have no static full-card PNG; their thumbnails still exist.
        deadline = time.monotonic()+30
        for server in ('cn','jp','en','tw','kr'):
            if time.monotonic() > deadline:
                break
            try:
                raw = valid(self.fetch(f'assets/{server}/thumb/chara/card{id_//50:05d}_rip/{resource}_{art}.png'))
            except (OSError,ValueError):
                continue
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(raw); temporary.replace(path)
            return raw
        raise OSError('卡牌小图暂时不可用')
