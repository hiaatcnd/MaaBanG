import json
from pathlib import Path
import shutil
import tempfile
import unittest

from interface_test_support import load_interface
from song_interface import bindings, compact_interface, expand_interface, install_bindings

ROOT = Path(__file__).resolve().parents[1]


class SharedSongInterfaceTests(unittest.TestCase):
    def test_source_contains_only_bindings_and_static_choices(self):
        source = json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        self.assertEqual(source, compact_interface(source))
        for name, binding in bindings()['selectors'].items():
            cases = source['option'][name]['cases']
            self.assertLessEqual(len(cases), 1)
            for case in cases:
                self.assertEqual(case['pipeline_override'][binding['song_node']]['attach']['value'], '')

    def test_packaged_paths_resolve_and_all_options_match_development(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)
            shutil.copy2(ROOT/'assets/interface.json', package/'interface.json')
            (package/'agent/data').mkdir(parents=True)
            shutil.copy2(ROOT/'agent/data/songs_cn.json', package/'agent/data/songs_cn.json')
            install_bindings(package)
            self.assertEqual(load_interface(package/'interface.json'), load_interface())

    def test_synthetic_catalog_filters_deduplicates_and_keeps_slots_independent(self):
        source = json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        def song(sid, title, active=True, expert=True, special=False):
            return dict(id=str(sid), title=title, band='band', active=active,
                        difficulties={'expert': {'available': expert}, 'special': {'available': special}})
        catalog = dict(server='cn', songs=[song(1,'同名'), song(2,'同名',special=True),
            song(3,'关闭',active=False), song(4,'未开放',expert=False), song(306,'SAVIOR OF SONG')])
        expanded = expand_interface(source, catalog)
        options = expanded['option']
        self.assertEqual([c['name'] for c in options['演出歌曲']['cases']],
                         ['同名 [1]', '同名 [2]', 'SAVIOR OF SONG'])
        for slot in range(1, 4):
            cases = options[f'谱面第{slot}首歌曲']['cases']
            self.assertEqual([c['name'] for c in cases], ['SAVIOR OF SONG', '同名 [1]', '同名 [2]'])
            self.assertEqual(cases[2]['pipeline_override'], {f'CL_song{slot}': {'attach': {'value': '2'}}})
            self.assertEqual([c['name'] for c in options[cases[2]['option'][0]]['cases']], ['EXPERT','SPECIAL'])
        options['谱面第1首歌曲']['cases'][0]['label'] = 'changed'
        self.assertNotEqual(options['谱面第2首歌曲']['cases'][0]['label'], 'changed')
        self.assertEqual(compact_interface(expanded)['task'], source['task'])

    def test_empty_and_all_server_catalogs_are_rejected(self):
        source = json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        for catalog in (dict(server='cn', songs=[]), dict(scope='all_servers', songs=[])):
            with self.assertRaises(ValueError):
                expand_interface(source, catalog)


if __name__ == '__main__':
    unittest.main()
