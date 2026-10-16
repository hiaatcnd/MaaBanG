"""Visual first-note probe for the measured 1280x720 stage layout."""
import numpy as np
from functools import lru_cache


@lru_cache(maxsize=7)
def _corridor(lane):
    center_ends = [640+(lane-3)*147.7*y/590 for y in (60,554)]
    left = max(0, int(min(center_ends)-50))
    right = min(1280, int(max(center_ends)+50)+1)
    yy, xx = np.mgrid[60:555, left:right]
    center = 640+(lane-3)*147.7*yy/590
    return left, right, np.abs(xx-center) < 12+yy*.065


def stage_state(image):
    """None before stage; False after health loss; True for an intact start."""
    line = image[587:589,280:1000]
    if np.mean((line[:,:,0]>180) & (line[:,:,1]>180) & (line[:,:,2]<180)) < .7:
        return None
    health = image[36:47,980:1170].astype(float)
    return bool(np.mean((health[:,:,1]>130) & (health[:,:,1]>health[:,:,2]*1.2)) > .9)


def locate_note_y(image, lane, color='cyan'):
    if image.shape != (720, 1280, 3) or not 0 <= lane <= 6:
        raise ValueError('This probe requires the calibrated 1280x720 layout')
    if color == 'skill_green':
        # Separate masks keep a golden head from merging with its green ribbon
        # into an over-height region. FirstNoteLock still enforces identity.
        candidates = [y for appearance in ('yellow', 'green')
                      if (y := locate_note_y(image, lane, appearance)) is not None]
        return max(candidates) if candidates else None
    left, right, corridor = _corridor(lane)
    roi = image[60:555, left:right]
    blue, green, red = roi[:,:,0], roi[:,:,1], roi[:,:,2]
    if color == 'cyan':
        mask = (blue > 190) & (green > 190) & (red < 180)
    elif color == 'green':
        mask = (green > 220) & (blue < 200) & (red < 200)
    elif color == 'orange':
        # SP right arrows use orange, including pale orange edges. Require
        # both chroma differences to exclude gold skills and ordinary flicks.
        mask = ((red > 220) & (green > 110) &
                (red.astype(np.int16)-green > 25) &
                (green.astype(np.int16)-blue > 20))
    elif color == 'purple':
        # SP left arrows are violet rather than the ordinary magenta flick.
        mask = ((blue > 210) & (red > 145) &
                (blue.astype(np.int16)-red > 15) &
                (red.astype(np.int16)-green > 15))
    elif color in ('yellow', 'skill'):
        # Skill taps have a gold head instead of the ordinary cyan head.
        # Require yellow chroma rather than matching white outlines/highlights.
        mask = ((red > 220) & (green > 210) &
                (red.astype(np.int16)-blue > 35) &
                (green.astype(np.int16)-blue > 25))
        if color == 'skill':
            # Online skill activation need not use the chart's solo skill head.
            # Accept its ordinary tap appearance in the same lane; the caller
            # still requires a coherent multi-frame first-note trajectory.
            mask |= (blue > 190) & (green > 190) & (red < 180)
    elif color == 'pink':
        # Flick heads brighten as they approach the line. Their green channel
        # can exceed 230, but magenta chroma still separates them from white.
        # DEPARTURES' simultaneous flicks have a pale center: both red and
        # blue exceed green by about 20, even before reaching the lock region.
        mask = ((red > 200) & (blue > 130) &
                (red.astype(np.int16)-green > 30) &
                (blue.astype(np.int16)-green > 15))
    else:
        raise ValueError('Unsupported first-note color')
    def candidates_for(colors):
        counts = (colors & corridor).sum(axis=1)
        ids = np.where(counts > np.maximum(8, np.arange(60, 555)*.08))[0]
        groups = np.split(ids, np.where(np.diff(ids) > 1)[0]+1)
        return [float(g[np.argmax(counts[g])]+60)
                for g in groups if 1 <= len(g) <= 18]

    candidates = candidates_for(mask)
    if color in ('pink', 'green'):
        if color == 'pink':
            pale = ((red > 230) & (blue > 230) &
                    (red.astype(np.int16)-green > 15) &
                    (blue.astype(np.int16)-green > 15))
        else:
            # Hold heads can brighten past the saturated mask's red ceiling.
            # Keep green chroma to exclude white/cyan heads and yellow effects.
            pale = ((green > 220) & (red >= 200) &
                    (green.astype(np.int16)-blue > 25) &
                    (green.astype(np.int16)-red > 15))
        # Preserve the existing head coordinate when its saturated pixels are
        # visible. Use the pale center only for otherwise missing note heads.
        candidates += [y for y in candidates_for(pale)
                       if not any(abs(y-original) <= 18 for original in candidates)]
    return max(candidates) if candidates else None


