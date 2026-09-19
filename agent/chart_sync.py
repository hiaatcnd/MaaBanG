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
    left, right, corridor = _corridor(lane)
    roi = image[60:555, left:right]
    blue, green, red = roi[:,:,0], roi[:,:,1], roi[:,:,2]
    if color == 'cyan':
        mask = (blue > 190) & (green > 190) & (red < 180)
    elif color == 'green':
        mask = (green > 220) & (blue < 200) & (red < 200)
    elif color == 'pink':
        mask = (red > 200) & (blue > 130) & (green < 180)
    else:
        raise ValueError('Unsupported first-note color')
    counts = (mask & corridor).sum(axis=1)
    ids = np.where(counts > np.maximum(8, np.arange(60, 555)*.08))[0]
    if not len(ids):
        return None
    groups = np.split(ids, np.where(np.diff(ids) > 1)[0]+1)
    candidates = [float(g[np.argmax(counts[g])]+60)
                  for g in groups if 1 <= len(g) <= 18]
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
        if len(self.points) < 5 or y < 435:
            return None
        points = np.array([p for p in self.points if p[1] >= 100][-9:])
        if len(points) < 5:
            return None
        if np.ptp(points[:,1]) < 80:
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
            if distances[i] > .085:
                continue
            if len(order)>1 and distances[order[1]]-distances[i] < .040:
                continue
            self.samples.append((timestamp,lane,i,float(errors[i])))
        self.samples = [s for s in self.samples if timestamp-s[0] <= 2.5]
        if len(self.samples)<10 or len({s[0] for s in self.samples})<5:
            return None
        grouped = {}
        for t,lane,i,error in self.samples:
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
        if mad > .009 or abs(estimate)>.080:
            return None
        return {'correction':estimate,'mad_ms':mad*1000,
                'notes':len(inliers),'samples':len(self.samples)}


class SlewedClock:
    """Bounded continuous phase adjustment; event order remains monotonic."""
    def __init__(self, rate=.010, limit=.080):
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
