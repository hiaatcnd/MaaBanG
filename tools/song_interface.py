"""Build-time/reference expansion of the shared catalog (never stored in the PI).

The client implements the same contract in MaaBanGSongCatalog.cs. The executable
parity check compares both implementations, including all case indices.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from catalog_update import DIFFICULTIES, playable, song_key


def bindings():
    return json.loads((ROOT/'assets/interface.songs.json').read_text(encoding='utf8'))


def compact_interface(interface, spec=None):
    """Remove generated choices, retaining static options and empty-song cases."""
    interface = deepcopy(interface)
    options = interface['option']
    for name, binding in (spec or bindings())['selectors'].items():
        node = binding['song_node']
        options[name]['cases'] = [case for case in options[name]['cases']
                                  if case.get('pipeline_override', {}).get(node, {})
                                  .get('attach', {}).get('value') == '']
        if not options[name]['cases']:
            options[name].pop('default_case', None)
        prefix = binding.get('difficulty_prefix')
        if prefix:
            for key in list(options):
                if key.startswith(prefix):
                    del options[key]
    return interface


def expand_interface(interface, catalog, spec=None):
    spec = spec or bindings()
    if spec['version'] != 1 or catalog.get('server') != 'cn':
        raise ValueError('Expected v1 bindings and a CN selection catalog')
    interface = compact_interface(interface, spec)
    options = interface['option']
    songs = playable(catalog['songs'])
    if not songs:
        raise ValueError('Empty playable CN catalog')
    chart_songs = sorted(songs, key=lambda s: (s['title'] != 'SAVIOR OF SONG', int(s['id'])))
    for name, binding in spec['selectors'].items():
        option = options[name]
        option.setdefault('default_case', spec['default_case'])
        node = binding['song_node']
        for song in songs if binding['order'] == 'catalog' else chart_songs:
            key = song_key(song, songs)
            case = {'name': key, 'label': f"{song['title']} · {song['band']}",
                    'pipeline_override': {node: {'attach': {
                        'value': key if binding['value'] == 'key' else song['id']}}}}
            if prefix := binding.get('difficulty_prefix'):
                names = [d for d in DIFFICULTIES if song['difficulties'].get(d, {}).get('available')]
                profile = prefix + '_'.join(names)
                options[profile] = {
                    'type': 'select', 'label': option['label'].replace('歌曲', '难度'),
                    'default_case': 'EXPERT', 'cases': [
                        {'name': d.upper(), 'pipeline_override': {
                            binding['difficulty_node']: {'attach': {'value': d}}}}
                        for d in names]}
                case['option'] = [profile]
            option['cases'].append(case)
        if option.get('default_case') not in {c['name'] for c in option['cases']}:
            option['default_case'] = option['cases'][0]['name']
    return interface


def load_interface(path=ROOT/'assets/interface.json'):
    path = Path(path)
    interface = json.loads(path.read_text(encoding='utf8'))
    spec = json.loads(path.with_suffix('.songs.json').read_text(encoding='utf8'))
    catalog = json.loads((path.parent/spec['catalog']).read_text(encoding='utf8'))
    return expand_interface(interface, catalog, spec)


def install_bindings(package):
    spec = bindings()
    spec['catalog'] = 'agent/data/songs_cn.json'
    (Path(package)/'interface.songs.json').write_text(
        json.dumps(spec, ensure_ascii=False, indent=4)+'\n', encoding='utf8')
