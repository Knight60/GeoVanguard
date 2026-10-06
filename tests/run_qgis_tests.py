"""End-to-end tests inside a headless QGIS (run with QGIS' own Python):

    "C:\\Program Files\\QGIS 4.2.2\\bin\\python-qgis.bat" tests\\run_qgis_tests.py
    "C:\\Program Files\\QGIS 3.40.7\\bin\\python-qgis-ltr.bat" tests\\run_qgis_tests.py

Checks, for both tools:
  * every output polygon is valid
  * no overlaps:  sum(area) == area(union)
  * no gaps:      with fixed outer boundary, sum(area) == area of the input coverage
  * results do not depend on the tile size (tiling is only a memory device)
  * LPoly/RPoly of the arcs output agree with the geometry
  * resume after cancel and re-smoothing from the work folder
"""
import math
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from qgis.core import (QgsApplication, QgsGeometry, QgsPointXY,  # noqa: E402
                       QgsProcessingFeedback, QgsVectorLayer)

app = QgsApplication([], False)
app.initQgis()
sys.path.append(os.path.join(QgsApplication.prefixPath(), 'python', 'plugins'))
import processing  # noqa: E402
from processing.core.Processing import Processing  # noqa: E402

Processing.initialize()

from geovanguard_qgis.provider import GeoVanguardProvider  # noqa: E402

PROVIDER = GeoVanguardProvider()
QgsApplication.processingRegistry().addProvider(PROVIDER)

import numpy as np  # noqa: E402
from osgeo import gdal, osr  # noqa: E402

from synth import make_raster  # noqa: E402,F401

ALG_A = 'geovanguard:smooth_polygons'
ALG_B = 'geovanguard:smooth_polygonization'
TMP = tempfile.mkdtemp(prefix='topotools_test_')
VERBOSE = '-v' in sys.argv


class Feedback(QgsProcessingFeedback):
    def __init__(self, cancel_at=None):
        super(Feedback, self).__init__()
        self.cancel_at = cancel_at      # cancel once progress reaches this percent
        self.log = []

    def setProgress(self, p):
        if self.cancel_at is not None and p >= self.cancel_at and not self.isCanceled():
            self.canceled_time = time.time()
            self.cancel()
        super(Feedback, self).setProgress(p)

    def pushInfo(self, msg):
        self.log.append(msg)
        if VERBOSE:
            print('      | ' + msg)

    def pushWarning(self, msg):
        self.log.append('WARNING ' + msg)
        if VERBOSE:
            print('      W ' + msg)

    def reportError(self, msg, fatalError=False):
        self.log.append('ERROR ' + msg)
        print('      ! ' + msg)


# ── data ──────────────────────────────────────────────────────────────────────

def layer(path):
    lyr = QgsVectorLayer(path, os.path.basename(path), 'ogr')
    assert lyr.isValid(), path
    return lyr


def geoms(lyr):
    return [f.geometry() for f in lyr.getFeatures()]


def check_coverage(lyr, expect_area=None, rel=1e-9, label=''):
    gs = geoms(lyr)
    bad = [i for i, g in enumerate(gs) if not g.isGeosValid()]
    assert not bad, '{}: {} invalid output polygons'.format(label, len(bad))
    total = sum(g.area() for g in gs)
    union = QgsGeometry.unaryUnion(gs).area()
    assert abs(total - union) <= rel * union + 1e-6, \
        '{}: overlap — sum {} vs union {}'.format(label, total, union)
    if expect_area is not None:
        assert abs(total - expect_area) <= rel * expect_area + 1e-6, \
            '{}: gap — sum {} vs expected {}'.format(label, total, expect_area)
    return len(gs), total


def fingerprint(lyr):
    out = []
    for f in lyr.getFeatures():
        g = f.geometry()
        c = g.centroid().asPoint()
        out.append((round(g.area(), 4), round(c.x(), 4), round(c.y(), 4),
                    g.constGet().nCoordinates()))
    return sorted(out)


def run(alg, params, feedback=None, expect_smoothing=True):
    fb = feedback or Feedback()
    t0 = time.time()
    res = processing.run(alg, params, feedback=fb)
    done = [m for m in fb.log if m.startswith('Done:')]
    if expect_smoothing and done:
        nums = dict((k, int(v.replace(',', ''))) for v, k in re.findall(
            r'([\d,]+) (smoothed|polygon\(s\) kept original)', done[0]))
        assert nums.get('smoothed', 0) > 0, 'nothing was smoothed: ' + done[0]
        assert nums.get('polygon(s) kept original', 0) == 0, 'fallback used: ' + done[0]
    return res, fb, time.time() - t0


