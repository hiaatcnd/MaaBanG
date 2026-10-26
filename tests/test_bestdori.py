import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from bestdori_profiles import (parse_profile, empty_profile, export_profile, merge_profile,
                              ATTRIBUTES, ProfileError, validate_scope, fingerprint)
from bestdori_catalog import load, identify
from bestdori_client import BestdoriClient, CloudError
from bestdori_portal import Workspace, make_server


def card(id_=1, **fields):
    return {'id': id_, 'level': 20, 'skill': 4, 'master': 2, 'ep': 2,
            'train': 0, 'art': 0, 'exclude': True, **fields}


class ProfilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load()
        cls.scope = {'bands': [1], 'members': [1], 'rarities': [1, 2, 3, 4, 5]}

    def base(self):
        p = empty_profile('底稿')
        p['cards'] = [card(), card(2)]
        p['items']['PoppinParty'][0] = 7
        p['items']['potentials'][0] = 50
        return p

    def test_official_compression_two_and_raw_round_trip(self):
        for compressed in (False, True):
            p = self.base()
            self.assertEqual(parse_profile(export_profile(p, compressed=compressed)), p)
        # The website uses unsigned 16-bit IDs with a special high-ID mapping.
        p = empty_profile()
        p['cards'] = [card(90001)]
        self.assertEqual(parse_profile(export_profile(p, compressed=True)), p)

    def test_old_format_one(self):
        p = parse_profile({'name': '旧版', 'server': 3, 'compression': '1', 'data': '01K00'})
        self.assertEqual(p['cards'][0], card(level=20, skill=0, master=0, ep=0, exclude=False))

    def test_old_v2_without_master_defaults_to_zero_and_unknown_extensions_fail(self):
        p = json.loads(export_profile(self.base(), compressed=True))
        del p['data']['cards']['masters']
        self.assertEqual(parse_profile(p)['cards'][0]['master'], 0)
        p['data']['future'] = {'keep': True}
        with self.assertRaises(ProfileError):
            parse_profile(p)

    def test_import_preserves_extensions_and_cloud_refuses_lossy_export(self):
        p = self.base()
        p['future'] = {'x': [1, 2]}
        p['cards'][0]['future'] = 'keep'
        self.assertEqual(parse_profile(export_profile(p)), p)
        with self.assertRaises(ProfileError):
            export_profile(p, compressed=True)

    def test_corrupt_compression_unknown_version_and_bomb_fail(self):
        for p in ({'cards': [], 'compression': '9', 'data': {}},
                  {'compression': '2', 'data': {}}, [], {'name': 'oops'},
                  {'cards': [card(skill=True)]}):
            with self.subTest(p=p), self.assertRaises(ProfileError):
                parse_profile(p)
        p = json.loads(export_profile(self.base(), compressed=True))
        p['data']['cards']['levels'] = [1000000000, 20]
        with self.assertRaises(ProfileError):
            parse_profile(p)

    def test_overwrite_and_max_have_different_results_without_touching_other_data(self):
        p = self.base()
        before = copy.deepcopy(p)
        observation = [{'id': 1, 'level': 10, 'skill': 1, 'master': 4, 'train': 0}]
        for policy, expected in [('overwrite', (10, 1, 4)), ('max', (20, 4, 4))]:
            r = merge_profile(p, observation, self.scope, policy, self.catalog)
            c = r['profile']['cards'][0]
            self.assertEqual(tuple(c[f] for f in ('level', 'skill', 'master')), expected)
            self.assertEqual((c['ep'], c['art'], c['exclude']), (2, 0, True))
            self.assertEqual(r['profile']['cards'][1], before['cards'][1])
            self.assertEqual(r['profile']['items'], before['items'])
        self.assertEqual(p, before)

    def test_unknown_fields_preserve_base_and_unknown_new_card_is_skipped(self):
        p = self.base()
        r = merge_profile(p, [{'id': 1, 'master': 4}], self.scope, 'overwrite', self.catalog)
        self.assertEqual(r['profile']['cards'][0]['level'], 20)
        r = merge_profile(empty_profile(), [{'id': 1, 'master': 4}], self.scope, 'max', self.catalog)
        self.assertEqual(len(r['skipped']), 1)
        self.assertEqual(r['added'], 0)

    def test_scoped_add_is_idempotent(self):
        other = next(int(id_) for id_, c in self.catalog['cards'].items() if c['characterId'] != 1)
        observations = [card(exclude=False), card(other, level=1)]
        r = merge_profile(empty_profile(), observations, self.scope, 'max', self.catalog)
        self.assertEqual(r['added'], 1)
        r2 = merge_profile(r['profile'], observations, self.scope, 'max', self.catalog)
        self.assertEqual(r2['changes'], [])

    def test_filters_are_intersection_and_empty_does_not_mean_all(self):
        p = self.base()
        for scope in ({'bands': [2], 'members': [6], 'rarities': [1]},
                      {'bands': [1], 'members': [1], 'rarities': [5]}):
            r = merge_profile(p, [card(level=1)], scope, 'overwrite', self.catalog)
            self.assertEqual(r['profile'], p)
        with self.assertRaises(ProfileError):
            validate_scope({'bands': [1], 'members': [], 'rarities': [1]}, self.catalog)

    def test_duplicate_base_ids_never_choose_arbitrarily(self):
        p = self.base()
        p['cards'].append(card(level=10))
        r = merge_profile(p, [card(level=1)], self.scope, 'overwrite', self.catalog)
        self.assertEqual(r['profile'], p)
        self.assertEqual(len(r['skipped']), 1)
        with self.assertRaises(ProfileError):
            merge_profile(p, [card(), card()], self.scope, 'max', self.catalog)

    def test_attribute_selection_only_merges_matching_cards_and_preserves_the_rest(self):
        ids = [next(int(id_) for id_, c in self.catalog['cards'].items()
                    if c['characterId'] == 1 and c['rarity'] == 5 and c['attribute'] == kind)
               for kind in ATTRIBUTES]
        baseline = self.base()
        baseline['cards'].extend(card(id_) for id_ in ids)
        observations = [card(id_, skill=1, master=4) for id_ in ids]
        scope = {**self.scope, 'rarities': [5], 'attributes': ['cool', 'happy']}
        for policy in ('max', 'overwrite'):
            result = merge_profile(baseline, observations, scope, policy, self.catalog)
            by_id = {c['id']: c for c in result['profile']['cards']}
            self.assertEqual(result['updated'], 2)
            for original in baseline['cards']:
                if original['id'] in (ids[1], ids[3]):
                    self.assertEqual(by_id[original['id']]['master'], 4)
                    self.assertEqual(by_id[original['id']]['skill'], 4 if policy == 'max' else 1)
                else:
                    self.assertEqual(by_id[original['id']], original)
            self.assertEqual(result['profile']['items'], baseline['items'])
        added = merge_profile(empty_profile(), observations, scope, 'max', self.catalog)
        self.assertEqual({c['id'] for c in added['profile']['cards']}, {ids[1], ids[3]})

    def test_legacy_scope_defaults_to_all_attributes_but_empty_or_invalid_selection_fails(self):
        self.assertEqual(validate_scope(self.scope, self.catalog)['attributes'], list(ATTRIBUTES))
        self.assertEqual(validate_scope({**self.scope, 'attributes': ['happy', 'cool', 'cool']}, self.catalog)['attributes'], ['cool', 'happy'])
        for attributes in ([], None, 'cool', ['unknown'], ['Cool'], [True], [1], [{}]):
            with self.subTest(attributes=attributes), self.assertRaises(ProfileError):
                validate_scope({**self.scope, 'attributes': attributes}, self.catalog)

    def test_cross_server_and_inconsistent_training_are_not_written(self):
        p = self.base(); p['server'] = 0
        with self.assertRaises(ProfileError):
            merge_profile(p, [], self.scope, 'max', self.catalog)
        r = merge_profile(self.base(), [{'id': 1, 'level': 99}], self.scope, 'max', self.catalog)
        self.assertEqual(r['profile'], self.base())
        self.assertEqual(len(r['skipped']), 1)

    def test_identity_needs_character_full_title_and_rarity(self):
        self.assertEqual(identify('奋不顾身向前冲！', '户山 香澄', 1, self.catalog), 1)
        self.assertIsNone(identify('奋不顾身', '户山香澄', 1, self.catalog))
        self.assertIsNone(identify('奋不顾身向前冲！', '花园多惠', 1, self.catalog))
        self.assertIsNone(identify('奋不顾身向前冲！', '户山香澄', 5, self.catalog))

    def test_maximum_settings_override_base_observations_and_conflict_policy(self):
        p = self.base()
        p['cards'] = [card(1703, level=90, train=0, skill=1, master=1), card(1704)]
        for policy in ('overwrite', 'max'):
            r = merge_profile(p, [{'id': 1703, 'level': 'ignored', 'skill': -1, 'master': -1}],
                              self.scope, policy, self.catalog, maximize=dict.fromkeys(('level', 'skill', 'master'), True))
            c = r['profile']['cards'][0]
            self.assertEqual((c['level'], c['skill'], c['master'], c['train']), (60, 4, 4, 1))
            self.assertEqual((c['ep'], c['art'], c['exclude']), (2, 0, True))
            self.assertEqual(r['profile']['cards'][1], p['cards'][1])
            self.assertEqual(r['profile']['items'], p['items'])

    def test_maximum_identity_only_records_and_turning_maximum_off(self):
        rows = [{'id': 1}, {'id': 1703}]
        r = merge_profile(empty_profile(), rows, self.scope, 'max', self.catalog,
                          maximize=dict.fromkeys(('level', 'skill', 'master'), True))
        self.assertEqual([(c['level'], c['train'], c['master']) for c in r['profile']['cards']], [(20, 0, 4), (60, 1, 4)])
        self.assertEqual(rows, [{'id': 1}, {'id': 1703}])
        r = merge_profile(empty_profile(), rows, self.scope, 'max', self.catalog)
        self.assertEqual(r['added'], 0)
        self.assertEqual(len(r['skipped']), 2)

    def test_maximum_options_are_independent(self):
        p = self.base()
        for field in ('level', 'skill', 'master'):
            r = merge_profile(p, [{'id': 1}], self.scope, 'overwrite', self.catalog, maximize={field: True})
            c = r['profile']['cards'][0]
            self.assertEqual(c[field], 20 if field == 'level' else 4)
            for other in {'level', 'skill', 'master'}-{field}:
                self.assertEqual(c[other], p['cards'][0][other])
        with self.assertRaises(ProfileError):
            merge_profile(p, [], self.scope, 'max', self.catalog, maximize={'skill': 'yes'})

    def test_per_card_maximum_never_forces_another_card(self):
        member=self.catalog['cards']['1704']['characterId']
        scope={'bands':[1,self.catalog['characters'][str(member)]['bandId']],'members':[1,member],'rarities':[5]}
        rows=[card(1703,level=30,skill=1,master=0,maximize={'level':True,'skill':True}),
              card(1704,level=25,skill=2,master=1)]
        for policy in ('overwrite','max'):
            result=merge_profile(empty_profile(),rows,scope,policy,self.catalog)
            first,second=result['profile']['cards']
            self.assertEqual((first['level'],first['skill'],first['master']),(60,4,0))
            self.assertEqual((second['level'],second['skill'],second['master']),(25,2,1))


