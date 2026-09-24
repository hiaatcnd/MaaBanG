import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import tempfile
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
sys.path.insert(0,str(ROOT/'tools'))
from mining_policy import MiningOptions, star_state, can_practice, parse_level, pending_difficulties, material_rois, member_cards, member_portrait_scores, member_signature, same_member_portrait, gold_member_stars, DIFFICULTY_NODES
from mining_live import MiningLiveFlow, ChallengeMiningFlow
from mining_stories import StoryMiningFlow
from chart_policy import ChartSelection
from update_mining_interface import update


class MiningTests(unittest.TestCase):
    def test_story_cancel_waits_for_overlay_to_leave_despite_visible_detail_header(self):
        from costume_unlock import FlowError
        for clears in (True,False):
            f=StoryMiningFlow.__new__(StoryMiningFlow)
            state={'time':0.,'snaps':0}
            f.image=np.zeros((720,1280,3))
            f.snap=lambda:state.update(snaps=state['snaps']+1)
            visible=lambda:not clears or state['snaps']<3
            f.reco=lambda node:visible() if node=='MN_StoryUnlock' else True
            f.hit_text=Mock(return_value=True);f.tap_hit=Mock()
            with patch('mining_stories.time.monotonic',side_effect=lambda:100+state['time']), patch(
                    'mining_stories.time.sleep',side_effect=lambda seconds:state.update(time=state['time']+1.1)), patch(
                    'mining_stories.dialog_box',side_effect=lambda image:(1,2,3,4) if visible() else None):
                if clears:
                    f.cancel_story_unlock()
                    self.assertEqual(f.tap_hit.call_count,2)
                    self.assertEqual(state['snaps'],3)
                else:
                    with self.assertRaisesRegex(FlowError,'未确认取消'):f.cancel_story_unlock()
                    self.assertEqual(f.tap_hit.call_count,3)

    def test_jittered_grid_keeps_each_card_identity_and_row_order(self):
        grids=[np.array(Image.open(ROOT/f'tests/fixtures/mining/selection_jitter_{name}.png'))[:,:,::-1]
               for name in ('before','after')]
        cards=[member_cards(grid) for grid in grids]
        self.assertNotEqual(cards[0][0][1],cards[1][0][1])
        signatures=[[member_signature(grid,point) for point in points] for grid,points in zip(grids,cards)]
        self.assertEqual(len(signatures[0]),13)
        for i,current in enumerate(signatures[1]):
            matches=[j for j,old in enumerate(signatures[0]) if same_member_portrait(current,old)]
            self.assertEqual(matches,[i])
        seen=[]
        for i in range(13):
            current=signatures[i%2]
            selected=next(j for j,sig in enumerate(current) if not any(same_member_portrait(sig,old) for old in seen))
            self.assertEqual(selected,i)
            seen.append(current[selected])

    def test_small_portrait_matches_without_skipping_correlation_peak(self):
        grid=np.array(Image.open(ROOT/'tests/fixtures/mining/selection_sayo_grid.png'))[:,:,::-1].copy()
        detail=np.array(Image.open(ROOT/'tests/fixtures/mining/selection_sayo_detail.png'))[:,:,::-1].copy()
        cards=member_cards(grid);scores=member_portrait_scores(grid,cards,detail)
        selected=next(i for i,p in enumerate(cards) if p[0]==959 and p[1]<300)
        self.assertGreater(scores[selected],.90)
        self.assertGreater(scores[selected]-max(s for i,s in enumerate(scores) if i!=selected),.15)

    def test_recommendation_requires_explicit_success_or_shortage(self):
        from costume_unlock import FlowError
        for state in ('missing','success','unknown'):
            f=ChallengeMiningFlow.__new__(ChallengeMiningFlow)
            f.report={'skipped':[]};f.click=Mock();f.wait=Mock();f.save_frame=Mock()
            f.tap_hit=Mock();f.wait_ready=Mock();f.snap=Mock();f.area_modals=Mock(return_value=False)
            f.reco=Mock(return_value=False)
            f.require_clear_notification_overlay=Mock()
            def hit(roi,pattern):
                return (state=='missing' if '成员不足' in pattern else
                        state=='success' if '已按照' in pattern else True)
            f.hit_text=hit
            if state=='unknown':
                with self.assertRaisesRegex(FlowError,'未确认推荐编组'):f.recommend()
                f.tap_hit.assert_not_called();f.wait_ready.assert_not_called()
            else:
                self.assertEqual(f.recommend(),state=='success')
                f.tap_hit.assert_called_once();f.wait_ready.assert_called_once()

    def test_ineligible_challenge_is_skipped_before_playing_next_challenge(self):
        f=ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        f.mining=MiningOptions.parse({});f.report={'skipped':[]};f.image=np.zeros((720,1280,3))
        for name in ('navigate_menu','click','select_stage_kind','swipe','wait','tap','choose_difficulty_exact','wait_ready','back'):
            setattr(f,name,Mock())
        f.limited=Mock(return_value=False)
        f.challenge_cards=Mock(side_effect=[[(200,np.zeros((12,30)),0,90)],[(300,np.full((12,30),30),0,90)]])
        f.selected_level=Mock(return_value=1);f.text=Mock(return_value='BLACK SHOUT')
        f.store=Mock();f.recommend=Mock(side_effect=[False,True]);f.perform=Mock(return_value=None)
        f.run()
        self.assertEqual(f.recommend.call_count,2)
        f.perform.assert_called_once()
        self.assertEqual(f.report['skipped'][0]['reason'],'insufficient_eligible_members')
        self.assertEqual(f.back.call_count,2)

    def test_recommendation_cannot_return_through_a_stuck_modal(self):
        from costume_unlock import FlowError
        f=ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        f.report={'skipped':[]};f.click=Mock();f.wait=Mock();f.save_frame=Mock()
        f.tap_hit=Mock();f.wait_ready=Mock();f.snap=Mock()
        f.hit_text=Mock(return_value=True);f.reco=Mock(return_value=True)
        with self.assertRaisesRegex(FlowError,'未确认关闭'):f.recommend()
        self.assertEqual(f.tap_hit.call_count,3)
        f.wait_ready.assert_not_called()

    def test_invalid_member_popup_stops_worker_before_first_note_timeout(self):
        from costume_unlock import FlowError
        f=ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        f.snap=Mock();f.hit_text=Mock(return_value=True);f.save_frame=Mock();f.tap=Mock();f.area_modals=Mock()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FlowError,'不符合成员条件'):
                f.monitor_chart_worker(Path(directory))
        f.tap.assert_not_called();f.area_modals.assert_not_called()

    def test_animated_bonus_badge_does_not_hide_a_member(self):
        image=np.array(Image.open(ROOT/'tests/fixtures/mining/selection_badge.png'))[:,:,::-1].copy()
        cards=member_cards(image)
        self.assertEqual([x for x,y in cards if y<300],[344,467,590,713,836,959,1082])

    def test_white_michelle_portrait_with_badge_touching_viewport_is_not_skipped(self):
        image=np.array(Image.open(ROOT/'tests/fixtures/mining/selection_michelle_badge.png'))[:,:,::-1].copy()
        cards=member_cards(image)
        self.assertEqual(len(cards),15)
        self.assertEqual([x for x,y in cards if y<300],[344,467,590,713,836,959,1082])

    def test_trained_portrait_uses_larger_matching_scale(self):
        grid=np.array(Image.open(ROOT/'tests/fixtures/mining/selection_trained_grid.png'))[:,:,::-1].copy()
        detail=np.array(Image.open(ROOT/'tests/fixtures/mining/selection_trained_detail.png'))[:,:,::-1].copy()
        cards=member_cards(grid);scores=member_portrait_scores(grid,cards,detail)
        selected=next(i for i,p in enumerate(cards) if p[0]==590 and p[1]<300)
        self.assertGreater(scores[selected],.75)
        self.assertGreater(scores[selected]-max(s for i,s in enumerate(scores) if i!=selected),.06)

    def test_member_selection_matches_detail_and_rejects_another_card(self):
        from costume_unlock import FlowError
        grid=np.array(Image.open(ROOT/'tests/fixtures/mining/selection_grid.png').convert('RGB'))[:,:,::-1].copy()
        detail=np.array(Image.open(ROOT/'tests/fixtures/mining/selection_detail.png').convert('RGB'))[:,:,::-1].copy()
        cards=member_cards(grid)
        for index in (0,1):
            f=StoryMiningFlow.__new__(StoryMiningFlow)
            f.image=grid.copy(); f.report={};f.save_frame=Mock();f.tap=Mock()
            f.wait=Mock(side_effect=lambda node:setattr(f,'image',detail.copy()))
            f.text=Mock(return_value='北泽育美 熊熊燃烧！');f.snap=Mock()
            if index==0:
                self.assertEqual(f.open_member_verified(cards[index],cards),'北泽育美熊熊燃烧!')
                self.assertEqual(f.report['selections'][0]['status'],'verified')
            else:
                with self.assertRaisesRegex(FlowError,'未与详情唯一匹配'):
                    f.open_member_verified(cards[index],cards)
                f.text.assert_not_called()

    def test_practice_enables_auto_training_before_checking_level_cap(self):
        flow=StoryMiningFlow.__new__(StoryMiningFlow)
        flow.mining=MiningOptions.parse({'practice':True,'stars':'3'})
        flow.report={'members':[{}]}
        flow.text=Mock(side_effect=['1 / 40','1 / 50','50 / 50'])
        flow.rarity=Mock(return_value=3)
        flow.hit_text=Mock(return_value=True)
        flow.checkbox=Mock(side_effect=[False,True])
        flow.tap=Mock();flow.snap=Mock();flow.click=Mock();flow.wait=Mock()
        flow.reco=Mock(return_value=False);flow.save_frame=Mock()
        flow.await_practice=Mock(return_value=True)
        row={}
        self.assertTrue(flow.practice(row,50))
        self.assertEqual(flow.tap.call_args_list[0].args,(831,331))
        flow.await_practice.assert_called_once_with(row,50)
        self.assertEqual(row['maximum'],50)
        self.assertTrue(row['automatic_training'])

    def test_excluded_star_never_enables_auto_training(self):
        flow=StoryMiningFlow.__new__(StoryMiningFlow)
        flow.mining=MiningOptions.parse({'practice':True,'stars':'1,2,3'})
        flow.text=Mock(return_value='1 / 50');flow.rarity=Mock(return_value=4)
        flow.tap=Mock();flow.hit_text=Mock()
        self.assertFalse(flow.practice({},60))
        flow.tap.assert_not_called();flow.hit_text.assert_not_called()

    def test_auto_training_insufficient_materials_cancels_without_submission(self):
        flow=StoryMiningFlow.__new__(StoryMiningFlow)
        flow.image=np.zeros((720,1280,3),dtype=np.uint8)
        flow.snap=Mock();flow.reco=lambda node:node=='MN_AutoTrainingConfirm'
        flow.text=Mock(return_value='2 / 3')
        flow.tap=Mock();flow.wait=Mock();flow.back=Mock();flow.click=Mock()
        row={}
        with patch('mining_stories.material_rois',return_value=[[0,0,100,30]]):
            self.assertFalse(flow.await_practice(row,50))
        flow.click.assert_not_called()
        self.assertNotIn('practice_confirmed',row)
        self.assertEqual(row['status'],'insufficient_or_unreadable_training_materials')

    def test_practice_confirmation_is_submitted_once_and_actual_level_verified(self):
        from costume_unlock import FlowError
        for actual in (40,39):
            flow=StoryMiningFlow.__new__(StoryMiningFlow)
            states=iter(['MN_PracticeConfirm','MN_PracticeConfirm','MN_PracticeSuccess','MN_PracticePage','MN_MemberDetail'])
            state={}
            flow.snap=lambda:state.update(scene=next(states))
            flow.reco=lambda node:node==state['scene']
            flow.click=Mock();flow.back=Mock();flow.wait=Mock()
            flow.text=Mock(side_effect=['40 / 40',f'{actual} / 40'])
            row={}
            with patch('mining_stories.time.sleep'):
                if actual==40:
                    self.assertTrue(flow.await_practice(row,40))
                    self.assertEqual(row['practiced_to'],40)
                else:
                    with self.assertRaisesRegex(FlowError,'练习后等级不符'):flow.await_practice(row,40)
            self.assertEqual([c.args[0] for c in flow.click.call_args_list],['MN_PracticeConfirmOK','MN_PracticeClose'])

    def test_real_empty_member_message_finishes_but_unknown_blank_page_does_not(self):
        import re
        from costume_unlock import FlowError
        for text,empty in [('该筛选条件下无合适的成员。',True),('',False)]:
            flow=StoryMiningFlow.__new__(StoryMiningFlow)
            flow.image=np.full((720,1280,3),255,dtype=np.uint8)
            flow.mining=MiningOptions.parse({'memories':False})
            flow.report={'members':[],'read':0}
            flow.member_list=Mock();flow.filter_unread=Mock();flow.wait=Mock();flow.home=Mock()
            flow.tap=Mock(side_effect=AssertionError('must not click empty grid'))
            flow.hit_text=lambda roi,pattern:re.search(pattern,text)
            if empty:
                flow.run()
                self.assertEqual(flow.report['status'],'finished')
            else:
                with self.assertRaisesRegex(FlowError,'无法确认列表为空'):flow.run()

    def test_large_five_star_is_not_downgraded_to_three(self):
        from types import SimpleNamespace
        image=np.full((720,1280,3),255,dtype=np.uint8)
        image[390:565,118:160]=np.asarray(Image.open(ROOT/'tests/fixtures/mining/member_five_stars.png').convert('RGB'))[:,:,::-1]
        flow=StoryMiningFlow.__new__(StoryMiningFlow)
        flow.image=image
        flow.reco=Mock(return_value=SimpleNamespace(filtered_results=[SimpleNamespace(box=[122,y,31,31]) for y in (407,465,523)]))
        self.assertEqual(gold_member_stars(image),5)
        self.assertEqual(flow.rarity(),5)
        self.assertFalse(can_practice(MiningOptions.parse({'practice':True}),flow.rarity(),1,50,50))

    def test_gold_star_count_ignores_portrait_background_and_rainbow_stars(self):
        for name,count in [('member_three_stars',3),('member_trained_stars',0)]:
            image=np.full((720,1280,3),255,dtype=np.uint8)
            image[390:565,118:160]=np.asarray(Image.open(ROOT/f'tests/fixtures/mining/{name}.png'))[:,:,::-1]
            self.assertEqual(gold_member_stars(image),count)

    def test_story_waits_for_both_rewards_before_returning(self):
        flow=StoryMiningFlow.__new__(StoryMiningFlow)
        flow.report={'members':[{}],'read':0}
        row=flow.report['members'][0]
        state={'time':0.,'rewards':0}
        flow.snap=Mock();flow.save_frame=Mock()
        def scene():
            if state['rewards']==0 or (state['rewards']==1 and state['time']>=.8):
                return 'MN_StoryReward'
            return 'MN_MemberDetail'
        flow.reco=lambda name:name==scene()
        def click(name):
            self.assertEqual(name,'MN_StoryRewardOK')
            state['rewards']+=1
        flow.click=Mock(side_effect=click)
        flow.tap=Mock(side_effect=AssertionError('must not reopen story'))
        with patch('mining_stories.time.monotonic',side_effect=lambda:state['time']),patch(
                'mining_stories.time.sleep',side_effect=lambda seconds:state.update(time=state['time']+seconds)):
            flow.finish_story(False,row)
        self.assertEqual(row['reward_dialogs'],2)
        self.assertEqual(row['status'],'read_reward_confirmed')
        self.assertEqual(flow.report['read'],1)
        self.assertGreaterEqual(state['time'],2.3)

    def member_fixture(self, name):
        image = np.full((720,1280,3),255,dtype=np.uint8)
        image[185:665,285:1140] = np.asarray(Image.open(ROOT/f'tests/fixtures/mining/{name}.png').convert('RGB'))[:,:,::-1]
        return image

    def test_single_level_one_member_without_level_ocr(self):
        image = self.member_fixture('member_single')
        self.assertEqual(member_cards(image),[(344,259)])
        # Erase only the tiny level text; the portrait still locates the card.
        image[295:305,381:391] = 80
        cards = member_cards(image)
        self.assertEqual(len(cards),1)
        self.assertEqual(cards[0][0],344)
        self.assertLess(abs(cards[0][1]-260),5)

    def test_member_grid_ignores_bonus_labels_empty_slots_and_clipped_cards(self):
        image = self.member_fixture('member_grid')
        cards = member_cards(image)
        self.assertEqual(len(cards),21)
        for index,y in enumerate((258,381,504)):
            row=cards[index*7:(index+1)*7]
            self.assertEqual([x for x,_ in row],[344,467,590,713,836,959,1082])
            self.assertTrue(all(abs(cy-y)<3 for _,cy in row))
        shifted=np.full_like(image,255)
        shifted[185:598,285:1140]=image[252:665,285:1140]
        self.assertEqual(len(member_cards(shifted)),14)
        self.assertEqual(member_cards(np.full_like(image,255)),[])

    def test_member_row_order_survives_small_portrait_height_differences(self):
        image=self.member_fixture('member_grid')
        # The old round(y/12) buckets split row two at y=378.
        shifted=image.copy()
        shifted[321:436,298:391]=255
        shifted[317:432,298:391]=image[321:436,298:391]
        cards=member_cards(shifted)
        self.assertEqual([x for x,y in cards if 360<y<400],
                         [344,467,590,713,836,959,1082])

    def test_real_selected_stars(self):
        for filename,expected in [('song_fc',['unplayed','unplayed','full_combo','full_combo','clear']),
                                  ('song_ap',['unplayed','full_combo','full_combo','full_combo'])]:
            image = np.asarray(Image.open(ROOT/f'tests/fixtures/mining/{filename}.png'))[:,:,::-1]
            actual = [star_state(image[10:19,x-425-3:x-425+4])
                      for x in (440,467,494,521,548)[:len(expected)]]
            self.assertEqual(actual,expected)

    def test_limits_and_materials_are_explicit(self):
        options = MiningOptions.parse({})
        self.assertFalse(options.practice)
        self.assertFalse(options.unlock)
        self.assertIsNone(MiningOptions.parse({'max_rounds':''}).max_rounds)
        for value in (0,-1,'1.5',True,1000):
            with self.assertRaises(ValueError):
                MiningOptions.parse({'max_rounds':value})
        for values in ({'practice':'false'},{'stars':'0,3'},{'stage':'other'}):
            with self.assertRaises(ValueError):
                MiningOptions.parse(values)

    def test_practice_checks_star_scope_and_unlock_level(self):
        options = MiningOptions.parse({'practice':True,'stars':'1,3'})
        self.assertTrue(can_practice(options,3,1,50,50))
        self.assertFalse(can_practice(options,4,1,60,60))
        self.assertFalse(can_practice(options,3,1,40,50))
        self.assertFalse(can_practice(options,3,50,50,50))
        self.assertEqual(parse_level('Lv. 1 / 50'),(1,50))
        with self.assertRaises(ValueError):
            parse_level('Lv. 55 / 50')

    def flow(self, limit=2):
        flow = MiningLiveFlow.__new__(MiningLiveFlow)
        flow.mining = MiningOptions.parse({'max_rounds':str(limit)})
        flow.report = dict(attempted=0,completed_rounds=0,full_combos=[],skipped=[])
        flow.home = Mock()
        flow.navigate_menu = Mock()
        flow.open_page = Mock()
        flow.find_song = Mock()
        flow.select_song = Mock()
        flow.wait_ready = Mock()
        return flow

    def test_maximum_counts_non_fc_attempts(self):
        flow = self.flow(2)
        selection = ChartSelection.parse('306','easy')
        flow.next_song = Mock(return_value=[selection])
        flow.song_stars = Mock(return_value={'easy':'unplayed'})
        def perform(_):
            flow.report['attempted'] += 1
            return {}
        flow.perform = Mock(side_effect=perform)
        flow.run()
        self.assertEqual(flow.perform.call_count,2)
        self.assertEqual(flow.report['status'],'max_rounds_reached')
        self.assertEqual(flow.report['full_combos'],[])

    def test_completed_star_stops_retrying(self):
        flow = self.flow(5)
        selection = ChartSelection.parse('306','easy')
        flow.next_song = Mock(side_effect=[[selection],[]])
        flow.song_stars = Mock(side_effect=[{'easy':'unplayed'},{'easy':'full_combo'}])
        def perform(_):
            flow.report['attempted'] += 1
            return {}
        flow.perform = Mock(side_effect=perform)
        flow.run()
        self.assertEqual(flow.perform.call_count,1)
        self.assertEqual(len(flow.report['full_combos']),1)

    def test_interface_generation_is_idempotent(self):
        interface = json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        expected = json.dumps(interface,ensure_ascii=False,sort_keys=True)
        self.assertEqual(json.dumps(update(interface),ensure_ascii=False,sort_keys=True),expected)
        self.assertEqual(len([t for t in interface['task'] if t['entry'].startswith('Mine')]),3)

    def test_locked_next_stage_is_not_selected(self):
        from types import SimpleNamespace
        flow = ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        flow.wait = Mock()
        flow.selected_level = Mock(return_value=8)
        flow.ocr = Mock(return_value=[SimpleNamespace(text='舞台9',box=[40,108,100,25])])
        flow.reco = Mock(return_value=True)
        flow.tap_hit = Mock()
        self.assertFalse(flow.advance_level(8))
        flow.tap_hit.assert_not_called()

    def test_unlocked_next_stage_is_selected_and_verified(self):
        from types import SimpleNamespace
        flow = ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        flow.wait = Mock()
        flow.selected_level = Mock(side_effect=[8,9])
        flow.ocr = Mock(return_value=[SimpleNamespace(text='舞台9',box=[40,108,100,25])])
        flow.reco = Mock(return_value=None)
        flow.tap_hit = Mock()
        self.assertTrue(flow.advance_level(8))
        flow.tap_hit.assert_called_once()

    def test_post_start_popup_monitor_stops_after_notes_start(self):
        flow = ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        flow.snap = Mock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'stage_started').write_text('started')
            flow.monitor_chart_worker(path)
        flow.snap.assert_not_called()

    def test_difficulty_selection_excludes_fc_and_unselected_levels(self):
        states = dict(easy='unplayed',normal='unplayed',hard='full_combo',expert='clear',special='unknown')
        self.assertEqual(pending_difficulties(states,('normal','hard','expert','special')),('normal','expert'))
        self.assertEqual(pending_difficulties(states,()),())
        self.assertEqual(MiningOptions.parse({'difficulties':['expert','easy','expert']}).difficulties,('easy','expert'))
        with self.assertRaises(ValueError):
            MiningOptions.parse({'difficulties':['master']})

    def test_empty_difficulty_selection_does_not_scan_or_play(self):
        flow = self.flow()
        flow.mining = MiningOptions.parse({'difficulties':[]})
        flow.next_song = Mock()
        flow.perform = Mock()
        flow.run()
        flow.next_song.assert_not_called()
        flow.perform.assert_not_called()
        self.assertEqual(flow.report['status'],'no_difficulties_selected')

    def test_checkbox_defaults_and_empty_overrides(self):
        interface = json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        pipeline = json.loads((ROOT/'assets/resource/pipeline/mining.json').read_text(encoding='utf8'))
        option = interface['option']['挖矿自由演出难度']
        self.assertEqual(option['type'],'checkbox')
        self.assertEqual(option['default_case'],[d.upper() for d in DIFFICULTY_NODES])
        self.assertTrue(all(pipeline[node]['attach']['enabled'] is False for node in DIFFICULTY_NODES.values()))
        for case in option['cases']:
            node = DIFFICULTY_NODES[case['name'].lower()]
            self.assertEqual(case['pipeline_override'],{node:{'attach':{'enabled':True}}})

    def test_fire_configuration_validation(self):
        for fire in range(4):
            self.assertEqual(MiningOptions.parse({'fire':fire,'shortage':'items'}).fire,fire)
        for values in ({'fire':4},{'fire':-1},{'fire':True},{'shortage':'stars'}):
            with self.assertRaises(ValueError):
                MiningOptions.parse(values)

    def test_insufficient_fire_does_not_count_or_start_an_attempt(self):
        from types import SimpleNamespace
        flow = self.flow()
        flow.settings = SimpleNamespace(fire=3)
        flow.store = Mock()
        flow.store.get.return_value = (None,{})
        flow.configured = True
        flow.refill_fire = Mock(return_value=False)
        flow.configure_fire = Mock()
        flow.play_chart = Mock()
        self.assertIsNone(flow.perform(ChartSelection.parse('306','easy')))
        flow.refill_fire.assert_called_once_with(3)
        flow.configure_fire.assert_not_called()
        flow.play_chart.assert_not_called()
        self.assertEqual(flow.report['attempted'],0)
        self.assertEqual(flow.report['status'],'insufficient_fire')

    def test_requested_fire_is_used_for_setup_and_start(self):
        from types import SimpleNamespace
        flow = self.flow()
        flow.settings = SimpleNamespace(fire=2)
        flow.store = Mock()
        flow.store.get.return_value = (None,{'duration':100})
        flow.configured = True
        flow.refill_fire = Mock(return_value=True)
        flow.configure_fire = Mock()
        flow.play_chart = Mock()
        flow.await_chart_result = Mock()
        flow.settle_results = Mock()
        flow.report['rounds'] = []
        row = flow.perform(ChartSelection.parse('306','easy'))
        flow.configure_fire.assert_called_once_with(2)
        self.assertEqual(flow.play_chart.call_args.args[3],2)
        self.assertEqual(row['fire'],2)
        self.assertEqual(flow.report['attempted'],1)

    def test_material_slots_are_detected_independently_of_ocr(self):
        image = np.full((720,1280,3),255,dtype=np.uint8)
        image[294:432,380:900] = np.asarray(Image.open(ROOT/'tests/fixtures/mining/materials.png'))[:,:,::-1]
        slots = material_rois(image)
        self.assertEqual(len(slots),3)
        self.assertTrue(all(roi[1]==397 and roi[2]>100 for roi in slots))
        self.assertEqual(material_rois(np.full_like(image,255)),[])

    def test_home_settlement_restores_the_original_challenge(self):
        flow = ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        flow.snap = Mock()
        flow.result_modals = Mock(return_value=False)
        flow.reco = Mock(side_effect=lambda node:node=='CU_HomeBand')
        flow.navigate_menu = Mock()
        flow.click = Mock()
        flow.restore_challenge = Mock()
        flow.settle_results()
        flow.navigate_menu.assert_called_once()
        flow.click.assert_called_once_with('MN_ChallengeEntry')
        flow.restore_challenge.assert_called_once()

    def test_another_challenge_level_is_not_treated_as_progress(self):
        from costume_unlock import FlowError
        flow = ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        flow.wait = Mock()
        flow.selected_level = Mock(return_value=30)
        with self.assertRaises(FlowError):
            flow.advance_level(9)


if __name__ == '__main__':
    unittest.main()