# ── tests ─────────────────────────────────────────────────────────────────────

def test_raster_tool_coverage_and_tiling():
    tif = os.path.join(TMP, 'cls_full.tif')
    make_raster(tif, nodata_hole=False)
    extent_area = 2400.0 * 2400.0
    fps = []
    for tile in (0, 400, 150):
        out = os.path.join(TMP, 'b_full_t{}.gpkg'.format(tile))
        res, fb, sec = run(ALG_B, {'INPUT': tif, 'BAND': 1, 'TILE_SIZE': tile,
                                   'KEEP_EXTENT': True, 'OUTPUT': out})
        lyr = layer(res['OUTPUT'])
        n, total = check_coverage(lyr, expect_area=extent_area, label='B tile={}'.format(tile))
        fps.append(fingerprint(lyr))
        print('  ok  B no gaps/overlaps, tile={:<4} {} polygons, {:.1f}s'.format(tile, n, sec))
    assert fps[0] == fps[1] == fps[2], 'B: result depends on tile size'
    print('  ok  B result identical for tile sizes 0 / 400 / 150')


def test_raster_tool_nodata():
    tif = os.path.join(TMP, 'cls_nodata.tif')
    make_raster(tif, nodata_hole=True)
    out = os.path.join(TMP, 'b_nodata.gpkg')
    arcs = os.path.join(TMP, 'b_nodata_arcs.gpkg')
    res, fb, sec = run(ALG_B, {'INPUT': tif, 'BAND': 1, 'TILE_SIZE': 300, 'SIEVE': 4,
                               'OUTPUT': out, 'OUTPUT_ARCS': arcs})
    lyr = layer(res['OUTPUT'])
    n, total = check_coverage(lyr, label='B nodata')
    union = QgsGeometry.unaryUnion(geoms(lyr))
    holes = sum(p.constGet().numInteriorRings() if hasattr(p.constGet(), 'numInteriorRings') else 0
                for p in union.asGeometryCollection())
    assert holes >= 1, 'nodata hole disappeared'
    check_lr(layer(res['OUTPUT_ARCS']), lyr, 'class_value')
    print('  ok  B with nodata + sieve: {} polygons, nodata hole kept, {:.1f}s'.format(n, sec))


def check_lr(arcs_lyr, poly_lyr, attr):
    """lpoly_id must be on the left of each arc, rpoly_id on the right."""
    polys = list(poly_lyr.getFeatures())
    n_checked = 0
    for f in arcs_lyr.getFeatures():
        if n_checked >= 300:
            break
        g = f.geometry()
        line = g.asPolyline()
        if len(line) < 2:
            continue
        L = g.length()
        mid = g.interpolate(L / 2).asPoint()
        a = g.interpolate(max(0.0, L / 2 - 0.05)).asPoint()
        b = g.interpolate(min(L, L / 2 + 0.05)).asPoint()
        dx, dy = b.x() - a.x(), b.y() - a.y()
        m = math.hypot(dx, dy)
        if m == 0:
            continue
        nx, ny = -dy / m * 0.02, dx / m * 0.02
        left = QgsGeometry.fromPointXY(QgsPointXY(mid.x() + nx, mid.y() + ny))
        right = QgsGeometry.fromPointXY(QgsPointXY(mid.x() - nx, mid.y() - ny))
        in_left = [p for p in polys if p.geometry().contains(left)]
        in_right = [p for p in polys if p.geometry().contains(right)]
        lp, rp = f['lpoly_id'], f['rpoly_id']
        assert len(in_left) == 1, 'nothing/too much on the left of arc {}'.format(f['arc_id'])
        if rp == 0:
            assert not in_right, 'arc {} has rpoly 0 but a polygon on the right'.format(f['arc_id'])
        else:
            assert len(in_right) == 1, 'nothing on the right of shared arc {}'.format(f['arc_id'])
            assert in_left[0].id() != in_right[0].id()
        n_checked += 1
    assert n_checked > 20
    print('  ok  LPoly/RPoly sides verified on {} arcs'.format(n_checked))