class CloudTests(unittest.TestCase):
    def test_missing_cloud_collection_is_never_interpreted_as_empty(self):
        c = BestdoriClient(); c.request = Mock(return_value={'result': True})
        with self.assertRaises(CloudError):
            c.profiles()
        c.request.return_value = {'result': True, 'profiles': None}
        self.assertEqual(c.profiles(), [])

    def client(self, current):
        c = BestdoriClient(); c.username = 'testuser'
        remote = {'profiles': copy.deepcopy(current)}
        def request(endpoint, body=None):
            if body is None:
                return {'result': True, **copy.deepcopy(remote)}
            remote['profiles'] = copy.deepcopy(body['profiles'])
            return {'result': True}
        c.request = Mock(side_effect=request)
        return c, remote

    def test_append_preserves_opaque_cloud_profiles_and_backs_up(self):
        old = [{'name': '保留', 'futureSchema': {'unknown': 1}}]
        c, remote = self.client(old)
        with tempfile.TemporaryDirectory() as folder:
            backup = Path(folder)/'backup.json'
            c.append(empty_profile('新档案'), backup)
            self.assertEqual(remote['profiles'][:-1], old)
            self.assertEqual(json.loads(backup.read_text(encoding='utf8'))['profiles'], old)
            self.assertTrue(c.append(empty_profile('新档案'), backup)['already_saved'])
            self.assertEqual(len(remote['profiles']), 2)
            self.assertEqual(sum(call.args[1:] != () for call in c.request.call_args_list), 1)

    def test_concurrent_change_aborts_before_post(self):
        c = BestdoriClient(); c.username = 'testuser'
        c.request = Mock(side_effect=[{'result': True, 'profiles': []},
                                     {'result': True, 'profiles': [{'name': '另一个设备'}]}])
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(CloudError):
            c.append(empty_profile(), Path(folder)/'backup.json')
        self.assertTrue(all(len(call.args) == 1 for call in c.request.call_args_list))

    def test_post_timeout_reads_back_before_reporting_failure(self):
        c, remote = self.client([])
        inner = c.request.side_effect
        def request(endpoint, body=None):
            result = inner(endpoint, body)
            if body is not None:
                raise CloudError('网络超时')
            return result
        c.request.side_effect = request
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(c.append(empty_profile(), Path(folder)/'backup.json')['already_saved'])

    def test_changed_existing_profiles_during_write_fail_verification_without_rollback(self):
        c = BestdoriClient();c.username = 'testuser'
        c.request = Mock(side_effect=[{'result': True, 'profiles': []},
            {'result': True, 'profiles': []}, {'result': True},
            {'result': True, 'profiles': [{'name': 'concurrent'}]}])
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(CloudError):
            c.append(empty_profile(), Path(folder)/'backup.json')
        self.assertEqual(len(c.request.call_args_list), 4)

    def test_login_uses_me_and_logout_always_clears_local_session(self):
        c=BestdoriClient()
        c.request=Mock(side_effect=[{'result':True},{'result':True,'username':'actualuser'}])
        self.assertEqual(c.login('inputuser','password'), 'actualuser')
        self.assertNotIn('password', c.__dict__)
        c.request=Mock(side_effect=CloudError('network'))
        with self.assertRaises(CloudError): c.logout()
        self.assertIsNone(c.username)

    def test_replace_preserves_position_opaque_neighbors_and_backup(self):
        source = json.loads(export_profile(empty_profile('我的档案'), compressed=True))
        old = [{'name': '前一个', 'future': {'x': 1}}, source, {'name': '后一个', 'opaque': 7}]
        c, remote = self.client(old)
        updated = empty_profile('我的档案'); updated['cards'] = [card()]
        with tempfile.TemporaryDirectory() as folder:
            backup = Path(folder)/'backup.json'
            c.replace(updated, backup, index=1, key=fingerprint(source), account='testuser')
            self.assertEqual(len(remote['profiles']), 3)
            self.assertEqual(remote['profiles'][::2], old[::2])
            self.assertEqual(parse_profile(remote['profiles'][1]), updated)
            self.assertEqual(json.loads(backup.read_text(encoding='utf8')), {'account': 'testuser', 'profiles': old})

    def test_replace_rejects_stale_target_account_and_duplicate_name_without_write(self):
        source = json.loads(export_profile(empty_profile('原档案'), compressed=True))
        cases = [(-1, fingerprint(source), 'testuser', '新档案'),
                 (True, fingerprint(source), 'testuser', '新档案'),
                 (0, 'stale', 'testuser', '新档案'),
                 (0, fingerprint(source), 'otheruser', '新档案'),
                 (0, fingerprint(source), 'testuser', '另一个')]
        for index, key, account, name in cases:
            with self.subTest(index=index, key=key, account=account, name=name), tempfile.TemporaryDirectory() as folder:
                old = [source, {'name': '另一个'}]; c, remote = self.client(old)
                with self.assertRaises(CloudError):
                    c.replace(empty_profile(name), Path(folder)/'backup.json', index=index, key=key, account=account)
                self.assertEqual(remote['profiles'], old)
                self.assertTrue(all(len(call.args) == 1 for call in c.request.call_args_list))

    def test_replace_rechecks_whole_collection_before_post(self):
        source = json.loads(export_profile(empty_profile('原档案'), compressed=True))
        c = BestdoriClient(); c.username = 'testuser'
        c.request = Mock(side_effect=[{'result': True, 'profiles': [source]},
                                     {'result': True, 'profiles': [source, {'name': '其他设备新增'}]}])
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(CloudError):
            c.replace(empty_profile('新档案'), Path(folder)/'backup.json', index=0,
                      key=fingerprint(source), account='testuser')
        self.assertTrue(all(len(call.args) == 1 for call in c.request.call_args_list))

    def test_replace_timeout_reads_back_and_never_replays(self):
        source = json.loads(export_profile(empty_profile('原档案'), compressed=True))
        for readback_fails in (False, True):
            with self.subTest(readback_fails=readback_fails), tempfile.TemporaryDirectory() as folder:
                c, remote = self.client([source]); inner = c.request.side_effect
                wrote = False
                def request(endpoint, body=None):
                    nonlocal wrote
                    if body is not None:
                        inner(endpoint, body); wrote = True
                        raise CloudError('提交超时')
                    if wrote and readback_fails:
                        raise CloudError('回读超时')
                    return inner(endpoint)
                c.request.side_effect = request
                def save():
                    return c.replace(empty_profile('已替换'), Path(folder)/'backup.json', index=0,
                                     key=fingerprint(source), account='testuser')
                if readback_fails:
                    with self.assertRaisesRegex(CloudError, '尚未确认'): save()
                else:
                    self.assertFalse(save()['already_saved'])
                self.assertEqual(remote['profiles'][0]['name'], '已替换')
                self.assertEqual(sum(len(call.args) > 1 for call in c.request.call_args_list), 1)


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.w=Workspace(self.folder.name)
        self.server,self.url=make_server(self.w)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin,self.token=self.url.rstrip('/').split('/#')

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.folder.cleanup()

    def request(self,path,body=None,headers=None):
        h={'X-MaaBanG-Token':self.token,'Content-Type':'application/json'}
        h.update(headers or {})
        return urlopen(Request(self.origin+path,data=None if body is None else json.dumps(body).encode(),headers=h),timeout=5)

    def test_page_and_api_security(self):
        with self.request('/api/state') as r:
            self.assertIn('base_name', json.loads(r.read()))
            self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        with self.assertRaises(HTTPError) as error:
            self.request('/')
        self.assertEqual(error.exception.code, 404)
        for headers in ({'X-MaaBanG-Token':'bad'}, {'Origin':'https://evil.example'},
                        {'Host':'evil.example'}, {'Sec-Fetch-Site':'cross-site'}):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as error:
                self.request('/api/state',headers=headers)
            self.assertEqual(error.exception.code,403)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/import',{}, {'Content-Type':'text/plain'})
        self.assertEqual(error.exception.code,415)

    def test_import_preview_export_and_stale_preview(self):
        scope={'bands':[1],'members':[1],'rarities':[1]}
        self.w.execute('import',{'text':export_profile(empty_profile('我的底稿'))})
        self.w.execute('preview',{'observations':[card()], 'scope':scope,'policy':'max','name':'新档案'})
        token=self.w.result_token
        output=self.w.execute('export',{'token':token})
        self.assertEqual(parse_profile(output['text'])['cards'][0]['id'],1)
        self.w.execute('empty',{})
        with self.assertRaises(ProfileError): self.w.execute('export',{'token':token})

    def test_scan_locks_mutations_and_cancel_keeps_partial_results(self):
        self.w.execute('scan',{'scope':{'bands':[1],'members':[1],'rarities':[1]}})
        with self.assertRaises(ProfileError):self.w.execute('empty',{})
        self.w.execute('cancel',{})
        self.assertTrue(self.w.cancel.is_set())
        self.w.process_scan(None,self.w.jobs.get())
        self.assertFalse(self.w.busy)
        self.assertEqual(self.w.scan_status,'partial')

    def test_scan_captures_maximum_settings_and_preview_uses_them(self):
        options = {'level': True, 'skill': True, 'master': True}
        scope = {'bands': [1], 'members': [1], 'rarities': [5]}
        self.w.execute('scan', {'scope': scope, 'maximize': options})
        job = self.w.jobs.get()
        options['level'] = False
        self.assertTrue(job['maximize']['level'])
        self.w.busy = False
        self.w.execute('preview', {'scope': scope, 'policy': 'max', 'observations': [{'id': 1703}], 'maximize': job['maximize']})
        self.assertEqual(self.w.result['profile']['cards'][0]['level'], 60)

    def test_cloud_base_uses_identity_guard(self):
        self.w.client.username = self.w.cloud_account = 'testuser'
        self.w.cloud=[json.loads(export_profile(empty_profile('云端'),compressed=True))]
        with self.assertRaises(ProfileError):self.w.execute('cloud_base',{'index':0,'key':'old'})
        self.w.execute('cloud_base',{'index':0,'key':fingerprint(self.w.cloud[0])})
        self.assertEqual(self.w.base['name'],'云端')

    def test_replace_requires_current_cloud_base_and_preview(self):
        base = empty_profile('我的档案'); base['cards'] = [card(id=2)]
        old = [json.loads(export_profile(base, compressed=True)), {'name': '其他档案', 'future': 1}]
        self.w.client, remote = CloudTests().client(old)
        choices = self.w.execute('cloud', {})['profiles']
        self.w.execute('cloud_base', choices[0])
        target = self.w.state()['cloud_target']
        with self.assertRaises(ProfileError): self.w.execute('replace', {'target': target})
        self.w.execute('preview', {'observations': [card()], 'scope': {'bands':[1], 'members':[1], 'rarities':[1]},
                                   'policy':'max', 'name':'我的档案'})
        request = {'token':self.w.result_token, 'target':target}
        with self.assertRaises(ProfileError): self.w.execute('replace', {**request, 'target':{**target, 'index':1}})
        self.w.execute('replace', request)
        self.assertEqual(len(remote['profiles']), 2)
        self.assertEqual(remote['profiles'][1], old[1])
        self.assertEqual({c['id'] for c in parse_profile(remote['profiles'][0])['cards']}, {1,2})
        self.assertEqual(parse_profile(remote['profiles'][0])['items'], base['items'])
        self.assertIsNone(self.w.cloud_target)
        with self.assertRaises(ProfileError): self.w.execute('replace', request)

    def test_replacing_after_remote_change_requires_new_base(self):
        old = [json.loads(export_profile(empty_profile('云端'), compressed=True))]
        self.w.client, remote = CloudTests().client(old)
        self.w.execute('cloud_base', self.w.execute('cloud', {})['profiles'][0])
        self.w.execute('preview', {'observations': [card()], 'scope': {'bands':[1], 'members':[1], 'rarities':[1]},
                                  'policy':'max', 'name':'云端'})
        target = self.w.state()['cloud_target']
        remote['profiles'][0]['name'] = '已被另一个设备改名'
        with self.assertRaises(CloudError):
            self.w.execute('replace', {'token':self.w.result_token, 'target':target})
        self.assertIsNone(self.w.cloud_target)
        self.assertEqual(remote['profiles'][0]['name'], '已被另一个设备改名')

    def test_cloud_target_clears_when_base_or_account_changes(self):
        source = json.loads(export_profile(empty_profile('云端'), compressed=True))
        for action, body in [('empty', {}), ('import', {'text':export_profile(empty_profile())}),
                             ('login', {'username':'new', 'password':'bad'}), ('logout', {})]:
            with self.subTest(action=action):
                self.w.client, _ = CloudTests().client([source])
                self.w.execute('cloud_base', self.w.execute('cloud', {})['profiles'][0])
                self.w.client.login = self.w.client.logout = Mock(side_effect=CloudError('失败'))
                try: self.w.execute(action, body)
                except CloudError: pass
                self.assertIsNone(self.w.cloud_target)

    def test_close_during_cloud_operation_waits_and_closed_session_rejects_mutation(self):
        self.w.operation.acquire()
        try:
            with self.assertRaises(ProfileError): self.w.execute('close', {})
            self.assertFalse(self.w.closed.is_set())
        finally:
            self.w.operation.release()
        self.w.execute('close', {})
        with self.assertRaises(ProfileError): self.w.execute('empty', {})


if __name__ == '__main__':
    unittest.main()
