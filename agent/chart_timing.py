"""Tempo conversion and deterministic gestures for chart-driven live playback."""
from bisect import bisect_right
from dataclasses import dataclass
import math
import random


class TempoMap:
    def __init__(self, chart):
        # Bestdori stable-sorts BPM events by beat and selects the last record at
        # each beat. Official song 667 has two different BPM records at beat 0.
        by_beat = {}
        for note in chart:
            if note['type'] != 'BPM':
                continue
            beat, bpm = float(note['beat']), float(note['bpm'])
            if not math.isfinite(beat) or beat<0 or not math.isfinite(bpm) or bpm<=0:
                raise ValueError('Invalid tempo')
            by_beat[beat] = bpm
        changes = sorted(by_beat.items())
        if not changes or changes[0][0] != 0:
            raise ValueError('An initial BPM at beat zero is required')
        self.beats, self.bpms, self.times = [], [], []
        for beat, bpm in changes:
            if not math.isfinite(beat) or not math.isfinite(bpm) or bpm <= 0:
                raise ValueError('Invalid tempo')
            if self.beats and beat <= self.beats[-1]:
                raise ValueError('Duplicate tempo change')
            elapsed = (self.times[-1] + (beat-self.beats[-1])*60/self.bpms[-1]
                       if self.beats else 0.)
            self.beats.append(beat); self.bpms.append(bpm); self.times.append(elapsed)

    def seconds(self, beat):
        beat = float(beat)
        if not math.isfinite(beat) or beat < 0:
            raise ValueError('Invalid beat')
        i = bisect_right(self.beats, beat)-1
        return self.times[i] + (beat-self.beats[i])*60/self.bpms[i]


@dataclass(frozen=True, order=True)
class TouchEvent:
    time: float
    priority: int  # release before reusing a contact at the same timestamp
    contact: int
    lane: int
    action: str


def compile_basic_chart(chart, tap_duration=.045, max_contacts=2):
    """Compile Single and stationary Long only; reject other gestures before playing."""
    if not math.isfinite(tap_duration) or tap_duration <= 0 or max_contacts < 1:
        raise ValueError('Invalid touch configuration')
    tempo = TempoMap(chart)
    gestures = []
    for note in chart:
        kind = note['type']
        if kind in ('BPM', 'System'):
            continue
        if kind not in ('Single', 'Long'):
            raise ValueError(f'Unsupported gesture: {kind}')
        points = [note] if kind == 'Single' else note['connections']
        if kind == 'Long' and len(points) != 2:
            raise ValueError('Only two-point stationary Long is supported')
        lane = points[0]['lane']
        if type(lane) is not int or not 0 <= lane <= 6:
            raise ValueError('Invalid lane')
        if any(p.get('flick') or p.get('hidden') or p['lane'] != lane for p in points):
            raise ValueError('Unsupported moving/hidden/flick gesture')
        start = tempo.seconds(points[0]['beat'])
        end = tempo.seconds(points[-1]['beat']) if kind == 'Long' else start+tap_duration
        if end <= start:
            raise ValueError('Invalid hold duration')
        gestures.append((start, end, lane))
    if not gestures:
        raise ValueError('Empty chart')
    available = [float('-inf')]*max_contacts
    lane_ends = [float('-inf')]*7
    result = []
    for start, end, lane in sorted(gestures):
        if lane_ends[lane] > start:
            raise ValueError('Overlapping gestures on the same lane')
        contact = next((i for i, t in enumerate(available) if t <= start), None)
        if contact is None:
            raise ValueError('Insufficient contacts')
        available[contact] = end; lane_ends[lane] = end
        result.extend((TouchEvent(start, 1, contact, lane, 'down'),
                       TouchEvent(end, 0, contact, lane, 'up')))
    return sorted(result)


@dataclass(frozen=True, order=True)
class GestureEvent:
    time: float
    priority: int
    contact: int
    lane: float
    action: str
    y: float = 590.
    dx: float = 0.


def first_anchor(chart):
    """Return an unjittered chart head, preferring a tap in an opening chord."""
    tempo = TempoMap(chart)
    heads = []
    for note in chart:
        if note['type'] in ('BPM', 'System'):
            continue
        p = note.get('connections', [note])[0]
        color = ('pink' if p.get('flick') or note['type'] == 'Directional'
                 else 'green' if 'connections' in note else 'cyan')
        heads.append((tempo.seconds(p['beat']), color != 'cyan', p['lane'], color))
    if not heads:
        raise ValueError('Empty chart')
    t, _, lane, color = min(heads)
    return t, lane, color


def bounded_normal(rng, bound):
    """Zero-centered normal with sigma=bound/3, conditioned on +/- bound.

    Reject tails instead of clipping them into artificial spikes at the limits.
    Zero remains available to offline probes; user-facing profiles are nonzero.
    """
    if not math.isfinite(bound) or bound < 0:
        raise ValueError('Invalid normal jitter bound')
    if bound == 0:
        return 0.
    while True:
        sample=rng.gauss(0.,bound/3.)
        if -bound <= sample <= bound:
            return sample