def test_vector_tool():
    tif = os.path.join(TMP, 'cls_vec.tif')
    make_raster(tif, nodata_hole=True, seed=7)
    raw = os.path.join(TMP, 'raw.gpkg')
    processing.run('gdal:polygonize', {'INPUT': tif, 'BAND': 1, 'FIELD': 'DN',
                                       'EIGHT_CONNECTEDNESS': False, 'OUTPUT': raw})
    raw_lyr = layer(raw)
    raw_area = sum(g.area() for g in geoms(raw_lyr))
    fps = []
    for tile in (0, 350):
        out = os.path.join(TMP, 'a_t{}.gpkg'.format(tile))
        res, fb, sec = run(ALG_A, {'INPUT': raw, 'KEEP_OUTER': True, 'TILE_SIZE': tile,
                                   'OUTPUT': out})
        lyr = layer(res['OUTPUT'])
        n, total = check_coverage(lyr, expect_area=raw_area, label='A tile={}'.format(tile))
        assert n == raw_lyr.featureCount(), 'A: feature count changed'
        assert [f['DN'] for f in lyr.getFeatures()] == [f['DN'] for f in raw_lyr.getFeatures()], \
            'A: attributes not carried over 1:1'
        fps.append(fingerprint(lyr))
        print('  ok  A no gaps/overlaps (outer kept), tile={:<4} {} polygons, {:.1f}s'.format(
            tile, n, sec))
    assert fps[0] == fps[1], 'A: result depends on tile size'
    print('  ok  A result identical for tile sizes 0 / 350')


VORONOI_SEED = int(os.environ.get('VORONOI_SEED', '5'))


def voronoi_points(seed, n=150):
    """Deterministic random points (the processing algorithm has no seed)."""
    from qgis.core import QgsFeature, QgsPointXY
    rng = np.random.RandomState(seed)
    lyr = QgsVectorLayer('Point?crs=EPSG:32647', 'pts', 'memory')
    feats = []
    for x, y in rng.rand(n, 2) * 1000.0:
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(x), float(y))))
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    return lyr


def test_vector_voronoi():
    """Vector coverage whose shared vertices come from GEOS, not a pixel grid."""
    pts = voronoi_points(seed=VORONOI_SEED)
    vor = os.path.join(TMP, 'voronoi.gpkg')
    processing.run('native:voronoipolygons', {'INPUT': pts, 'BUFFER': 0, 'OUTPUT': vor})
    vor_lyr = layer(vor)
    vor_area = sum(g.area() for g in geoms(vor_lyr))
    # Voronoi edges are single straight segments between junctions: nothing to
    # smooth, so this checks the topology (junction matching) only.
    res, fb, sec = run(ALG_A, {'INPUT': vor, 'KEEP_OUTER': True, 'PRE_SIMPLIFY': 0,
                               'POST_SIMPLIFY': 0, 'TILE_SIZE': 300,
                               'OUTPUT': os.path.join(TMP, 'a_vor.gpkg')},
                       expect_smoothing=False)
    done = [m for m in fb.log if m.startswith('Done:')][0]
    assert '0 polygon(s) kept original' in done, done
    shared = int(re.search(r'\(([\d,]+) shared\)', done).group(1).replace(',', ''))
    arcs = int(re.search(r'([\d,]+) arcs', done).group(1).replace(',', ''))
    assert shared > 0.8 * arcs, 'junctions not matched: ' + done
    # quantisation to the 0.1 mm grid moves non-grid vertices: ~1e-7 relative area
    n, total = check_coverage(layer(res['OUTPUT']), expect_area=vor_area, rel=1e-6,
                              label='A voronoi')
    print('  ok  A on Voronoi coverage: {} polygons, no gaps/overlaps'.format(n))


def test_resume_and_resmooth():
    tif = os.path.join(TMP, 'cls_full.tif')
    work = os.path.join(TMP, 'work')
    base = {'INPUT': tif, 'BAND': 1, 'TILE_SIZE': 300, 'WORK_DIR': work}
    ref, _, _ = run(ALG_B, dict(base, WORK_DIR='', OUTPUT=os.path.join(TMP, 'ref.gpkg')))
    ref_fp = fingerprint(layer(ref['OUTPUT']))

    fb = Feedback(cancel_at=60)      # inside the smoothing stage (pipeline spans 20–90 %)
    res, fb, _ = run(ALG_B, dict(base, OUTPUT=os.path.join(TMP, 'cancel.gpkg')), fb)
    assert not res.get('OUTPUT'), 'expected a canceled run'
    res, fb, _ = run(ALG_B, dict(base, OUTPUT=os.path.join(TMP, 'resumed.gpkg')))
    assert any('Resuming' in m for m in fb.log), 'did not resume'
    assert fingerprint(layer(res['OUTPUT'])) == ref_fp, 'resumed result differs'
    print('  ok  cancel + resume gives the same result')

    # the same with worker processes: cancel stops at once, resume is identical
    pwork = os.path.join(TMP, 'work_parallel')
    fb = Feedback(cancel_at=45)
    res, fb, _ = run(ALG_B, dict(base, WORK_DIR=pwork, WORKERS=2,
                                 OUTPUT=os.path.join(TMP, 'pcancel.gpkg')), fb)
    stop = time.time() - fb.canceled_time
    assert not res.get('OUTPUT'), 'expected a canceled parallel run'
    assert any('2 worker' in m for m in fb.log), 'workers were not used'
    assert stop < 10, 'cancel took {:.1f} s with worker processes'.format(stop)
    res, fb, _ = run(ALG_B, dict(base, WORK_DIR=pwork, WORKERS=2,
                                 OUTPUT=os.path.join(TMP, 'presumed.gpkg')))
    assert any('Resuming' in m for m in fb.log), 'parallel run did not resume'
    assert fingerprint(layer(res['OUTPUT'])) == ref_fp, 'parallel resumed result differs'
    print('  ok  cancel with 2 workers stops in {:.1f} s; resume gives the same result'.format(stop))

    res, fb, _ = run(ALG_B, dict(base, SIGMA=20, OUTPUT=os.path.join(TMP, 'resmooth.gpkg')))
    assert any('re-using topology' in m for m in fb.log), 'topology was rebuilt'
    assert fingerprint(layer(res['OUTPUT'])) != ref_fp
    check_coverage(layer(res['OUTPUT']), expect_area=2400.0 * 2400.0, label='resmooth')
    print('  ok  changing only smoothing parameters re-uses the topology')


