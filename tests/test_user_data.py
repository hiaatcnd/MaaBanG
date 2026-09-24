import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from chart_store import ChartStore
from user_data import data_root, migrate_legacy


class UserDataTests(unittest.TestCase):
    def app(self, parent, name, config=None):
        app = parent / f'MaaBanG-{name}' / 'app'
        (app / 'agent').mkdir(parents=True)
        (app / 'interface.json').write_text('{}')
        if config is not None:
            (app / 'config/instances').mkdir(parents=True)
            (app / 'config/config.json').write_text(json.dumps(config))
            (app / 'config/instances/default.json').write_text(json.dumps({'tasks': [name]}))
        return app

    def chart(self, app, name, value):
        folder = app / 'cache/charts'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(json.dumps(value))

    def test_cache_is_shared_across_working_directories_but_explicit_paths_are_respected(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'MAABANG_DATA_DIR': folder}):
            self.assertEqual(ChartStore().directory, Path(folder).resolve() / 'cache/charts')
            with patch('pathlib.Path.cwd', return_value=Path(folder) / 'another-version'):
                self.assertEqual(ChartStore().directory, Path(folder).resolve() / 'cache/charts')
            self.assertEqual(ChartStore('custom').directory, Path('custom'))
        with patch.dict(os.environ, {k:v for k,v in os.environ.items() if k!='MAABANG_DATA_DIR'}, clear=True):
            self.assertEqual(data_root(), Path.home() / '.maabang')

    def test_imports_current_settings_as_one_snapshot_and_merges_unique_charts(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); shared=base/'shared'
            old=self.app(base,'old',{'selected':'old'})
            current=self.app(base,'current',{'selected':'current'})
            (current/'appsettings.json').write_text('{"theme":"dark"}')
            self.chart(old,'1_easy.json',[{'type':'Single'}])
            self.chart(current,'2_expert.json',[{'type':'BPM'}])
            self.chart(old,'2_expert.json',[{'type':'different'}])
            result=migrate_legacy(current,shared)
            self.assertEqual(result['charts_imported'],2)
            self.assertEqual(result['config_source'],str(current.resolve()))
            for relative in ('config/config.json','config/instances/default.json','appsettings.json'):
                self.assertEqual((shared/relative).read_bytes(),(current/relative).read_bytes())
            self.assertEqual((shared/'cache/charts/2_expert.json').read_bytes(),(current/'cache/charts/2_expert.json').read_bytes())
            self.assertTrue((old/'config/config.json').exists())

    def test_new_or_older_package_never_overwrites_shared_preferences_or_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); shared=base/'shared'
            old=self.app(base,'old',{'selected':'old'})
            current=self.app(base,'current',{'selected':'current'})
            self.chart(current,'1_easy.json',[{'type':'current'}])
            migrate_legacy(current,shared)
            (shared/'config/config.json').write_text('{"selected":"edited"}')
            (shared/'config/instances/default.json').unlink()
            self.chart(old,'1_easy.json',[{'type':'old'}])
            result=migrate_legacy(old,shared)
            self.assertIsNone(result['config_source'])
            self.assertEqual(result['charts_imported'],0)
            self.assertEqual(json.loads((shared/'config/config.json').read_text()),{'selected':'edited'})
            self.assertFalse((shared/'config/instances/default.json').exists())
            self.assertEqual(json.loads((shared/'cache/charts/1_easy.json').read_text()),[{'type':'current'}])

    def test_new_package_can_import_an_existing_sibling_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            old=self.app(base,'old',{'selected':'old'})
            new=self.app(base,'new')
            self.assertEqual(migrate_legacy(new,base/'shared')['config_source'],str(old.resolve()))

    def test_invalid_configuration_aborts_without_partial_shared_config(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); app=self.app(base,'current',{})
            (app/'config/instances/default.json').write_text('{broken')
            with self.assertRaises(ValueError):migrate_legacy(app,base/'shared')
            self.assertFalse((base/'shared/config').exists())
            self.assertTrue((app/'config/config.json').exists())

    def test_invalid_and_unrelated_chart_files_are_not_imported(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); app=self.app(base,'current',{})
            self.chart(app,'1_easy.json',{})
            self.chart(app,'2_easy.json',[])
            self.chart(app,'3_easy.json',['wrong'])
            self.chart(app,'random.json',[{}])
            self.chart(app,'4_easy.json',[{}])
            result=migrate_legacy(app,base/'shared')
            self.assertEqual(result['invalid_charts'],3)
            self.assertEqual(result['charts_imported'],1)
            self.assertEqual([p.name for p in (base/'shared/cache/charts').iterdir()],['4_easy.json'])


if __name__ == '__main__':
    unittest.main()