class FirstNoteLock:
    """A bounded multi-frame fit, not a calibrated game judgment timestamp."""
    def __init__(self, travel_scale=None):
        self.points = []
        self.travel_scale = travel_scale

    def observe(self, timestamp, y):
        # Once real downward motion is established, never reinterpret a later
        # note as the first note when this candidate disappears.
        armed = len(self.points) >= 3 and self.points[-1][1]-self.points[0][1] >= 20
        if armed and timestamp-self.points[-1][0] > .12:
            # A short missing frame is not proof that the note has passed. With
            # calibrated speed, retain this identity only until its predicted
            # crossing, and require a reappearing candidate to agree in time.
            crossing=None
            if self.travel_scale is not None:
                arrivals=[t+self.travel_scale*np.log(590/pos) for t,pos in self.points[-5:]]
                crossing=float(np.median(arrivals))
            if (crossing is None or timestamp >= crossing-.005 or
                    timestamp-self.points[-1][0] > .30):
                raise ValueError('First note lost before lock')
            if y is not None:
                arrival=timestamp+self.travel_scale*np.log(590/y)
                if abs(arrival-crossing)>.025:
                    raise ValueError('First note moved backwards or changed identity')
        if y is None:
            return None
        if armed and y < self.points[-1][1]-8:
            raise ValueError('First note moved backwards or changed identity')
        if not self.points and y > 180:
            return None
        if self.points and not armed and (y < self.points[-1][1]-8 or timestamp-self.points[-1][0] > .15):
            self.points.clear()
        if not self.points or y > self.points[-1][1]+3:
            self.points.append((timestamp, y))
        if len(self.points) < 5:
            return None
        points = np.array([p for p in self.points if p[1] >= 100][-9:])
        if len(points) < 5:
            return None
        if np.ptp(points[:,1]) < 80:
            return None
        if self.travel_scale is not None and 300 <= y <= 554:
            # Decide before a slow capture can skip the near-line window. Keep
            # the motion/identity checks above; this is not a single-frame gate.
            arrivals=points[-3:,0]+self.travel_scale*np.log(590/points[-3:,1])
            center=float(np.median(arrivals))
            spread=float(np.median(np.abs(arrivals-center)))
            crossing=float(np.min(arrivals))
            # A stale capture biases an arrival late. Prefer the earlier of
            # three agreeing projections, and check every sample: MAD alone
            # can hide a single outlier. Leave enough time for the first touch.
            if (spread <= .018 and np.max(np.abs(arrivals-center)) <= .025 and
                    .030 < crossing-timestamp < .200):
                return {'crossing':crossing,'residual_ms':spread*1000,
                        'points':3,'y':y,'method':'hybrid_early',
                        'travel_scale':self.travel_scale}
            # When capture jitter prevented agreement, a tracked head near
            # the line gives a shorter projection. Identity guards still apply.
            crossing=float(arrivals[-1])
            if y >= 450 and .003 < crossing-timestamp < .080:
                return {'crossing':crossing,'residual_ms':spread*1000,
                        'points':3,'y':y,'method':'hybrid_line',
                        'travel_scale':self.travel_scale}
        if y < 435:
            return None
        if self.travel_scale is not None:
            # A calibrated projection avoids amplifying startup frame jitter
            # into an erroneous fall speed. Runtime multi-note sync refines it.
            arrivals = points[-5:,0]+self.travel_scale*np.log(590/points[-5:,1])
            crossing = float(np.median(arrivals))
            spread = float(np.median(np.abs(arrivals-crossing)))
            if spread > .009 or not .005 < crossing-timestamp < .13:
                return None
            return {'crossing':crossing,'residual_ms':spread*1000,
                    'points':5,'y':y,'method':'calibrated_projection',
                    'travel_scale':self.travel_scale}
        # The observed note accelerates toward the judgment line. Fit time
        # against log(y), validated by replaying both recorded start traces.
        fit = np.polyfit(np.log(points[:,1]), points[:,0], 1)
        crossing = float(np.polyval(fit, np.log(590)))
        residual = float(np.std(np.polyval(fit, np.log(points[:,1]))-points[:,0]))
        if residual >= .009 or not -.015 < crossing-timestamp < .16:
            return None
        return {'crossing': crossing, 'residual_ms': residual*1000,
                'points': len(points), 'y': y}


