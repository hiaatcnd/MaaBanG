import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'agent'))
from bestdori_catalog import load
from bestdori_media import CardMedia, skill_descriptions
from bestdori_profiles import ProfileError


class MediaTests(unittest.TestCase):
    def test_full_skill_effect_tracks_actual_level_and_preserves_conditions(self):
        skill = {'description':[None,None,None,'生命回复{0}，{1}秒内得分提升40%，PERFECT时生效'],
                 'duration':[5,5.5,6,6.5,7],
                 'onceEffect':{'onceEffectType':'life','onceEffectValue':[300,330,370,410,450]}}
        values = skill_descriptions(skill)
        self.assertEqual(values[0], '生命回复300，5秒内得分提升40%，PERFECT时生效')
        self.assertEqual(values[4], '生命回复450，7秒内得分提升40%，PERFECT时生效')
        skill.pop('onceEffect'); skill['description'][3] = '{0}秒内，PERFECT时得分提升150%'
        self.assertEqual(skill_descriptions(skill)[1], '5.5秒内，PERFECT时得分提升150%')
        skill['description'][3] = '{2} 未知效果'
        self.assertEqual(skill_descriptions(skill), ['']*5)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.media = CardMedia(folder.name, load())
        self.detail = {'characterId':5, 'rarity':4, 'resourceSetName':'res005004',
                       'skillId':10, 'skillName':[None,None,None,'停下来咯！']}
        data = io.BytesIO(); Image.new('RGB',(180,180),'pink').save(data,format='PNG')
        self.png = data.getvalue()
        self.responses = {'api/cards/20.json':json.dumps(self.detail).encode(),
                          'api/skills/all.10.json':json.dumps({'10':{'simpleDescription':[None,None,None,'生命回复(特大)&得分提升40%']}}).encode(),
                          'assets/cn/thumb/chara/card00000_rip/res005004_after_training.png':self.png}
        self.media.fetch = Mock(side_effect=lambda path:self.responses[path])

    def test_public_media_is_resolved_by_id_and_cached(self):
        result = self.media.get(20,True)
        self.assertEqual(result['skill_name'],'停下来咯！')
        self.assertIn('40%',result['skill_summary'])
        with Image.open(io.BytesIO(base64.b64decode(result['image']))) as art:
            self.assertEqual(art.size,(180,180))
        self.assertEqual(self.media.get(20,True),result)
        self.assertEqual(self.media.fetch.call_count,3)
        self.assertFalse(any('Cookie' in type(h).__name__ for h in self.media.opener.handlers))

    def test_bad_or_mismatched_identity_never_fetches_another_card(self):
        for value in (None,True,'20',-1,999999):
            with self.assertRaises(ProfileError): self.media.get(value,True)
        self.media.fetch.assert_not_called()
        self.detail['characterId']=1
        self.responses['api/cards/20.json']=json.dumps(self.detail).encode()
        with self.assertRaisesRegex(ProfileError,'不一致'): self.media.get(20,False)

    def test_missing_artwork_keeps_skill_reference(self):
        def fetch(path):
            if path.endswith('.png'): raise OSError('offline')
            return self.responses[path]
        self.media.fetch.side_effect=fetch
        result=self.media.get(20,True)
        self.assertIsNone(result['image'])
        self.assertIn('image_error',result)
        self.assertEqual(result['skill_name'],'停下来咯！')

    def test_html_asset_and_corrupt_cache_fall_back_to_public_thumbnail(self):
        path=self.media.root/'thumbnails/20-1.png'
        path.parent.mkdir(parents=True);path.write_bytes(b'corrupt')
        self.responses['assets/cn/thumb/chara/card00000_rip/res005004_after_training.png']=b'<!DOCTYPE html><html></html>'
        self.responses['assets/jp/thumb/chara/card00000_rip/res005004_after_training.png']=self.png
        self.assertEqual(base64.b64decode(self.media.get(20)['image']),self.png)
        self.assertEqual(path.read_bytes(),self.png)

    def test_zero_training_bonus_five_star_still_uses_trained_thumbnail(self):
        self.media.catalog['cards']['20']['trainingLevel']=0
        self.assertIsNotNone(self.media.get(20)['image'])
        self.assertIn('after_training',self.media.fetch.call_args.args[0])

    def test_both_variants_are_cached_and_single_kirafes_is_not_a_failure(self):
        self.responses['assets/cn/thumb/chara/card00000_rip/res005004_normal.png']=self.png
        result=self.media.get(20,both=True)
        self.assertEqual([v['trained'] for v in result['images']],[False,True])
        calls=self.media.fetch.call_count
        self.assertEqual(self.media.get(20,both=True),result)
        self.assertEqual(self.media.fetch.call_count,calls)
        self.detail['type']='kirafes'
        (self.media.root/'cards/20.json').write_text(json.dumps(self.detail),encoding='utf8')
        self.assertEqual(len(self.media.get(20,both=True)['images']),1)

    def test_unsafe_resource_path_rejected(self):
        self.detail['resourceSetName']='../../account'
        self.responses['api/cards/20.json']=json.dumps(self.detail).encode()
        with self.assertRaisesRegex(ProfileError,'资源名称无效'):self.media.get(20,False)


if __name__=='__main__':unittest.main()
