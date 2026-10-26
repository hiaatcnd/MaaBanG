"""Bestdori profile codecs and conservative, scoped card updates (no I/O)."""
import base64
import copy
import hashlib
import json
import struct

MAX_CARDS = 10000
MAX_BYTES = 4 * 1024 * 1024
GROUPS = {**dict.fromkeys(('PoppinParty', 'Afterglow', 'HelloHappyWorld',
          'PastelPalettes', 'Roselia', 'RaiseASuilen', 'Morfonica', 'MyGO', 'Everyone'), 7),
          'Magazine': 3, 'Plaza': 4, 'Menu': 4, 'potentials': 40}
FIELDS = {'level': (1, 100), 'skill': (0, 4), 'master': (0, 4),
          'ep': (0, 2), 'train': (0, 1), 'art': (0, 1)}
NUMERIC = ('level', 'skill', 'master')
ATTRIBUTES = ('powerful', 'cool', 'pure', 'happy')


class ProfileError(ValueError):
    pass


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ProfileError(f'{label} 必须是 {low}～{high} 的整数')
    return value


def empty_profile(name='MaaBanG', server=3):
    return {'name': name, 'server': server, 'cards': [],
            'items': {k: [1 if k == 'potentials' else None] * n for k, n in GROUPS.items()}}


def _rle_decode(data, count):
    if not isinstance(data, list) or len(data) % 2:
        raise ProfileError('档案压缩数组无效')
    result = []
    for i in range(0, len(data), 2):
        n = integer(data[i], 1, MAX_CARDS, '压缩长度')
        if len(result) + n > count:
            raise ProfileError('档案压缩长度与内容不符')
        result.extend([data[i+1]] * n)
    if len(result) != count:
        raise ProfileError('档案压缩数据不完整')
    return result


def _rle_encode(values):
    result = []
    for value in values:
        if result and type(result[-1]) is type(value) and result[-1] == value:
            result[-2] += 1
        else:
            result.extend([1, value])
    return result


def _decode_v2(data):
    try:
        cards = data['cards']
        if (set(data) - {'cards', 'items'} or
                set(cards) - {'ids', *(f+'s' for f in (*FIELDS, 'exclude'))} or
                set(data['items']) - GROUPS.keys()):
            raise ProfileError('压缩档案包含未知字段，无法保证完整保留')
        binary = base64.b64decode(cards['ids'], validate=True)
        if len(binary) % 2 or len(binary) > MAX_CARDS * 2:
            raise ProfileError('卡牌 ID 数据长度无效')
        ids = [v + 65536 if v > 24464 else v for (v,) in struct.iter_unpack('<H', binary)]
        arrays = {f: ([0]*len(ids) if f == 'master' and 'masters' not in cards else
                       _rle_decode(cards[f+'s'], len(ids)))
                  for f in (*FIELDS, 'exclude')}
        return {'cards': [{'id': id_, **{f: values[i] for f, values in arrays.items()}}
                          for i, id_ in enumerate(ids)],
                'items': {key: _rle_decode(data['items'][key], size)
                          for key, size in GROUPS.items() if key in data['items']}}
    except (KeyError, TypeError, struct.error, ValueError) as exc:
        raise ProfileError('无法读取 Bestdori 压缩档案') from exc