def test_distance_units():
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle
    from geovanguard_qgis.units import crs_units_per_meter
    f, note = crs_units_per_meter(QgsCoordinateReferenceSystem('EPSG:32647'), None)
    assert f == 1.0 and note is None
    f, note = crs_units_per_meter(QgsCoordinateReferenceSystem('EPSG:2263'), None)   # US feet
    assert abs(f - 3.2808333) < 1e-4, f
    f, note = crs_units_per_meter(QgsCoordinateReferenceSystem('EPSG:4326'),
                                  QgsRectangle(100.0, 13.0, 101.0, 14.0))
    assert note and 1 / 111500.0 < f < 1 / 108000.0, f     # ~1 degree ≈ 109–111 km at 13.5°N
    print('  ok  meters → CRS units: UTM 1, US ft {:.4f}, WGS84 1/{:.0f}'.format(3.2808333, 1 / f))

    # geographic coverage, distances given in meters
    tif = os.path.join(TMP, 'cls_full.tif')
    raw = os.path.join(TMP, 'raw_geo_src.gpkg')
    processing.run('gdal:polygonize', {'INPUT': tif, 'BAND': 1, 'FIELD': 'DN', 'OUTPUT': raw})
    geo = os.path.join(TMP, 'raw_4326.gpkg')
    processing.run('native:reprojectlayer', {'INPUT': raw, 'TARGET_CRS': 'EPSG:4326', 'OUTPUT': geo})
    geo_lyr = layer(geo)
    geo_area = sum(g.area() for g in geoms(geo_lyr))
    res, fb, sec = run(ALG_A, {'INPUT': geo, 'UNITS': 0, 'KEEP_OUTER': True,
                               'OUTPUT': os.path.join(TMP, 'a_4326.gpkg')})
    n, _ = check_coverage(layer(res['OUTPUT']), expect_area=geo_area, rel=1e-7, label='A 4326')
    # same run with the raw degree value (2 "units" = 2 degrees) must differ completely
    res2, _, _ = run(ALG_A, {'INPUT': geo, 'UNITS': 1, 'KEEP_OUTER': True,
                             'OUTPUT': os.path.join(TMP, 'a_4326_deg.gpkg')}, expect_smoothing=False)
    assert fingerprint(layer(res['OUTPUT'])) != fingerprint(layer(res2['OUTPUT']))
    print('  ok  A on EPSG:4326 with distances in meters: {} polygons, no gaps/overlaps'.format(n))