def compile_chart(chart, *, seed=0, jitter_ms=0., position_jitter=0.,
                  tap_duration=.025, move_interval=.008, flick_duration=.056,
                  max_contacts=10, slide_motion_delay=0.):
    """Experimental full gestures; a seed reproduces bounded per-gesture jitter.

    Hidden points shape the path without producing extra touch downs. Knots use
    BPM-integrated chart times; optional slide movement calibration delays only
    motion and release, preserving head timing. One random timing offset is
    shared by heads in a chord and by the whole corresponding gesture.
    Game judgment accuracy still requires device calibration and result checks.
    """
    if not (math.isfinite(jitter_ms) and 0 <= jitter_ms <= 180 and
            math.isfinite(position_jitter) and 0 <= position_jitter <= 22 and
            math.isfinite(tap_duration) and 0 < tap_duration <= .1 and
            math.isfinite(move_interval) and .002 <= move_interval <= .02 and
            math.isfinite(flick_duration) and .024 <= flick_duration <= .1 and
            math.isfinite(slide_motion_delay) and 0 <= slide_motion_delay <= .040 and
            type(max_contacts) is int and 1 <= max_contacts <= 10):
        raise ValueError('Invalid gesture configuration')
    tempo = TempoMap(chart)
    rng = random.Random(seed)
    shifts, gestures = {}, []
    for note in chart:
        kind = note['type']
        if kind in ('BPM', 'System'):
            continue
        if kind not in ('Single', 'Long', 'Slide', 'Directional'):
            raise ValueError(f'Unsupported gesture: {kind}')
        points = note.get('connections', [note])
        if not points or (kind in ('Long', 'Slide') and len(points) < 2):
            raise ValueError('Missing gesture points')
        knots = []
        for p in points:
            lane = p['lane']
            low, high = (-.5, 6.5) if p.get('hidden') else (0, 6)
            if isinstance(lane, bool) or not isinstance(lane, (int, float)) or not math.isfinite(lane) or not low <= lane <= high:
                raise ValueError('Invalid lane')
            t = tempo.seconds(p['beat'])
            if knots and t <= knots[-1][0]:
                raise ValueError('Gesture points must advance in time')
            knots.append((t, float(lane)))
        if any(p.get('flick') for p in points[:-1]):
            raise ValueError('Interior flick is not supported')
        first, last = knots[0][0], knots[-1][0]
        motion_delay = slide_motion_delay if kind == 'Slide' else 0.
        motion_knots = [(t+motion_delay,lane) for t,lane in knots]
        last += motion_delay
        if first not in shifts:
            shifts[first] = bounded_normal(rng,jitter_ms)/1000
        shift = shifts[first]
        dx = bounded_normal(rng,position_jitter)
        dy = bounded_normal(rng,position_jitter/2)
        path = [(first, 1, knots[0][1], 'down', 590.)]
        for (ta, la), (tb, lb) in zip(motion_knots, motion_knots[1:]):
            if la == lb:
                continue
            steps = max(1, math.ceil((tb-ta)/move_interval))
            path.extend((ta+(tb-ta)*i/steps, 2, la+(lb-la)*i/steps, 'move', 590.)
                        for i in range(1, steps+1))
        flick = kind == 'Directional' or points[-1].get('flick', False)
        if flick:
            if len(knots) == 1:
                path[0] = (first-.018, 1, knots[0][1], 'down', 590.)
            lane = knots[-1][1]
            steps = math.ceil(flick_duration/move_interval)
            if kind == 'Directional':
                if note.get('direction') not in ('Left', 'Right'):
                    raise ValueError('Unknown flick direction')
                width = note.get('width', 1)
                if type(width) is not int or not 1 <= width <= 7:
                    raise ValueError('Invalid directional width')
                sign = -1 if note['direction'] == 'Left' else 1
                distance = max(.6, width-1)
                path.extend((last+i*flick_duration/steps, 2, lane+sign*distance*i/steps, 'move', 590.)
                            for i in range(1, steps+1))
            else:
                path.extend((last+i*flick_duration/steps, 2, lane, 'move', 590.-132*i/steps)
                            for i in range(1, steps+1))
            end = last+flick_duration+.008
        else:
            end = last + (tap_duration if len(knots) == 1 else .008)
        path.append((end, 3, path[-1][2], 'up', path[-1][4]))
        gestures.append((path[0][0]+shift, end+shift, dx, dy, shift, path))
    if not gestures:
        raise ValueError('Empty chart')
    available, events = [float('-inf')]*max_contacts, []
    for start, end, dx, dy, shift, path in sorted(gestures):
        contact = next((i for i, t in enumerate(available) if t < start-1e-9), None)
        if contact is None:
            raise ValueError('Insufficient contacts')
        available[contact] = end
        events.extend(GestureEvent(t+shift, priority, contact, lane, action, y+dy, dx)
                      for t, priority, lane, action, y in path)
    return sorted(events)
