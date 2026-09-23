"""Mining options and image decisions, independent of device navigation."""
from dataclasses import dataclass
import re
import numpy as np

DIFFICULTIES = ('easy', 'normal', 'hard', 'expert', 'special')
DEFAULTS = dict(max_rounds='10', stories=True, memories=True, practice=False,
                unlock=False, stars='1,2,3', stage='main', fire=0, shortage='stop')
NODES = {key: 'MN_' + key for key in DEFAULTS}
DIFFICULTY_NODES = {difficulty:'MN_difficulty_'+difficulty for difficulty in DIFFICULTIES}


@dataclass(frozen=True)
class MiningOptions:
    max_rounds: int | None
    stories: bool
    memories: bool
    practice: bool
    unlock: bool
    stars: frozenset[int]
    stage: str
    difficulties: tuple[str, ...]
    fire: int
    shortage: str

    @classmethod
    def parse(cls, data):
        values = DEFAULTS | data
        raw = values['max_rounds']
        if raw in ('', None):
            limit = None
        elif isinstance(raw, bool) or not re.fullmatch(r'[1-9][0-9]{0,2}', str(raw)):
            raise ValueError('最大演出数请留空或填写 1–999')
        else:
            limit = int(raw)
        for key in ('stories', 'memories', 'practice', 'unlock'):
            if not isinstance(values[key], bool):
                raise ValueError(f'{key} 必须为布尔值')
        raw_stars = values['stars']
        if not isinstance(raw_stars, str) or not re.fullmatch(r'[1-5](?:,[1-5])*', raw_stars):
            raise ValueError('练习星级请用英文逗号分隔，例如 1,2,3')
        stars = frozenset(map(int, raw_stars.split(',')))
        if values['stage'] not in ('main', 'special'):
            raise ValueError('请选择主舞台或特别舞台')
        difficulties = values.get('difficulties',DIFFICULTIES)
        if not isinstance(difficulties,(list,tuple)) or any(d not in DIFFICULTIES for d in difficulties):
            raise ValueError('请选择有效的挖矿难度')
        fire = values['fire']
        if isinstance(fire,bool) or not re.fullmatch(r'[0-3]',str(fire)):
            raise ValueError('每首消耗火数必须为 0–3')
        shortage = values['shortage']
        if shortage not in ('stop','items'):
            raise ValueError('火不足时请选择停止或使用回复道具')
        return cls(limit, *(values[k] for k in ('stories', 'memories', 'practice', 'unlock')),
                   stars, values['stage'],tuple(d for d in DIFFICULTIES if d in difficulties),int(fire),shortage)


def star_state(area):
    """Classify the *interior* of a song star (BGR), never the pink row background.

    Rainbow AP and pink FC are both complete. Unknown images are not candidates.
    """
    if area.size == 0:
        return 'unknown'
    b, g, r = (area[:, :, i].astype(float) for i in range(3))
    if np.mean((b > 130) & (r > 170) & (r - g > 20)) > .22:
        return 'full_combo'
    if np.mean((r > 180) & (g > 130) & (b < 130)) > .22:
        return 'clear'
    # Unplayed selected stars are dark pink; unselected stars are neutral gray.
    if np.mean((r < 235) & (g < 180) & (b < 180)) > .65:
        return 'unplayed'
    return 'unknown'


def parse_level(text):
    match = re.search(r'(\d+)\s*/\s*(\d+)', text)
    if not match or not 1 <= int(match[1]) <= int(match[2]) <= 100:
        raise ValueError(f'无法确认成员等级：{text}')
    return tuple(map(int, match.groups()))


def can_practice(options, rarity, current, maximum, required):
    return (options.practice and rarity in options.stars and current < required
            and current < maximum and maximum >= required)


def pending_difficulties(states, selected):
    return tuple(d for d in DIFFICULTIES if d in selected and states.get(d) in ('clear','unplayed'))


def material_rois(image, frame_y=294, amount_y=397):
    """Find every colored material frame, so missing OCR cannot hide a cost."""
    strip = image[frame_y:frame_y+13,380:900].astype(float)
    mask = (np.ptp(strip,axis=2)>70)&(strip.max(2)>150)&(strip.min(2)<200)
    columns = np.flatnonzero(mask.any(0))
    groups = np.split(columns,np.where(np.diff(columns)>1)[0]+1)
    return [[380+int(g[0])+4,amount_y,len(g)-8,30] for g in groups if 100<=len(g)<=155]


def member_cards(image):
    """Locate complete portraits on the white member grid without tiny level OCR.

    Bonus labels are short and mostly white; portraits contain a continuous band
    of artwork. Dark pixels retain grayscale artwork and the level footer while
    colored pixels retain pale portraits. Ignore cards clipped by the viewport.
    """
    cards = []
    for x in (344,467,590,713,836,959,1082):
        strip = image[185:665,x-46:x+47].astype(float)
        color = (np.ptp(strip,axis=2)>35)&(strip.max(2)>90)
        ys = np.flatnonzero((color.mean(1)>.23)|((strip.min(2)<235).mean(1)>.65))
        for group in np.split(ys,np.where(np.diff(ys)>4)[0]+1):
            if not len(group):
                continue
            top,bottom = int(group[0]),int(group[-1])
            if top==0 or bottom==479 or not 95<=bottom-top+1<=112:
                continue
            cards.append((x,185+(top+bottom)//2))
    return sorted(cards,key=lambda card:(round(card[1]/12),card[0]))


def gold_member_stars(image):
    """Count gold interiors in the five fixed star slots, bottom upward."""
    strip=image[390:565,124:150].astype(float)
    b,g,r=(strip[:,:,i] for i in range(3))
    gold=(r>200)&(g>150)&(b<130)&(r-b>70)
    ys=np.flatnonzero(gold.mean(1)>.4)
    slots=set()
    for group in np.split(ys,np.where(np.diff(ys)>3)[0]+1):
        if not len(group) or not 8<=group[-1]-group[0]+1<=23:
            continue
        center=390+(group[0]+group[-1])/2
        for slot in range(5):
            if abs(center-(542-29*slot))<=7:
                slots.add(slot)
    return len(slots) if slots==set(range(len(slots))) else 0