def test_raster_integer_types():
    """Tool B accepts every integer type, rejects floats and out-of-Int32 classes."""
    from qgis.core import QgsProcessingException
    cls = make_raster(os.path.join(TMP, 'u8.tif'), size=40, nodata_hole=False)

    def write(name, dt, values, opts=None):
        path = os.path.join(TMP, 'dt_{}.tif'.format(name))
        ds = gdal.GetDriverByName('GTiff').Create(path, 40, 40, 1, dt, opts or [])
        ds.SetGeoTransform((600000.0, 10.0, 0.0, 1500000.0, 0.0, -10.0))
        ds.GetRasterBand(1).WriteArray(values)
        ds = None
        return path

    ok_types = [('UInt16', gdal.GDT_UInt16), ('Int16', gdal.GDT_Int16),
                ('UInt32', gdal.GDT_UInt32), ('Int32', gdal.GDT_Int32)]
    for name in ('GDT_Int8', 'GDT_Int64', 'GDT_UInt64'):
        if hasattr(gdal, name):
            ok_types.append((name[4:], getattr(gdal, name)))
    for name, dt in ok_types:
        res, _, _ = run(ALG_B, {'INPUT': write(name, dt, cls.astype(np.float64) + 100),
                                'BAND': 1, 'OUTPUT': 'memory:'})
        vals = sorted({f['class_value'] for f in res['OUTPUT'].getFeatures()})
        assert vals == sorted({int(v) + 100 for v in np.unique(cls)}), (name, vals)
    bad = [('Float32', write('Float32', gdal.GDT_Float32, cls.astype(np.float64)), 'integer'),
           ('Float64', write('Float64', gdal.GDT_Float64, cls.astype(np.float64)), 'integer'),
           ('UInt32 > Int32', write('UInt32big', gdal.GDT_UInt32,
                                    cls.astype(np.float64) + 3e9), '32-bit')]
    for name, path, word in bad:
        try:
            processing.run(ALG_B, {'INPUT': path, 'BAND': 1, 'OUTPUT': 'memory:'},
                           feedback=Feedback())
        except QgsProcessingException as err:
            assert word in str(err), str(err)
            continue
        raise AssertionError('{} raster was accepted'.format(name))
    print('  ok  B accepts {} (class values kept); rejects {}'.format(
        ', '.join(n for n, _ in ok_types), ', '.join(n for n, _, _ in bad)))


def test_large_job_requires_file():
    """Large job + temporary output → refused with a message; a file output runs and
    gets the work folder <output name> next to it (created, kept)."""
    import geovanguard_qgis.algorithms as A
    from qgis.core import QgsProcessingException
    old = (A.LARGE_OUTPUT_FEATURES, A.LARGE_RASTER_PIXELS)
    A.LARGE_OUTPUT_FEATURES, A.LARGE_RASTER_PIXELS = 10, 1000
    try:
        tif = os.path.join(TMP, 'cls_full.tif')
        cases = [(ALG_B, {'INPUT': tif, 'BAND': 1}),
                 (ALG_A, {'INPUT': os.path.join(TMP, 'raw.gpkg')})]
        for alg, base in cases:
            try:
                run(alg, dict(base, OUTPUT='TEMPORARY_OUTPUT'))
                raise AssertionError('{}: a large job ran with a temporary output'.format(alg))
            except QgsProcessingException as err:
                assert 'too large for a temporary layer' in str(err), str(err)
            out = os.path.join(TMP, 'large_{}.gpkg'.format(alg.split(':')[1]))
            res, fb, _ = run(alg, dict(base, OUTPUT=out))
            work = out[:-len('.gpkg')]
            assert os.path.isfile(out) and os.path.isdir(work), (out, work)
            assert os.path.exists(os.path.join(work, 'topology_work.sqlite')), 'work folder unused'
            assert any('same name as the output' in m for m in fb.log), 'work folder not logged'
            res, fb, _ = run(alg, dict(base, OUTPUT=out))
            assert any('Resuming' in m for m in fb.log), 'second run did not reuse the work folder'
        print('  ok  large job: temporary output refused; file output → work folder of the same name')
    finally:
        A.LARGE_OUTPUT_FEATURES, A.LARGE_RASTER_PIXELS = old


def test_all_algorithms():
    tif = os.path.join(TMP, 'cls_full.tif')
    from geovanguard.core.smoothing import ALGORITHMS
    for i, name in enumerate(ALGORITHMS):
        res, fb, sec = run(ALG_B, {'INPUT': tif, 'BAND': 1, 'ALGORITHM': i,
                                   'OUTPUT': os.path.join(TMP, 'alg{}.gpkg'.format(i))})
        n, _ = check_coverage(layer(res['OUTPUT']), expect_area=2400.0 * 2400.0, label=name)
        reverted = [m for m in fb.log if m.startswith('Done')]
        print('  ok  {:<16} {:.1f}s  {}'.format(name, sec, reverted[0] if reverted else ''))


if __name__ == '__main__':
    from qgis.core import Qgis
    print('QGIS', Qgis.QGIS_VERSION, '| tmp', TMP)
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            print(name)
            try:
                fn()
            except Exception:
                import traceback
                traceback.print_exc()
                failed += 1
    app.exitQgis()
    if failed:
        print('{} TEST(S) FAILED'.format(failed))
        sys.exit(1)
    shutil.rmtree(TMP, ignore_errors=True)
    print('ALL QGIS TESTS PASSED')
