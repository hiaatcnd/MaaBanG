"""User-facing chart live configuration, independent of the built-in auto quota."""
from dataclasses import dataclass

from live_policy import DIFFICULTIES, number, fever_option, favorites_option
from song_catalog import RECOGNITION_BY_ID, resolve_song, available_difficulties

# Truncated normal timing and position jitter (sigma = bound/3), without clipping.
# Every profile is nonzero. Profiles describe input variation, not guaranteed judgments.
JITTER_PROFILES = {
    'precise': (3., 1.),
    'small': (9., 3.),
    'medium': (35., 6.),
    'medium_large': (60., 9.),
    'large': (90., 12.),
    'extreme': (180., 22.),
}

COOP_ROOMS = {'free':'自由房间', 'beginner':'新手房间',
              'master':'首席房间', 'legend':'传说房间'}
COOP_ROOM_GROUPS = {'normal':'普通', 'special':'特别'}


@dataclass(frozen=True)
class ChartSelection:
    song_id: str
    difficulty: str

    @property
    def song(self):
        # Selection validation stays CN-only; recognized songs can use all-server
        # chart metadata without changing saved selection/report identities.
        return RECOGNITION_BY_ID.get(self.song_id) or resolve_song(self.song_id)

    @classmethod
    def parse(cls, song, difficulty):
        entry = resolve_song(str(song))
        if difficulty not in available_difficulties(entry):
            raise ValueError(f"{entry['title']} 在中国服没有 {difficulty.upper()} 谱面")
        return cls(entry['id'], difficulty)

    @classmethod
    def from_recognized(cls, song, difficulty):
        """Use recognized metadata without going through the CN choice catalog."""
        if difficulty not in available_difficulties(song):
            raise ValueError(f"{song['title']} 没有 {difficulty.upper()} 谱面数据")
        return cls(song['id'], difficulty)


@dataclass(frozen=True)
class ChartOptions:
    mode: str
    selections: tuple[ChartSelection, ...]
    difficulties: tuple[str, ...]
    jitter: str
    fire: int
    shortage: str
    max_rounds: int | None
    cp: int = 200
    cp_song: str = ''
    coop_room: str = 'free'
    coop_room_group: str = 'normal'
    coop_song: str = ''
    fever: str = 'off'
    from_favorites: bool = False
    avoid_full_combo: bool = False

    @classmethod
    def parse(cls, data):
        mode = data.get('mode', 'free')
        if mode not in ('free', 'tour_free', 'tour_fixed', 'team', 'coop', 'challenge'):
            raise ValueError('未知谱面演出模式')
        jitter = data.get('jitter', 'small')
        avoid_full_combo = data.get('avoid_full_combo', False)
        if not isinstance(avoid_full_combo, bool):
            raise ValueError('避免 Full Combo 必须为开或关')
        if jitter not in JITTER_PROFILES:
            raise ValueError('请选择非零随机偏差档位')
        challenge = mode == 'challenge'
        shortage = 'stop' if challenge else data.get('shortage', 'stop')
        if shortage not in ('stop', 'items'):
            raise ValueError('火不足策略必须为停止或使用回复道具')
        fire = 0 if challenge else number(data.get('fire', 1), '每首火数', 3)
        cp = number(data.get('cp', 200), '每首CP', 1600)
        if cp not in (200, 400, 800, 1600):
            raise ValueError('每首CP必须为200、400、800或1600')
        limit = data.get('max_rounds', '')
        rounds = None if limit in ('', None) else number(limit, '最大演出次数', 999)
        if rounds == 0:
            raise ValueError('最大演出次数请留空或填写 1–999')
        online = mode in ('team', 'coop')
        count = 1 if mode == 'free' or online or challenge else 3
        difficulties = tuple(data.get(f'difficulty{i}', 'expert') for i in range(1, count+1))
        if any(d not in DIFFICULTIES for d in difficulties):
            raise ValueError('未知演出难度')
        selections = (() if mode == 'tour_fixed' or online or challenge else tuple(
            ChartSelection.parse(data.get(f'song{i}', '306'), d)
            for i, d in enumerate(difficulties, 1)))
        coop_room = data.get('coop_room', 'free')
        coop_room_group = data.get('coop_room_group', 'normal')
        coop_song = str(data.get('coop_song', '')).strip()
        if mode == 'coop':
            if coop_room not in COOP_ROOMS or coop_room_group not in COOP_ROOM_GROUPS:
                raise ValueError('未知协力房间类型')
            if coop_song:
                coop_song = resolve_song(coop_song)['id']
        return cls(mode, selections, difficulties, jitter, fire, shortage, rounds,
                   cp, str(data.get('cp_song', '')).strip(), coop_room, coop_room_group, coop_song,
                   fever_option(data.get('fever', 'off')),
                   favorites_option(data.get('from_favorites', False)), avoid_full_combo)

    @property
    def songs_per_round(self):
        return 3 if self.mode in ('tour_free', 'tour_fixed') else 1


OPTION_DEFAULTS = dict(mode='free', jitter='small', fire=1, shortage='stop', max_rounds='', cp=200, cp_song='', fever='off',
                       coop_room='free', coop_room_group='normal', coop_song='', from_favorites=False,
                       avoid_full_combo=False,
                       **{f'song{i}':'306' for i in range(1,4)},
                       **{f'difficulty{i}':'expert' for i in range(1,4)})
OPTION_NODES = {key: 'CL_'+key for key in OPTION_DEFAULTS}
