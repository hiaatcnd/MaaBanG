import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from bestdori_desktop import DesktopWorkspace, job_directory, run_scan
from bestdori_catalog import atomic_json
from bestdori_profiles import ProfileError


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.w = DesktopWorkspace(self.folder.name)
        self.options = {'scope': {'bands': [1], 'members': [1], 'rarities': [5]},
                        'maximize': {'level': True, 'skill': True, 'master': True}}

    def start(self):
        result = self.w.execute('scan', self.options)
        return result['scan_id'], job_directory(result['scan_id'], self.folder.name)

    def test_scan_bridge_captures_scope_and_preserves_partial_after_failure(self):
        id_, folder = self.start()
        request = json.loads((folder/'request.json').read_text(encoding='utf-8'))
        self.assertEqual(request['maximize'], self.options['maximize'])
        self.assertNotIn('password', request)
        with self.assertRaises(ProfileError): self.w.execute('empty', {})
        atomic_json(folder/'progress.json', {'message': '已检查 2 张'})
        self.assertEqual(self.w.state()['message'], '已检查 2 张')
        atomic_json(folder/'scan.json', {'status':'running', 'observations':[{'id':1703}], 'errors':[]})
        self.w.execute('scan_finish', {'scan_id':id_, 'error':'设备已断开'})
        self.assertFalse(self.w.busy)
        self.assertEqual(self.w.observations, [{'id':1703,'original':{}}])
        self.assertEqual(self.w.scan_status, 'partial')
        self.assertIn('设备已断开', self.w.scan_errors)
        self.assertTrue((folder/'finished').exists())

    def test_connection_failure_and_stale_finish_do_not_corrupt_next_scan(self):
        id_, _ = self.start()
        self.w.execute('scan_finish', {'scan_id':id_})
        self.assertFalse(self.w.busy)
        self.assertEqual(self.w.scan_status, 'partial')
        self.start()
        with self.assertRaises(ProfileError): self.w.execute('scan_finish', {'scan_id':id_})
        self.assertTrue(self.w.busy)

    def test_scan_and_review_cache_survive_restart_without_cloud_or_result(self):
        id_,folder=self.start()
        rows=[{'id':1703,'level':60,'skill':2,'master':0,'train':0}]
        atomic_json(folder/'scan.json',{'status':'partial','observations':rows,'errors':['已停止']})
        self.w.execute('scan_finish',{'scan_id':id_})
        restored=DesktopWorkspace(self.folder.name)
        rows[0]['original'] = {k: rows[0][k] for k in ('level','skill','master','train')}
        self.assertEqual(restored.observations,rows)
        self.assertEqual(restored.scan_status,'partial')
        self.assertIsNone(restored.result)
        self.assertIsNone(restored.client.username)
        rows[0].update(skill=4,selected=False,maximize={'skill':True})
        restored.execute('cache_review',{'scope':self.options['scope'],'observations':rows})
        self.assertEqual(DesktopWorkspace(self.folder.name).observations,rows)
        restored.execute('preview',{'observations':[{'id':1703,'level':60,'skill':4,'master':4,'train':1}],
                                  'scope':self.options['scope'],'policy':'max','name':'Preview'})
        self.assertEqual(restored.observations,rows,'preview must not replace review originals or selection')

    def test_previous_preview_scan_is_recovered_without_fabricating_observed_values(self):
        id_,folder=self.start()
        atomic_json(folder/'scan.json',{'status':'partial','observations':[{'id':1703,'skill':2}],'errors':['已停止']})
        (folder/'finished').touch()
        restored=DesktopWorkspace(self.folder.name)
        self.assertEqual(restored.observations[0]['maximize'],self.options['maximize'])
        self.assertNotIn('level',restored.observations[0])
        self.assertIsNotNone(restored.cached_at)

    def test_legacy_review_recovers_missing_originals_and_preserves_edits(self):
        from datetime import datetime
        id_,folder=self.start()
        raw={'id':1703,'level':35,'skill':2,'master':0,'train':0}
        atomic_json(folder/'scan.json',{'status':'complete','observations':[raw],'errors':[]})
        (folder/'finished').touch()
        review={'id':1703,'skill':4,'master':0,'selected':False,'original':{'skill':4,'master':0},'maximize':{'level':True}}
        legacy={'observations':[review],'scope':self.options['scope'],'saved_at':datetime.now().isoformat(),'status':'complete'}
        atomic_json(Path(self.folder.name)/'last-scan.json',legacy)
        restored=DesktopWorkspace(self.folder.name)
        row=restored.observations[0]
        self.assertEqual(row['original'],{k:raw[k] for k in ('level','skill','master','train')})
        self.assertEqual((row['level'],row['train'],row['skill'],row['selected']),(35,0,4,False))
        self.assertEqual(restored.cache_recovery['fields'],3)
        restored.execute('cache_review',{'scope':self.options['scope'],'observations':[row]})
        self.assertEqual(json.loads(next((Path(self.folder.name)/'backups').glob('review-before-recovery-*')).read_text('utf8')),legacy)
        self.assertEqual(DesktopWorkspace(self.folder.name).observations,[row])

    def test_recovery_rejects_mismatched_scan_and_does_not_refill_deliberately_cleared_values(self):
        from datetime import datetime
        _,folder=self.start()
        atomic_json(folder/'scan.json',{'status':'complete','observations':[{'id':1703,'level':60,'skill':4,'master':0,'train':1}],'errors':[]})
        (folder/'finished').touch()
        saved={'observations':[{'id':1704,'original':{'skill':2}}],'scope':self.options['scope'],'saved_at':datetime.now().isoformat()}
        path=Path(self.folder.name)/'last-scan.json'
        atomic_json(path,saved)
        self.assertEqual(DesktopWorkspace(self.folder.name).observations,saved['observations'])
        saved['observations'][0]['id']=1703
        saved['observations'][0]['original']['skill']=4
        saved['version']=2
        atomic_json(path,saved)
        self.assertEqual(DesktopWorkspace(self.folder.name).observations,saved['observations'])

    def test_cancel_before_agent_starts_performs_no_game_input_and_cannot_replay(self):
        id_, folder = self.start()
        self.w.execute('cancel', {})
        self.assertTrue((folder/'cancel').exists())
        with patch('bestdori_scan.BestdoriScan') as scan:
            report = run_scan(Mock(), id_, self.folder.name)
            scan.assert_not_called()
        self.assertEqual(report['status'], 'partial')
        with self.assertRaises(ProfileError): run_scan(Mock(), id_, self.folder.name)

    def test_bridge_runs_scanner_with_skip_options_and_rejects_expired_or_unsafe_ids(self):
        id_, folder = self.start()
        with patch('bestdori_scan.BestdoriScan') as scan:
            scan.return_value.run.return_value = {'status': 'complete'}
            self.assertEqual(run_scan(Mock(), id_, self.folder.name)['status'], 'complete')
            self.assertEqual(scan.call_args.kwargs['maximize'], self.options['maximize'])
        for id_ in ('../escape', '', None, 'f'*31):
            with self.assertRaises(ProfileError): job_directory(id_, self.folder.name)
        self.w.busy = False
        id_, folder = self.start()
        request = json.loads((folder/'request.json').read_text(encoding='utf-8'))
        request['created'] = time.time()-601
        atomic_json(folder/'request.json', request)
        with self.assertRaises(ProfileError): run_scan(Mock(), id_, self.folder.name)

    def test_parent_pipe_closure_stops_private_service(self):
        env = dict(os.environ, MAABANG_DATA_DIR=self.folder.name, PYTHONUTF8='1')
        process = subprocess.Popen([sys.executable, str(ROOT/'agent/bestdori_desktop.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding='utf-8', env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            ready = json.loads(process.stdout.readline())
            origin, token = ready['url'].split('/#')
            with urlopen(Request(origin+'/api/state', headers={'X-MaaBanG-Token':token}), timeout=5) as response:
                self.assertEqual(json.load(response)['base_count'], 0)
            with urlopen(Request(origin+'/api/scan', data=json.dumps(self.options).encode(),
                         headers={'X-MaaBanG-Token':token, 'Content-Type':'application/json'}), timeout=5) as response:
                scan_id = json.load(response)['scan_id']
            process.stdin.close()
            self.assertEqual(process.wait(timeout=8), 0, process.stderr.read())
            self.assertTrue((Path(self.folder.name)/'bestdori'/'jobs'/scan_id/'cancel').exists())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdout.close(); process.stderr.close()
            if not process.stdin.closed: process.stdin.close()


if __name__ == '__main__':
    unittest.main()
