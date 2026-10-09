import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from interface_test_support import expand_interface

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
import catalog_update as updater
import song_catalog


class CatalogUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root/'data'
        shutil.copytree(ROOT/'agent/data', self.data)
        self.interface = self.root/'interface.json'
        shutil.copy2(ROOT/'assets/interface.json', self.interface)
        self.original = {p: p.read_bytes() for p in
                         (self.interface, self.data/'songs_cn.json', self.data/'songs_all.json')}
        self.source = {'99999': {'bandId': 1, 'musicTitle': ['new', None, None, '新歌曲'],
                                'publishedAt': [1, None, None, 1],
                                'difficulty': {'3': {'playLevel': 26}}, 'notes': {'3': 500}},
                       '99998': {'bandId': 1, 'musicTitle': ['JP only'],
                                'publishedAt': [1], 'difficulty': {'3': {'playLevel': 25}}}}
        self.raw = json.dumps(self.source).encode()

    def refresh(self, **kwargs):
        return updater.refresh_catalog(self.data, songs_raw=self.raw,
                                       bands_raw=b'{}', **kwargs)

    def assert_unchanged(self):
        for path, old in self.original.items():
            self.assertEqual(path.read_bytes(), old)

    def test_refresh_all_selectors_and_keep_tasks_and_other_options(self):
        catalog, recognition = self.refresh()
        self.assertEqual([s['id'] for s in catalog['songs']], ['99999'])
        self.assertEqual([s['id'] for s in recognition['songs']], ['99998', '99999'])
        new = expand_interface(json.loads(self.interface.read_text(encoding='utf8')), catalog)
        old = json.loads(self.original[self.interface])
        self.assertEqual(self.interface.read_bytes(), self.original[self.interface])
        self.assertEqual(new['task'], old['task'])
        self.assertEqual(new['option']['谱面随机偏差'], old['option']['谱面随机偏差'])
        for key in ('演出歌曲', '谱面第1首歌曲', '谱面第2首歌曲', '谱面第3首歌曲', '直接演出歌曲'):
            self.assertEqual([c['name'] for c in new['option'][key]['cases']], ['新歌曲'])
        for key in ('谱面协力歌曲', '谱面挑战歌曲'):
            self.assertEqual(len(new['option'][key]['cases']), 2)

    def test_download_invalid_data_and_cancel_keep_existing_files(self):
        with patch.object(updater, 'fetch', side_effect=TimeoutError('offline')):
            with self.assertRaises(TimeoutError):
                updater.refresh_catalog(self.data)
        self.assert_unchanged()
        self.raw = b'{}'
        with self.assertRaises(ValueError):
            self.refresh()
        self.assert_unchanged()
        with self.assertRaises(RuntimeError):
            self.refresh(check_stop=lambda: (_ for _ in ()).throw(RuntimeError('stopped')))
        self.assert_unchanged()

    def test_partial_replace_failure_rolls_back_catalogs(self):
        real_replace = updater.os.replace
        calls = []
        def replace(source, target):
            calls.append(target)
            if target == self.data/'songs_all.json':
                raise PermissionError('recognition catalog locked')
            return real_replace(source, target)
        with patch.object(updater.os, 'replace', side_effect=replace):
            with self.assertRaises(PermissionError):
                self.refresh()
        self.assertEqual(len(calls), 2)
        self.assert_unchanged()
        self.assertFalse(list(self.data.glob('*.tmp')))

    def test_another_instance_cannot_interleave_catalog_writes(self):
        with updater.catalog_update_lock(self.data):
            with self.assertRaisesRegex(RuntimeError, '另一个实例'):
                self.refresh()
        self.assert_unchanged()
        self.refresh()  # Lock is released after the first task exits.

    def test_dynamic_reload_keeps_imported_dictionary_references(self):
        from chart_policy import RECOGNITION_BY_ID as imported_recognition
        from mining_live import BY_ID as imported_choices
        self.refresh()
        try:
            with patch.object(song_catalog, 'DATA_DIR', self.data):
                song_catalog.reload_catalog()
                self.assertIs(imported_choices, song_catalog.BY_ID)
                self.assertEqual(song_catalog.resolve_song('新歌曲')['id'], '99999')
                self.assertIn('99998', imported_recognition)
                self.assertNotIn('99998', imported_choices)
        finally:
            song_catalog.reload_catalog()

    def test_registered_task_updates_through_framework_without_game_actions(self):
        import numpy as np
        from maa.controller import CustomController
        from maa.context import ContextEventSink
        from maa.resource import Resource
        from maa.tasker import Tasker
        import update_songs
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'song-catalog-test'
            def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
        resource = Resource()
        bundle = self.root/'resource'
        (bundle/'pipeline').mkdir(parents=True)
        shutil.copy2(ROOT/'assets/resource/pipeline/song_catalog.json', bundle/'pipeline/song_catalog.json')
        self.assertTrue(resource.post_bundle(bundle).wait().succeeded)
        controller = Controller()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        tasker.bind(resource, controller)
        events = []
        class Sink(ContextEventSink):
            def on_raw_notification(self, context, message, details):
                events.append((message, details.get('name')))
        tasker.add_context_sink(Sink())
        resource.register_custom_action('UpdateSongCatalog', update_songs.UpdateSongCatalog())
        agent = self.root/'agent'
        agent.mkdir()
        shutil.move(self.data, agent/'data')
        try:
            with patch.object(update_songs, '__file__', str(agent/'update_songs.py')), \
                 patch.object(song_catalog, 'DATA_DIR', agent/'data'), \
                 patch.object(updater, 'fetch', side_effect=[self.raw, b'{}']):
                self.assertTrue(tasker.post_task('UpdateSongCatalog').wait().succeeded)
                self.assertEqual(song_catalog.resolve_song('新歌曲')['id'], '99999')
                self.assertIn(('Node.Action.Succeeded', 'UpdateSongCatalog'), events)
        finally:
            song_catalog.reload_catalog()


if __name__ == '__main__':
    unittest.main()