class ChartPhaseTracker:
    """Match ordinary visible notes to a chart using a calibrated projection.

    Calibrated for the measured 9.80 speed and midpoint capture timestamps.
    An estimate is evidence for a clock correction, never a direct touch.
    """
    def __init__(self, chart, travel_scale=.245, phase_reference=.037):
        from chart_timing import TempoMap
        tempo = TempoMap(chart)
        self.notes = {lane: np.array([tempo.seconds(n['beat']) for n in chart
            if n['type']=='Single' and not n.get('flick') and n['lane']==lane])
            for lane in range(7)}
        self.travel_scale = travel_scale
        self.phase_reference = phase_reference
        self.samples = []
        self.sparse_samples = []

    def origin_from_anchor(self, crossing, chart_time):
        """Use the same judgment-line reference for startup and later notes.

        crossing is an absolute projection onto y=590. A second startup-only
        lead would create an artificial phase error that the tracker must undo.
        """
        return crossing-chart_time-self.phase_reference

    @staticmethod
    def _supported(samples, count, frames):
        return (len(samples) >= count and len({s[0] for s in samples}) >= frames and
                len({(s[1],s[2]) for s in samples}) >= 4 and
                len({s[1] for s in samples}) >= 2)

    def observe(self, timestamp, image, correction=0.):
        positions = [(lane,locate_note_y(image,lane)) for lane in range(7)]
        return self.observe_positions(timestamp,positions,correction)

    def observe_positions(self, timestamp, positions, correction=0.):
        for lane,y in positions:
            notes = self.notes[lane]
            if y is None or not 160 < y < 430 or not len(notes):
                continue
            crossing = timestamp+self.travel_scale*np.log(590/y)
            errors = crossing-notes-self.phase_reference
            distances = np.abs(errors-correction)
            order = np.argsort(distances)
            i = int(order[0])
            if distances[i] > .120:
                continue
            if len(order)>1 and distances[order[1]]-distances[i] < .040:
                continue
            sample=(timestamp,lane,i,float(errors[i]))
            self.sparse_samples.append(sample)
            if distances[i] <= .085:
                self.samples.append(sample)
        self.samples = [s for s in self.samples if timestamp-s[0] <= 2.5]
        self.sparse_samples = [s for s in self.sparse_samples if timestamp-s[0] <= 8.]
        # EASY charts may never put four distinct notes in a 2.5s window.
        # Extend the evidence window only when short-window support is missing;
        # retain four-note/two-lane consensus and the same outlier/MAD checks.
        if self._supported(self.samples,10,5):
            samples,window=self.samples,2.5
        elif self._supported(self.sparse_samples,6,4):
            samples,window=self.sparse_samples,8.
        else:
            return None
        grouped = {}
        for t,lane,i,error in samples:
            grouped.setdefault((lane,i),[]).append(error)
        if len(grouped)<4 or len({key[0] for key in grouped})<2:
            return None
        errors = np.array([np.median(v) for v in grouped.values()])
        center = float(np.median(errors))
        inliers = errors[np.abs(errors-center) < .020]
        if len(inliers)<4 or len(inliers)<len(errors)*.7:
            return None
        estimate = float(np.median(inliers))
        mad = float(np.median(np.abs(inliers-estimate)))
        if mad > .009 or abs(estimate)>.100:
            return None
        return {'correction':estimate,'mad_ms':mad*1000,
                'notes':len(inliers),'samples':len(samples),'window':window}


