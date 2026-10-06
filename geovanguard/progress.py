"""Step-by-step progress for the Processing dialog.

The standard QGIS Processing dialog has a single progress bar.  StepTracker
drives it as the sum of weighted steps, writes the current step (and tile /
batch counter) into the text above the bar (``setProgressText``), and logs a
line with counts and elapsed time whenever a step finishes, so every stage —
including the per-tile ones — can be checked in the Log tab.

ETA: the current step's remaining time is extrapolated from its own progress.
The later steps are estimated from the measured seconds per weight unit of the
steps already FINISHED in this run (resumed steps are not counted), so within
a step the Total ETA counts down steadily and is re-estimated only when a
step ends.  Only during the first executed step, with nothing finished yet,
does the running step's own rate stand in.  (Including the running step made
the total climb all through a step that is slower than its weight says.)
"""
import time

MIN_ETA_FRACTION = 0.02     # no ETA before 2 % of a step ...
MIN_ETA_SECONDS = 2.0       # ... or before it ran 2 s


def fmt_duration(sec):
    sec = int(round(max(sec, 0)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return '{}:{:02d}:{:02d}'.format(h, m, s) if h else '{}:{:02d}'.format(m, s)


# relative weights ≈ share of the run time on a large raster (perf runs)
WEIGHTS = {
    'sieve': 2, 'polygonize': 6, 'load': 6, 'insert': 12, 'densify': 5, 'label': 16,
    'merge': 3, 'smooth': 20, 'reconstruct': 18, 'repair': 4, 'output': 7, 'arcs': 2,
}

LABELS = {
    'sieve': 'GDAL sieve',
    'polygonize': 'GDAL polygonize',
    'load': 'Load & quantise polygons',
    'insert': 'Junction insertion (per tile)',
    'densify': 'Densify shared edges',
    'label': 'LPoly/RPoly arc building (per tile)',
    'merge': 'Join arc pieces across tiles',
    'smooth': 'Smooth arcs',
    'reconstruct': 'Reconstruct polygons',
    'repair': 'Repair invalid polygons',
    'output': 'Write smoothed polygons',
    'arcs': 'Write topology arcs',
}

PIPELINE_STEPS = ('load', 'insert', 'densify', 'label', 'merge', 'smooth', 'reconstruct',
                  'repair')


class StepTracker(object):

    def __init__(self, feedback, steps):
        self.fb = feedback
        self.steps = list(steps)
        total = float(sum(WEIGHTS.get(s, 1) for s in self.steps)) or 1.0
        self.offset = {}
        acc = 0.0
        for s in self.steps:
            self.offset[s] = acc / total * 100.0
            acc += WEIGHTS.get(s, 1)
        self.share = dict((s, WEIGHTS.get(s, 1) / total * 100.0) for s in self.steps)
        self.weight = dict((s, float(WEIGHTS.get(s, 1))) for s in self.steps)
        self.total_weight = total
        self.t0 = {}
        self.finished = set()
        self.run_weight = 0.0      # weight of steps actually executed and finished
        self.run_seconds = 0.0     # time those steps took
        self._last_text = 0.0
        self._last_bucket = {}

    def number(self, step):
        return '[{}/{}]'.format(self.steps.index(step) + 1, len(self.steps))

    def start(self, step, detail=''):
        self.t0[step] = time.time()
        self.update(step, 0.0, detail, force=True)

    def update(self, step, frac, detail='', force=False):
        if step not in self.offset:
            return
        frac = max(0.0, min(1.0, frac))
        self.fb.setProgress(self.offset[step] + self.share[step] * frac)
        now = time.time()
        # QGIS copies every progress text into the Log tab, so the text is
        # refreshed only at step start, at each further 10 % of the step (at
        # most every 2 s) or every 30 s — at most ~10 log lines per step.
        bucket = int(frac * 10)
        due = ((bucket > self._last_bucket.get(step, -1) and now - self._last_text >= 2.0)
               or now - self._last_text >= 30.0)
        if force or due:
            self._last_text = now
            self._last_bucket[step] = bucket
            text = '{} {}'.format(self.number(step), LABELS.get(step, step))
            if detail:
                text += ' — ' + detail
            text += ' ({:.0f}%)'.format(frac * 100)
            eta = self.eta(step, frac, now)
            if eta:
                text += ' · ' + eta
            try:
                self.fb.setProgressText(text)
            except AttributeError:
                pass

    def eta(self, step, frac, now=None):
        """'Step ETA m:ss · Total ETA ~m:ss' or '' while estimates are unreliable."""
        now = now or time.time()
        t0 = self.t0.get(step)
        if t0 is None or step in self.finished:
            return ''
        elapsed = now - t0
        if frac < MIN_ETA_FRACTION or elapsed < MIN_ETA_SECONDS:
            return ''
        step_left = elapsed * (1.0 - frac) / frac
        # seconds per weight unit from the finished steps only (steady within a
        # step); the running step's own rate only while nothing has finished
        if self.run_weight > 0:
            rate = self.run_seconds / self.run_weight
        else:
            rate = elapsed / (self.weight[step] * frac)
        later = sum(self.weight[s] for s in self.steps[self.steps.index(step) + 1:]
                    if s not in self.finished)
        if later <= 0:
            return 'ETA {}'.format(fmt_duration(step_left))
        return 'Step ETA {} · Total ETA ~{}'.format(fmt_duration(step_left),
                                                    fmt_duration(step_left + rate * later))

    def done(self, step, summary='', resumed=False):
        if step not in self.offset:
            return
        if not resumed and step in self.t0:
            self.run_weight += self.weight[step]
            self.run_seconds += time.time() - self.t0[step]
        self.finished.add(step)
        self.fb.setProgress(self.offset[step] + self.share[step])
        if resumed:
            msg = '↷ {} {} — already done (resumed from work folder)'.format(
                self.number(step), LABELS.get(step, step))
        else:
            dt = time.time() - self.t0.get(step, time.time())
            msg = '✔ {} {}{} — {:.1f} s'.format(
                self.number(step), LABELS.get(step, step),
                ' — ' + summary if summary else '', dt)
        self.fb.pushInfo(msg)

    def shift(self, seconds):
        """Ignore ``seconds`` (a pause) in the timing of the running steps."""
        for step, t0 in list(self.t0.items()):
            if step not in self.finished:
                self.t0[step] = t0 + seconds

    def note(self, text):
        """Show ``text`` above the progress bar (e.g. 'Paused')."""
        self._last_text = time.time()
        try:
            self.fb.setProgressText(text)
        except AttributeError:
            pass

    def skip(self, step, reason):
        if step not in self.offset:
            return
        self.finished.add(step)
        self.fb.setProgress(self.offset[step] + self.share[step])
        self.fb.pushInfo('– {} {} — skipped ({})'.format(self.number(step),
                                                          LABELS.get(step, step), reason))
