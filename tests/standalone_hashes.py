"""Standalone API (no QGIS) must give the same results as the QGIS plugin.

Runs the cases of result_hashes.py through geovanguard.api with a chosen
geometry backend and compares the SHA-256 of every output WKB with a baseline
written by result_hashes.py (the plugin):

    python tests/standalone_hashes.py OUT.json --compare PLUGIN.json [--backend shapely]

Any Python with numpy, GDAL and shapely >= 2 works (QGIS' Python too: the
script never imports qgis — it checks that at the end).
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from osgeo import gdal, ogr  # noqa: E402

from synth import make_raster  # noqa: E402
from geovanguard import api  # noqa: E402
from geovanguard.core.smoothing import ALGORITHMS  # noqa: E402
from geovanguard.feedback import SilentFeedback  # noqa: E402

gdal.UseExceptions()
ogr.UseExceptions()
DATA = os.path.join(HERE, 'data')
TMP = tempfile.mkdtemp(prefix='gv_standalone_')


def digest(path):
    ds = ogr.Open(path)
    lyr = ds.GetLayer(0)
    wkbs = sorted(bytes(f.GetGeometryRef().ExportToIsoWkb(ogr.wkbNDR)) for f in lyr)
    h = hashlib.sha256()
    for w in wkbs:
        h.update(w)
    return {'features': len(wkbs), 'sha256': h.hexdigest()}


def cases():
    tif = os.path.join(TMP, 'cls.tif')
    make_raster(tif, size=300, seed=3)
    # real-data clips are kept out of the public repository: skipped when absent
    for name in ('region5_view', 'region5_riverbank'):
        src = os.path.join(DATA, name + '.gpkg')
        if not os.path.exists(src):
            continue
        yield name + ' / Gaussian', api.smooth_polygons, (src,), {}
        yield name + ' / Chaikin', api.smooth_polygons, (src,), {'algorithm': 'Chaikin'}
    for alg in ALGORITHMS:
        yield 'raster / ' + alg, api.smooth_polygonization, (tif,), {'algorithm': alg}
    yield 'raster / Gaussian tile 400', api.smooth_polygonization, (tif,), {'tile_size': 400}


WORKERS = int(sys.argv[sys.argv.index('--workers') + 1]) if '--workers' in sys.argv else None


def main():
    out_json = sys.argv[1]
    backend = sys.argv[sys.argv.index('--backend') + 1] if '--backend' in sys.argv else 'shapely'
    base = None
    if '--compare' in sys.argv:
        with open(sys.argv[sys.argv.index('--compare') + 1]) as f:
            base = json.load(f)
    result = {}
    for k, (label, fn, args, kw) in enumerate(cases()):
        fb = SilentFeedback()
        out = os.path.join(TMP, 's{}.gpkg'.format(k))
        t0 = time.time()
        summary = fn(*(args + (out,)), feedback=fb, backend=backend,
                     workers=WORKERS or 0, **kw)
        result[label] = digest(out)
        same = '' if base is None else ('  SAME' if base.get(label) == result[label] else '  DIFFERENT')
        print('{:<34} {:>6} feat  {:6.1f}s  {}{}'.format(
            label, result[label]['features'], time.time() - t0,
            result[label]['sha256'][:16], same))
        if '-v' in sys.argv:
            print('      ' + summary['backend'] + ' | ' + [m for m in fb.log if m.startswith('Done')][0])
    with open(out_json, 'w') as f:
        json.dump(result, f, indent=1)
    shutil.rmtree(TMP, ignore_errors=True)
    leaked = sorted(m for m in sys.modules if m == 'qgis' or m.startswith('qgis.'))
    if backend != 'qgis' and leaked:
        print('QGIS WAS IMPORTED: ' + ', '.join(leaked))
        sys.exit(2)
    if base is not None and any(base.get(k) != v for k, v in result.items()):
        print('RESULTS DIFFER FROM BASELINE')
        sys.exit(1)
    print('standalone ({}) OK, qgis not imported'.format(backend))


if __name__ == '__main__':
    main()