class PhaseReacquirer:
    """Recheck song time from fresh notes while stalled input is released.

    A host stall does not tell us whether the game kept playing or also paused.
    Search both the old phase and a bounded game delay, requiring an unambiguous
    multi-note match before the caller changes its origin and seeks future heads.
    """
    timeout = 1.25
    max_frame_age = .100

    def __init__(self, tracker, started, correction, stall, *, evidence_since=None):
        if not all(np.isfinite(v) for v in (started, correction, stall)) or not 0 < stall <= 2:
            raise ValueError('Invalid phase recovery')
        evidence_since = started if evidence_since is None else evidence_since
        if not np.isfinite(evidence_since) or evidence_since > started:
            raise ValueError('Invalid phase evidence boundary')
        self.tracker = tracker
        # Admission and timeout use different clocks: captures made during this
        # stall are fresh evidence, but must not consume the new recovery budget.
        self.started = started
        self.evidence_since = evidence_since
        self.low = correction-.050
        self.high = correction+min(.500, max(.250, stall+.150))
        self.rows = []
        self.result = None
        self.last_stamp = float('-inf')

    def observe_positions(self, timestamp, positions, *, evaluate=True, received_at=None):
        received_at = timestamp if received_at is None else received_at
        if (not all(np.isfinite(v) for v in (timestamp, received_at)) or
                received_at < timestamp or timestamp < self.evidence_since or
                received_at-self.started > self.timeout or
                timestamp <= self.last_stamp):
            return None
        self.last_stamp = timestamp
        if self.result is not None:
            return self.result
        # Capture each lane once per frame, even if a diagnostic caller supplies
        # duplicate positions. Duplicates must not manufacture extra evidence.
        for lane, y in dict(positions).items():
            if y is None or not 160 < y < 430 or not len(self.tracker.notes[lane]):
                continue
            crossing = timestamp+self.tracker.travel_scale*np.log(590/y)
            errors = crossing-self.tracker.notes[lane]-self.tracker.phase_reference
            ids = np.where((errors >= self.low) & (errors <= self.high))[0]
            self.rows.append((timestamp, lane, [(int(i), float(errors[i])) for i in ids]))
        self.rows = [r for r in self.rows if timestamp-r[0] <= 1.]
        # A cached batch must be evaluated only after its newest observation;
        # an early match must not hide a later change in the game's phase.
        # OCR or lock contention can delay a live frame just like cached history.
        # Such frames can contribute evidence, but cannot confirm the current phase.
        if not evaluate or received_at-timestamp > self.max_frame_age:
            return None
        # Do not borrow pre-stall samples: they can otherwise keep reporting a
        # reassuring old correction for several seconds after synchronization is lost.
        if len(self.rows) < 6 or len({r[0] for r in self.rows}) < 4:
            return None
        fits = []
        centers = {error for _, _, candidates in self.rows for _, error in candidates}
        for center in centers:
            samples = []
            for stamp, lane, candidates in self.rows:
                matches = [(i, error) for i, error in candidates if abs(error-center) <= .018]
                if len(matches) == 1:
                    i, error = matches[0]
                    samples.append((stamp, lane, i, error))
            if (not ChartPhaseTracker._supported(samples, 6, 4) or
                    not any(s[0] == timestamp for s in samples) or
                    len(samples) < len(self.rows)*.75):
                continue
            grouped = {}
            for _, lane, i, error in samples:
                grouped.setdefault((lane, i), []).append(error)
            errors = np.array([np.median(v) for v in grouped.values()])
            estimate = float(np.median(errors))
            mad = float(np.median(np.abs(errors-estimate)))
            if mad > .009 or np.max(np.abs(errors-estimate)) > .020:
                continue
            fits.append({'correction':estimate, 'mad_ms':mad*1000,
                         'notes':len(grouped), 'samples':len(samples),
                         'frames':len({s[0] for s in samples})})
        if not fits:
            return None
        best = max(fits, key=lambda f:(f['samples'], f['notes'], -f['mad_ms']))
        # Repeating patterns may support two different song positions. A wider
        # search is useful only if it resolves that ambiguity rather than guessing.
        if any(abs(f['correction']-best['correction']) > .040 for f in fits):
            return None
        self.result = best
        return best


class SlewedClock:
    """Bounded continuous phase adjustment; event order remains monotonic."""
    def __init__(self, rate=.010, limit=.100):
        if not 0 < rate < 1 or not 0 < limit <= .1:
            raise ValueError('Unsafe clock correction limits')
        self.rate, self.limit = rate, limit
        self.value = self.target = 0.
        self.previous = None

    def update(self, target):
        if not np.isfinite(target) or abs(target)>self.limit:
            return False
        self.target = float(target)
        return True

    def advance(self, timestamp):
        if self.previous is not None:
            elapsed = max(0.,timestamp-self.previous)
            difference = self.target-self.value
            if abs(difference)>.002:
                self.value += float(np.clip(difference,-self.rate*elapsed,self.rate*elapsed))
        self.previous = timestamp
        return self.value