def _decode_v1(data):
    alphabet = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_'
    if not isinstance(data, str) or len(data) % 5 or len(data) > MAX_CARDS * 5:
        raise ProfileError('旧版档案长度无效')
    def number(s):
        result = 0
        for ch in s:
            if ch not in alphabet:
                raise ProfileError('旧版档案包含无效字符')
            result = result * 64 + alphabet.index(ch)
        return result
    cards = []
    for i in range(0, len(data), 5):
        id_ = number(data[i:i+2])
        bits = number(data[i+3:i+5])
        card = {'id': id_ + 6928 if id_ >= 3072 else id_, 'level': number(data[i+2]),
                'exclude': bool(bits % 2), 'art': (bits // 2) % 2,
                'train': (bits // 4) % 2, 'ep': (bits // 8) % 3,
                'skill': bits // 24, 'master': 0}
        cards.append(card)
    return {'cards': cards}


def parse_profile(value):
    if isinstance(value, str):
        if len(value.encode('utf-8')) > MAX_BYTES:
            raise ProfileError('档案超过 4 MB')
        try:
            value = json.loads(value)
        except (ValueError, RecursionError) as exc:
            raise ProfileError('请粘贴 Bestdori 导出的完整 JSON 资料') from exc
    if not isinstance(value, dict):
        raise ProfileError('需要一个用户档案，不能传入整个档案列表')
    value = copy.deepcopy(value)
    compression = value.pop('compression', None)
    if compression is not None:
        data = value.pop('data', None)
        if compression == '2':
            value.update(_decode_v2(data))
        elif compression == '1':
            value.update(_decode_v1(data))
        else:
            raise ProfileError('不支持此档案版本，请在 Bestdori 重新导出')
    if 'cards' not in value or not isinstance(value['cards'], list):
        raise ProfileError('档案缺少 cards 数组')
    result = empty_profile()
    result.update(value)
    if not isinstance(result['name'], str) or not 1 <= len(result['name']) <= 200:
        raise ProfileError('档案名称须为 1～200 个字符')
    integer(result['server'], 0, 4, '服务器')
    if len(result['cards']) > MAX_CARDS:
        raise ProfileError('档案卡牌数量异常')
    cards = []
    for source in result['cards']:
        if not isinstance(source, dict):
            raise ProfileError('卡牌记录无效')
        card = {'level': 1, 'skill': 0, 'master': 0, 'ep': 0,
                'train': 0, 'art': 0, 'exclude': False, **source}
        integer(card.get('id'), 1, 999999, '卡牌 ID')
        for f, (low, high) in FIELDS.items():
            integer(card[f], low, high, f)
        if type(card['exclude']) is not bool:
            raise ProfileError('exclude 必须是布尔值')
        cards.append(card)
    result['cards'] = cards
    if not isinstance(result['items'], dict):
        raise ProfileError('区域道具数据无效')
    items = empty_profile()['items']
    items.update(result['items'])
    for key, size in GROUPS.items():
        values = items[key]
        if not isinstance(values, list) or len(values) != size:
            raise ProfileError(f'{key} 数组长度应为 {size}')
        for v in values:
            if v is None and key != 'potentials':
                continue
            integer(v, 1 if key == 'potentials' else 0, 1000, key)
    result['items'] = items
    return result


def export_profile(profile, *, compressed=False):
    profile = parse_profile(profile)
    if not compressed:
        return json.dumps(profile, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    # The cloud website currently stores this exact compressed representation.
    # Never silently discard extensions introduced by a future schema.
    if (set(profile) - {'name', 'server', 'cards', 'items'} or
            set(profile['items']) - GROUPS.keys() or
            any(set(c) - {'id', 'exclude', *FIELDS} for c in profile['cards'])):
        raise ProfileError('档案含有扩展字段，请使用文件导出以完整保留，暂不上传')
    ids = []
    for card in profile['cards']:
        id_ = card['id']
        if not (1 <= id_ <= 24464 or 90001 <= id_ <= 131071):
            raise ProfileError('卡牌 ID 超出 Bestdori 当前云端压缩格式范围')
        ids.append(id_ & 65535)
    data = {'cards': {'ids': base64.b64encode(struct.pack('<'+'H'*len(ids), *ids)).decode()},
            'items': {key: _rle_encode(values) for key, values in profile['items'].items()}}
    data['cards'].update({f+'s': _rle_encode([c[f] for c in profile['cards']])
                          for f in (*FIELDS, 'exclude')})
    return json.dumps({'name': profile['name'], 'server': profile['server'],
                       'compression': '2', 'data': data}, ensure_ascii=False, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_scope(scope, catalog):
    if not isinstance(scope, dict):
        raise ProfileError('请选择更新范围')
    result = {}
    for key, valid in (('bands', set(map(int, catalog['bands']))),
                       ('members', set(map(int, catalog['characters']))),
                       ('rarities', set(range(1, 6)))):
        values = scope.get(key)
        if not isinstance(values, list) or not values or any(type(v) is not int or v not in valid for v in values):
            raise ProfileError('乐队、成员和星级都至少选择一项')
        result[key] = sorted(set(values))
    # Profiles and scan caches created before attribute selection covered all kinds.
    attributes = scope.get('attributes', list(ATTRIBUTES))
    if (not isinstance(attributes, list) or not attributes
            or any(not isinstance(v, str) or v not in ATTRIBUTES for v in attributes)):
        raise ProfileError('种类至少选择一项，且必须是 Powerful、Cool、Pure 或 Happy')
    result['attributes'] = [value for value in ATTRIBUTES if value in attributes]
    if not any(c['bandId'] in result['bands'] and int(id_) in result['members']
               for id_, c in catalog['characters'].items()):
        raise ProfileError('所选乐队和成员没有交集')
    return result


def in_scope(id_, scope, catalog):
    card = catalog['cards'].get(str(id_))
    if not card:
        return False
    character = catalog['characters'].get(str(card['characterId']), {})
    return (card['characterId'] in scope['members'] and card['rarity'] in scope['rarities']
            and character.get('bandId') in scope['bands']
            and card.get('attribute') in scope.get('attributes', ATTRIBUTES))


def validate_maximize(value=None):
    if value is None:
        return dict.fromkeys(NUMERIC, False)
    if (not isinstance(value, dict) or set(value)-set(NUMERIC)
            or any(type(v) is not bool for v in value.values())):
        raise ProfileError('拉满选项格式无效')
    return {f: value.get(f, False) for f in NUMERIC}


def maximum_values(card, maximize):
    values = {}
    if maximize['level']:
        values.update(level=card['levelLimit']+card.get('trainingLevel', 0),
                      train=int(bool(card.get('trainingLevel', 0))))
    if maximize['skill']:
        values['skill'] = 4
    if maximize['master']:
        values['master'] = 4
    return values


def merge_profile(base, observations, scope, policy, catalog, *, name=None, maximize=None):
    base = parse_profile(base)
    if base['server'] != 3:
        raise ProfileError('当前扫描中国服，不能更新其他服务器的底稿')
    scope = validate_scope(scope, catalog)
    maximize = validate_maximize(maximize)
    if policy not in ('overwrite', 'max'):
        raise ProfileError('冲突处理必须为覆盖或取最大值')
    if not isinstance(observations, list) or len(observations) > MAX_CARDS:
        raise ProfileError('扫描记录无效')
    result = copy.deepcopy(base)
    if name is not None:
        result['name'] = name
    positions = {}
    for i, card in enumerate(result['cards']):
        positions.setdefault(card['id'], []).append(i)
    changes, skipped, seen = [], [], set()
    for observation in observations:
        if not isinstance(observation, dict):
            raise ProfileError('扫描卡牌记录无效')
        id_ = integer(observation.get('id'), 1, 999999, '扫描卡牌 ID')
        if id_ in seen:
            raise ProfileError('扫描结果存在重复卡牌 ID，请先解决冲突')
        seen.add(id_)
        if not in_scope(id_, scope, catalog):
            continue
        index = positions.get(id_, [])
        if len(index) > 1:
            skipped.append({'id': id_, 'reason': '底稿有多条同 ID 卡牌，保留并等待人工处理'})
            continue
        before = copy.deepcopy(result['cards'][index[0]]) if index else None
        # Only confirmed fields are observations; absent means unknown, not zero.
        row_maximize = validate_maximize(observation.get('maximize'))
        forced = maximum_values(catalog['cards'][str(id_)],
                                {f:maximize[f] or row_maximize[f] for f in NUMERIC})
        observed = {f: observation[f] for f in (*NUMERIC, 'train') if f in observation and f not in forced}
        for f, v in observed.items():
            integer(v, *FIELDS[f], f)
        if not before and not all(f in observed or f in forced for f in (*NUMERIC, 'train')):
            skipped.append({'id': id_, 'reason': '新卡培养数值未读全，请补全或跳过'})
            continue
        after = copy.deepcopy(before) if before else {'id': id_, 'ep': 0, 'art': 0, 'exclude': False}
        for f, v in observed.items():
            after[f] = max(after.get(f, v), v) if policy == 'max' else v
        # Explicit maximum settings override both observations and the base,
        # even if the imported level exceeds this card's actual ceiling.
        after.update(forced)
        if not before:
            after['art'] = after['train']
        meta = catalog['cards'][str(id_)]
        limit = meta['levelLimit'] + (meta.get('trainingLevel', 0) if after['train'] else 0)
        if after['level'] > limit:
            skipped.append({'id': id_, 'reason': '等级与特训状态不一致，请检查底稿和识别结果'})
            continue
        if before:
            result['cards'][index[0]] = after
        else:
            result['cards'].append(after)
        if before != after:
            changes.append({'id': id_, 'before': before, 'observed': observed, 'forced': forced, 'after': after,
                            'kind': 'updated' if before else 'added'})
    return {'profile': parse_profile(result), 'changes': changes, 'skipped': skipped,
            'policy': policy, 'scope': scope, 'maximize': maximize,
            'added': sum(c['kind'] == 'added' for c in changes),
            'updated': sum(c['kind'] == 'updated' for c in changes)}
