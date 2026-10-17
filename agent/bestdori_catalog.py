"""Small offline identity catalog; explicit refresh uses public metadata only."""
import json
import os
import tempfile
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen

from bestdori_profiles import ProfileError


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def build(cards, characters, bands):
    result = {'cards': {}, 'characters': characters, 'bands': bands}
    for id_, card in cards.items():
        if str(card['characterId']) not in characters:
            continue
        result['cards'][id_] = {k: card[k] for k in ('characterId', 'rarity', 'attribute',
                                                   'levelLimit', 'prefix')}
        result['cards'][id_]['trainingLevel'] = card.get('stat', {}).get('training', {}).get('levelLimit', 0)
    validate(result)
    return result


def validate(data):
    if not all(isinstance(data.get(k), dict) and data[k] for k in ('cards', 'characters', 'bands')):
        raise ProfileError('Bestdori 目录不完整')
    for id_, card in data['cards'].items():
        int(id_)
        if (card['rarity'] not in range(1, 6) or not 1 <= card['levelLimit'] <= 100
                or not isinstance(card['prefix'], list)
                or str(card['characterId']) not in data['characters']):
            raise ProfileError('卡牌目录无效')
    for character in data['characters'].values():
        if str(character['bandId']) not in data['bands']:
            raise ProfileError('角色与乐队目录不匹配')
    return data


def load():
    return validate(json.loads((Path(__file__).parent/'data/bestdori.json').read_text(encoding='utf-8')))


def refresh():
    sources = []
    for endpoint in ('cards/all.5.json', 'characters/main.3.json', 'bands/main.1.json'):
        req = Request('https://bestdori.com/api/'+endpoint, headers={'User-Agent': 'MaaBanG/BestdoriProfiles'})
        with urlopen(req, timeout=25) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ProfileError('目录响应过大')
        sources.append(json.loads(raw))
    return build(*sources)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def identify(title_text, member_text, rarity, catalog):
    """Require full title and character identity, never pick a fuzzy first match."""
    title, member = normalized(title_text), normalized(member_text)
    members = {int(id_) for id_, c in catalog['characters'].items()
               if member and member in {normalized(n) for n in
                   [*c.get('characterName', []), *c.get('nickname', [])] if n}}
    matches = [int(id_) for id_, c in catalog['cards'].items()
               if c['characterId'] in members and c['rarity'] == rarity
               and title and title in {normalized(n) for n in c['prefix'] if n}]
    return matches[0] if len(matches) == 1 else None
