import sys
import json
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'agent'))
from chart_sync import FirstNoteLock, locate_note_y, stage_state, ChartPhaseTracker, SlewedClock


class ChartSyncTests(unittest.TestCase):
    def test_online_skill_hold_accepts_gold_and_green_but_not_other_heads(self):
        for color in ([50,245,255],[80,255,100],[180,255,220]):
            with self.subTest(color=color):
                lock=FirstNoteLock(travel_scale=.245)
                result=None
                for y in (70,95,125,165,215,275,335):
                    frame=np.zeros((720,1280,3),dtype=np.uint8)
                    frame[y:y+5,620:660]=color
                    observed=locate_note_y(frame,3,'skill_green')
                    self.assertEqual(observed,y)
                    result=lock.observe(2-.245*np.log(590/y),observed)
                    if result:break
                self.assertIsNotNone(result)
                self.assertAlmostEqual(result['crossing'],2)
        for color in ([255,255,255],[255,255,100],[253,230,254]):
            frame=np.zeros((720,1280,3),dtype=np.uint8)
            frame[390:395,600:680]=color
            self.assertIsNone(locate_note_y(frame,3,'skill_green'))

    def test_online_skill_hold_does_not_merge_ribbon_or_switch_to_tail(self):
        lock=FirstNoteLock(travel_scale=.245)
        for y in (100,125,160,200):
            frame=np.zeros((720,1280,3),dtype=np.uint8)
            frame[y-30:y,620:660]=[80,255,100]
            frame[y:y+5,620:660]=[50,245,255]
            observed=locate_note_y(frame,3,'skill_green')
            self.assertEqual(observed,y)
            self.assertIsNone(lock.observe(2-.245*np.log(590/y),observed))
        frame=np.zeros((720,1280,3),dtype=np.uint8)
        frame[100:105,620:660]=[80,255,100]
        with self.assertRaisesRegex(ValueError,'changed identity'):
            lock.observe(2-.245*np.log(590/240),locate_note_y(frame,3,'skill_green'))

    def test_recorded_skill_hold_locks_from_its_head_before_green_tail(self):
        from PIL import Image
        from chart_timing import first_anchor
        root=Path(__file__).parent/'fixtures/chart_sync'
        data=json.loads((root/'eat_past_skill_hold.json').read_text(encoding='utf8'))
        for online in (False,True):
            _,lane,color=first_anchor(data['chart'],online=online)
            lock=FirstNoteLock(travel_scale=.245)
            result=None
            for row in data['trace']:
                y=row['y']
                if 'image' in row:
                    frame=np.asarray(Image.open(root/row['image']).convert('RGB'))[:,:,::-1].copy()
                    self.assertEqual(locate_note_y(frame,lane,color),y)
                    self.assertIsNone(locate_note_y(frame,lane,'green'))
                result=lock.observe(row['time'],y)
                if result:break
            self.assertIsNotNone(result)
            self.assertGreater(result['crossing']-row['time'],.140)

    def test_recorded_online_skill_head_uses_cyan_appearance(self):
        from PIL import Image
        frame=np.asarray(Image.open(Path(__file__).parent/
            'fixtures/chart_sync/peak_online_skill.png').convert('RGB'))[:,:,::-1].copy()
        self.assertIsNone(locate_note_y(frame,0,'yellow'))
        self.assertEqual(locate_note_y(frame,0,'cyan'),307)
        self.assertEqual(locate_note_y(frame,0,'skill'),307)

    def test_recorded_yellow_skill_head_locks_before_crossing(self):
        from PIL import Image
        root=Path(__file__).parent/'fixtures/chart_sync'
        trace=json.loads((root/'peak_first_skill.json').read_text(encoding='utf8'))
        lock=FirstNoteLock(travel_scale=.245)
        result=None
        for row in trace:
            y=row['y']
            if 'image' in row:
                frame=np.asarray(Image.open(root/row['image']).convert('RGB'))[:,:,::-1].copy()
                self.assertEqual(locate_note_y(frame,0,'yellow'),y)
                self.assertEqual(locate_note_y(frame,0,'skill'),y)
                self.assertIsNone(locate_note_y(frame,0,'cyan'))
            result=lock.observe(row['time'],y)
            if result:break
        self.assertIsNotNone(result)
        self.assertGreater(result['crossing']-row['time'],.1)

    def test_yellow_head_does_not_match_white_cyan_green_or_pink(self):
        frame=np.zeros((720,1280,3),dtype=np.uint8)
        for color in ([255,255,255],[255,255,100],[80,255,100],[253,230,254]):
            frame[390:395,600:680]=color
            self.assertIsNone(locate_note_y(frame,3,'yellow'))
        frame[390:395,600:680]=[50,245,255]
        self.assertEqual(locate_note_y(frame,3,'yellow'),390)

    def test_online_skill_anchor_tracks_both_tap_appearances(self):
        for color in ([50,245,255],[255,255,100]):
            with self.subTest(color=color):
                lock=FirstNoteLock(travel_scale=.245)
                result=None
                for y in (70,95,125,165,215,275,335):
                    frame=np.zeros((720,1280,3),dtype=np.uint8)
                    frame[y:y+5,625:655]=color
                    observed=locate_note_y(frame,3,'skill')
                    self.assertEqual(observed,y)
                    result=lock.observe(2-.245*np.log(590/y),observed)
                    if result:break
                self.assertIsNotNone(result)
                self.assertAlmostEqual(result['crossing'],2)
        for color in ([255,255,255],[80,255,100],[253,230,254]):
            frame=np.zeros((720,1280,3),dtype=np.uint8)
            frame[390:395,600:680]=color
            self.assertIsNone(locate_note_y(frame,3,'skill'))

    def test_startup_and_observed_notes_share_one_phase_reference(self):
        chart=[{'type':'BPM','beat':0,'bpm':60}]+[
            {'type':'Single','beat':2+i*.2,'lane':i%3} for i in range(12)]
        for reference in (.025,.037,.050):
            tracker=ChartPhaseTracker(chart,phase_reference=reference)
            origin=tracker.origin_from_anchor(100+2+reference,2)
            self.assertAlmostEqual(origin,100.)
            estimates=[]
            for timestamp in np.arange(1.7,4.3,.035):
                positions=[]
                for lane,notes in tracker.notes.items():
                    for note in notes:
                        y=590*np.exp((timestamp-note-reference)/tracker.travel_scale)
                        if 160<y<430:positions.append((lane,y))
                estimate=tracker.observe_positions(timestamp,positions)
                if estimate:estimates.append(estimate)
            self.assertTrue(estimates)
            self.assertAlmostEqual(estimates[0]['correction'],0.,places=7)

    def test_team_capture_jitter_locks_before_the_near_line_frame(self):
        root=Path(__file__).parent/'fixtures/chart_sync'
        for name in ('team_scary_capture_jitter','scary_skipped_gate'):
            with self.subTest(trace=name):
                trace=json.loads((root/f'{name}.json').read_text(encoding='utf8'))['trace']
                lock=FirstNoteLock(travel_scale=.245)
                result=None
                for row in trace:
                    result=lock.observe(row['time'],row['y'])
                    if result:break
                self.assertIsNotNone(result)
                self.assertLess(result['y'],400)
                # Reserve time for capture completion and the first SDK dispatch.
                remaining=result['crossing']-row['time']-row['capture_ms']/2000
                self.assertGreater(remaining,.050)

    def test_latest_projection_outlier_is_not_hidden_by_small_mad(self):
        lock=FirstNoteLock(travel_scale=.245)
        for y in (100,140,180,230,290):
            self.assertIsNone(lock.observe(2.-.245*np.log(590/y),y))
        # The last sample is 40ms late while the other two agree perfectly.
        # Median absolute deviation alone is zero and cannot reject it.
        self.assertIsNone(lock.observe(2.-.245*np.log(590/360)+.040,360))

    def test_unstable_early_blue_lock_waits_for_more_evidence(self):
        root=Path(__file__).parent/'fixtures/chart_sync'
        trace=json.loads((root/'teardrops_unstable_early_lock.json').read_text(encoding='utf8'))['trace']
        lock=FirstNoteLock(travel_scale=.245)
        # An 18ms early-lock tolerance accepted this recorded 16.5ms spread;
        # the resulting live run had 26 GOOD and 2 BAD. Do not commit this fit.
        for row in trace:
            self.assertIsNone(lock.observe(row['time'],row['y']))

    def test_recorded_capture_jump_locks_before_unreliable_late_frame(self):
        root=Path(__file__).parent/'fixtures/chart_sync'
        trace=json.loads((root/'scary_latest_outlier.json').read_text(encoding='utf8'))['trace']
        lock=FirstNoteLock(travel_scale=.245)
        result=None
        for row in trace:
            result=lock.observe(row['time'],row['y'])
            if result:break
        self.assertIsNotNone(result)
        self.assertLess(result['y'],400)
        self.assertGreater(result['crossing']-row['time'],.080)

    def test_yakusoku_bright_hold_keeps_first_note_identity(self):
        from PIL import Image
        root=Path(__file__).parent/'fixtures/chart_sync'
        trace=json.loads((root/'yakusoku_first_hold.json').read_text())
        lock=FirstNoteLock(travel_scale=.245)
        fitted=None
        for row in trace:
            y=row['y']
            if 'image' in row:
                frame=np.array(Image.open(root/row['image']))[:,:,::-1].copy()
                y=locate_note_y(frame,0,'green')
                # Previously the first bright head at 220 became a later one at 92.
                self.assertAlmostEqual(y,row['y'],delta=2)
                self.assertAlmostEqual(locate_note_y(frame,0,'skill_green'),row['y'],delta=2)
            fitted=lock.observe(row['t'],y)
            if fitted:break
        self.assertIsNotNone(fitted)
        self.assertLessEqual(fitted['residual_ms'],9)
        self.assertGreater(fitted['crossing']-row['t'],.04)

    def test_bright_green_fallback_excludes_other_colors_and_preserves_head(self):
        frame=np.zeros((720,1280,3),dtype=np.uint8)
        for color in ([255,255,255],[230,240,230],[255,255,100],
                      [80,255,255],[253,230,254]):
            frame[390:395,600:680]=color
            self.assertIsNone(locate_note_y(frame,3,'green'))
        frame[390:395,600:680]=[180,255,220]
        self.assertEqual(locate_note_y(frame,3,'green'),390)
        frame[380:385,600:680]=[80,255,100]
        self.assertEqual(locate_note_y(frame,3,'green'),380)

    def test_departures_pale_flick_keeps_first_note_identity(self):
        from PIL import Image
        root=Path(__file__).parent/'fixtures/chart_sync'
        trace=json.loads((root/'departures_first_flick.json').read_text())
        lock=FirstNoteLock(travel_scale=.245)
        fitted=None
        for row in trace:
            y=row['y']
            if 'image' in row:
                frame=np.array(Image.open(root/row['image']))[:,:,::-1].copy()
                y=locate_note_y(frame,0,'pink')
                # The old detector switched to the later flick at y=129.
                self.assertAlmostEqual(y,row['y'],delta=2)
                self.assertAlmostEqual(locate_note_y(frame,6,'pink'),y,delta=10)
            fitted=lock.observe(row['t'],y)
            if fitted:break
        self.assertIsNotNone(fitted)
        self.assertLessEqual(fitted['residual_ms'],9)
        self.assertGreater(fitted['crossing']-row['t'],.04)

    def test_pale_flick_fallback_excludes_white_and_other_note_colors(self):
        frame=np.zeros((720,1280,3),dtype=np.uint8)
        for color in ([255,255,255],[240,240,240],[255,250,255],
                      [255,255,100],[80,255,100]):
            frame[390:395,600:680]=color
            self.assertIsNone(locate_note_y(frame,3,'pink'))
        frame[390:395,600:680]=[253,230,254]
        self.assertEqual(locate_note_y(frame,3,'pink'),390)

    def test_real_bright_flick_heads_remain_visible_near_line(self):
        from PIL import Image
        root=Path(__file__).parent/'fixtures/chart_sync'
        for name,expected in (('flick_411.png',411),('flick_440.png',440)):
            frame=np.array(Image.open(root/name))[:,:,::-1].copy()
            self.assertAlmostEqual(locate_note_y(frame,6,'pink'),expected,delta=2)

    def test_real_flick_sequence_locks_with_original_timing_tolerance(self):
        from PIL import Image
        root=Path(__file__).parent/'fixtures/chart_sync'
        trace=json.loads((root/'first_flick_sparse.json').read_text())
        lock=FirstNoteLock(travel_scale=.245)
        fitted=None
        for row in trace:
            y=row['y']
            if 'image' in row:
                frame=np.array(Image.open(root/row['image']))[:,:,::-1].copy()
                y=locate_note_y(frame,6,'pink')
            fitted=lock.observe(row['t'],y)
            if fitted:break
        self.assertIsNotNone(fitted)
        self.assertLessEqual(fitted['residual_ms'],9)
        self.assertGreater(fitted['crossing']-row['t'],.04)

    def test_white_loading_page_is_not_a_stage(self):
        self.assertIsNone(stage_state(np.full((720,1280,3),255,dtype=np.uint8)))

    def test_static_feature_never_locks(self):
        lock = FirstNoteLock()
        for i in range(20):
            self.assertIsNone(lock.observe(i*.02, 450))

    def test_missing_and_empty_frames(self):
        self.assertIsNone(locate_note_y(np.zeros((720,1280,3), dtype=np.uint8), 2))
        self.assertIsNone(FirstNoteLock().observe(0, None))

    def test_green_head_is_distinct_from_taps_and_white_background(self):
        frame = np.zeros((720,1280,3), dtype=np.uint8)
        frame[195:201,620:660] = [80,255,100]
        frame[250:254,620:660] = [255,255,100]
        frame[300:304,620:660] = [255,255,255]
        self.assertEqual(locate_note_y(frame,3,'green'),195)
        self.assertEqual(locate_note_y(frame,3),250)
        self.assertIsNone(locate_note_y(frame,3,'pink'))
        frame[350:356,610:670] = [210,80,255]
        self.assertEqual(locate_note_y(frame,3,'pink'),350)
        with self.assertRaises(ValueError): locate_note_y(frame,3,'unknown')

    def test_time_gap_resets_track(self):
        lock = FirstNoteLock()
        lock.observe(0, 100); lock.observe(.03, 110); lock.observe(1, 150)
        self.assertEqual(lock.points, [(1,150)])

    def test_monotone_motion_can_lock(self):
        lock = FirstNoteLock(); result = None
        for y in range(100, 501, 50):
            result = lock.observe(.15*np.log(y), y)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['crossing'], .15*np.log(590), places=6)

    def test_lost_first_note_cannot_lock_to_a_later_note(self):
        lock = FirstNoteLock()
        for t,y in [(0,100),(.02,120),(.04,145)]:
            lock.observe(t,y)
        with self.assertRaisesRegex(ValueError, 'First note lost'):
            lock.observe(.3,100)

    def test_recorded_sparse_first_note_locks_before_later_notes(self):
        # Real regression trace: the old polynomial missed this first note,
        # then incorrectly locked a later same-lane note about 11 seconds later.
        trace = [(10.406,60),(10.446,70),(10.491,76),(10.537,91),
                 (10.580,128),(10.625,198),(10.658,222),(10.698,316),(10.744,456)]
        lock = FirstNoteLock(); result = None
        for timestamp,y in trace:
            result = lock.observe(timestamp,y)
            if result:
                break
        self.assertIsNotNone(result)
        self.assertGreater(result['crossing'], trace[-1][0])
        self.assertLess(result['crossing'], 10.85)

    def test_calibrated_first_note_does_not_refit_speed_to_capture_jitter(self):
        lock = FirstNoteLock(travel_scale=.245)
        crossing = 2.
        result = None
        for i,y in enumerate(range(100,501,25)):
            stamp = crossing-.245*np.log(590/y)+(i%3-1)*.002
            result = lock.observe(stamp,y)
            if result: break
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['crossing'], crossing, delta=.003)

    def test_calibrated_lock_survives_short_dropout_before_crossing(self):
        lock=FirstNoteLock(travel_scale=.245)
        stamp=lambda y: 2.-.245*np.log(590/y)
        for y in (100,125,150,200):
            lock.observe(stamp(y),y)
        self.assertIsNone(lock.observe(stamp(200)+.13,None))
        result=None
        for y in (370,400,435,470):
            result=lock.observe(stamp(y),y)
            if result:break
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['crossing'],2.)

    def test_calibrated_dropout_does_not_accept_a_later_note(self):
        for late_time,y in ((1.88,120),(2.01,None)):
            lock=FirstNoteLock(travel_scale=.245)
            for pos in (100,125,150,200):
                lock.observe(2.-.245*np.log(590/pos),pos)
            with self.assertRaises(ValueError):
                lock.observe(late_time,y)

    def test_multiple_chart_notes_estimate_phase_with_capture_noise(self):
        chart = [{'type':'BPM','beat':0,'bpm':60}]+[
            {'type':'Single','beat':1+i*.13,'lane':i%3} for i in range(30)]
        tracker = ChartPhaseTracker(chart)
        result = None
        for k,t in enumerate(np.arange(.65,3.5,.06)):
            positions=[]
            for lane,notes in tracker.notes.items():
                for note in notes:
                    y=590*np.exp((t-note-.037+.040+(k%3-1)*.003)/.245)
                    if 160<y<430:positions.append((lane,y))
            new=tracker.observe_positions(t,positions,-.04)
            if new:result=new
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['correction'],-.040,delta=.004)
        self.assertGreaterEqual(result['notes'],4)

    def test_recorded_sparse_song_acquires_phase_without_dense_bursts(self):
        root=Path(__file__).parent/'fixtures/chart_sync'
        data=json.loads((root/'teardrops_sparse_phase.json').read_text(encoding='utf8'))
        tracker=ChartPhaseTracker(data['chart'])
        hits=[]
        for row in data['trace']:
            result=tracker.observe_positions(row['time'],row['positions'])
            if result:hits.append((row['time'],result))
        self.assertTrue(hits)
        self.assertLess(hits[0][0],15)
        self.assertAlmostEqual(hits[0][1]['correction'],-.070,delta=.008)
        self.assertGreaterEqual(hits[0][1]['notes'],4)

    def test_sparse_phase_requires_multiple_lanes_and_expires(self):
        for multiple_lanes in (False,True):
            chart=[{'type':'BPM','beat':0,'bpm':60}]+[
                {'type':'Single','beat':1+i*2,'lane':i%2 if multiple_lanes else 0}
                for i in range(4)]
            tracker=ChartPhaseTracker(chart);results=[]
            for i in range(4):
                for y in (230,300):
                    stamp=1+i*2+.037-.095-.245*np.log(590/y)
                    result=tracker.observe_positions(stamp,[(i%2 if multiple_lanes else 0,y)])
                    if result:results.append(result)
            if multiple_lanes:
                self.assertTrue(results)
                self.assertAlmostEqual(results[-1]['correction'],-.095)
            else:self.assertFalse(results)
            self.assertIsNone(tracker.observe_positions(30,[]))
            self.assertEqual(tracker.samples,[])

    def test_single_note_cannot_adjust_clock_and_samples_expire(self):
        tracker=ChartPhaseTracker([{'type':'BPM','beat':0,'bpm':60},
                                  {'type':'Single','beat':1,'lane':3}])
        for t in np.arange(.7,.9,.01):
            y=590*np.exp((t-1-.037)/.245)
            self.assertIsNone(tracker.observe_positions(t,[(3,y)]))
        self.assertIsNone(tracker.observe_positions(5,[]))
        self.assertEqual(tracker.samples,[])

    def test_ambiguous_chart_candidates_are_not_accumulated(self):
        tracker=ChartPhaseTracker([{'type':'BPM','beat':0,'bpm':60},
                                  {'type':'Single','beat':1,'lane':3},
                                  {'type':'Single','beat':1.04,'lane':3}])
        y=250
        timestamp=1.02+.037-.245*np.log(590/y)
        for _ in range(20):
            self.assertIsNone(tracker.observe_positions(timestamp,[(3,y)]))
        self.assertEqual(tracker.samples,[])

    def test_clock_correction_is_bounded_and_slews_in_both_directions(self):
        clock=SlewedClock()
        self.assertFalse(clock.update(float('nan')))
        self.assertFalse(clock.update(.2))
        clock.advance(0)
        self.assertTrue(clock.update(-.060))
        self.assertAlmostEqual(clock.advance(1),-.010)
        self.assertAlmostEqual(clock.advance(2),-.020)
        self.assertTrue(clock.update(.030))
        self.assertAlmostEqual(clock.advance(3),-.010)


if __name__ == '__main__':
    unittest.main()
