import sys
import json
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'agent'))
from chart_sync import FirstNoteLock, locate_note_y, stage_state, ChartPhaseTracker, SlewedClock


class ChartSyncTests(unittest.TestCase):
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
