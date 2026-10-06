"""Pause / resume / cancel-while-paused with worker processes (standalone API).

    python tests/test_pause.py        (any Python with numpy, GDAL, shapely >= 2)

* paused: progress stands still, the log says Paused / Resumed, and the
  result is bit-identical to an uninterrupted run;
* Cancel while paused stops the job promptly (frozen workers are killed).
"""
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from osgeo import gdal, ogr  # noqa: E402

from synth import make_raster  # noqa: E402
from geovanguard import api  # noqa: E402
from geovanguard.feedback import SilentFeedback  # noqa: E402
from geovanguard.pause import PauseControl  # noqa: E402

gdal.UseExceptions()
ogr.UseExceptions()
TMP = tempfile.mkdtemp(prefix='gv_pause_')


def digest(path):
    ds = ogr.Open(path)
    h = hashlib.sha256()
    for w in sorted(bytes(f.GetGeometryRef().ExportToIsoWkb(ogr.wkbNDR)) for f in ds.GetLayer(0)):
        h.update(w)
    return h.hexdigest()


class Recorder(SilentFeedback):
    """Pauses once progress passes ``at`` %, keeps the progress history."""

    def __init__(self, control, at):
        super(Recorder, self).__init__()
        self.control, self.at = control, at
        self.history = []
        self.paused_at = None

    def setProgress(self, p):  # noqa: N802
        super(Recorder, self).setProgress(p)
        self.history.append((time.time(), p))
        if self.paused_at is None and p >= self.at:
            self.paused_at = time.time()
            self.control.pause()


def main():
    tif = os.path.join(TMP, 'cls.tif')
    make_raster(tif, size=400, seed=5)
    base = dict(workers=2, tile_size=300)
    ref = os.path.join(TMP, 'ref.gpkg')
    api.smooth_polygonization(tif, ref, **base)

    # pause in the middle, resume after 3 s
    control = PauseControl()
    fb = Recorder(control, at=45)
    holder = {}

    def resume_later():
        while fb.paused_at is None:
            time.sleep(0.05)
        time.sleep(3.0)
        holder['resumed'] = time.time()
        control.resume()

    t = threading.Thread(target=resume_later)
    t.start()
    out = os.path.join(TMP, 'paused.gpkg')
    api.smooth_polygonization(tif, out, pause=control, feedback=fb, **base)
    t.join()
    assert fb.paused_at is not None, 'never paused'
    during = [p for ts, p in fb.history if fb.paused_at + 0.8 < ts < holder['resumed']]
    assert not during, 'progress moved while paused: {}'.format(during[:5])
    assert any(m.startswith('⏸ Paused') for m in fb.log), 'no Paused message'
    assert any(m.startswith('▶ Resumed') for m in fb.log), 'no Resumed message'
    assert digest(out) == digest(ref), 'paused/resumed result differs'
    print('  ok  pause 3 s with 2 workers: progress held, result identical')

    # cancel while paused
    control = PauseControl()
    fb = Recorder(control, at=45)

    def cancel_later():
        while fb.paused_at is None:
            time.sleep(0.05)
        time.sleep(1.0)
        holder['cancel'] = time.time()
        fb.cancel()

    t = threading.Thread(target=cancel_later)
    t.start()
    try:
        api.smooth_polygonization(tif, os.path.join(TMP, 'canceled.gpkg'), pause=control,
                                  feedback=fb, work_dir=os.path.join(TMP, 'work'), **base)
        raise AssertionError('expected CanceledError')
    except api.CanceledError:
        stop = time.time() - holder['cancel']
    t.join()
    assert stop < 5, 'cancel while paused took {:.1f} s'.format(stop)
    print('  ok  cancel while paused stops in {:.1f} s'.format(stop))
    shutil.rmtree(TMP, ignore_errors=True)
    print('PAUSE TESTS PASSED')


if __name__ == '__main__':
    main()
