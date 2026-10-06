"""Standalone API — the same two tools as the QGIS plugin, without QGIS.

    smooth_polygons(input, output, ...)        A) vector polygon coverage
    smooth_polygonization(raster, output, ...) B) classified raster

Needs numpy, GDAL (osgeo) and shapely >= 2.  ``backend`` may also be a
GeometryBackend instance (the QGIS plugin passes its QgsGeometry backend).
Distances are in meters (converted to the layer CRS units) unless
``units='crs'``.
"""
import math
import os
import shutil
import tempfile

from osgeo import ogr, osr

from . import raster as rst
from .backends import get_backend
from .core import topology
from .core.store import ARC_FALLBACK, ARC_FALLBACK2, ARC_SMOOTHED
from .feedback import SilentFeedback
from .params import DEFAULT_ALGORITHM, smooth_signature
from .pipeline import Canceled, SmoothPipeline
from .progress import PIPELINE_STEPS, StepTracker

DRIVERS = {'.gpkg': 'GPKG', '.shp': 'ESRI Shapefile', '.fgb': 'FlatGeobuf',
           '.geojson': 'GeoJSON', '.json': 'GeoJSON', '.parquet': 'Parquet',
           '.sqlite': 'SQLite'}


class CanceledError(RuntimeError):
    """The run was canceled; progress is kept in the work folder (if given)."""


# ── units ─────────────────────────────────────────────────────────────────────

def crs_units_per_meter(srs, extent):
    """``(factor, note)``: meters × factor = layer units (same rules as the plugin).

    ``extent`` is (xmin, xmax, ymin, ymax) in layer units.  Geographic CRS:
    degrees per meter on the CRS ellipsoid at the centre of the data
    (geometric mean of the east-west and north-south scales).
    """
    if srs is None:
        return 1.0, 'Layer has no CRS: distances are used as-is (no conversion).'
    if srs.IsGeographic():
        cy = 0.0
        if extent is not None and all(math.isfinite(v) for v in extent):
            cy = 0.5 * (extent[2] + extent[3])
        cy = max(-85.0, min(85.0, cy))
        a = srs.GetSemiMajor()
        inv_f = srs.GetInvFlattening()
        e2 = (2.0 / inv_f - 1.0 / inv_f ** 2) if inv_f else 0.0
        phi = math.radians(cy)
        w = 1.0 - e2 * math.sin(phi) ** 2
        east = math.radians(1.0) * a * math.cos(phi) / math.sqrt(w)
        north = math.radians(1.0) * a * (1.0 - e2) / w ** 1.5
        m_per_deg = math.sqrt(east * north) if east > 0 and north > 0 else 111320.0
        note = ('Layer CRS is geographic: distances in meters are converted with '
                '{:.1f} m per degree (at latitude {:.2f}°). Accuracy decreases away from '
                'that latitude; reproject to a projected CRS for exact distances.'.format(
                    m_per_deg, cy))
        return 1.0 / m_per_deg, note
    if srs.IsProjected() or srs.IsLocal():
        m_per_unit = srs.GetLinearUnits()
        if m_per_unit and m_per_unit > 0:
            return 1.0 / m_per_unit, None
    return 1.0, 'Layer CRS units are unknown: distances are used as-is (no conversion).'


def _factor(units, srs, extent, fb):
    if str(units).lower() in ('crs', 'layer', 'map'):
        return 1.0
    if str(units).lower() not in ('m', 'meter', 'meters', 'metre', 'metres'):
        raise ValueError("units must be 'meters' or 'crs', not {!r}".format(units))
    factor, note = crs_units_per_meter(srs, extent)
    if note:
        fb.pushWarning(note)
    else:
        fb.pushInfo('Distances in meters → layer units × {:g}'.format(factor))
    return factor


# ── shared helpers ────────────────────────────────────────────────────────────

def _tracker(fb, before, arcs):
    steps = list(before) + list(PIPELINE_STEPS) + ['output'] + (['arcs'] if arcs else [])
    fb.pushInfo('{} steps: {}'.format(len(steps), ' → '.join(steps)))
    return StepTracker(fb, steps)


def _work_dir(work_dir):
    if work_dir:
        if not os.path.isdir(work_dir):
            os.makedirs(work_dir)
        return work_dir, False
    return tempfile.mkdtemp(prefix='geovanguard_'), True


def _create_output(path, layer_name, srs, geom_type, fields, overwrite=True):
    ext = os.path.splitext(path)[1].lower()
    drv_name = DRIVERS.get(ext)
    if drv_name is None:
        raise ValueError('Unsupported output format {!r} (use one of {}).'.format(
            ext, ', '.join(sorted(DRIVERS))))
    drv = ogr.GetDriverByName(drv_name)
    if drv is None:
        raise ValueError('This GDAL has no {} driver.'.format(drv_name))
    if os.path.exists(path) and overwrite:
        drv.DeleteDataSource(path)
    ds = drv.CreateDataSource(path)
    lyr = ds.CreateLayer(layer_name, srs=srs, geom_type=geom_type)
    for fd in fields:
        lyr.CreateField(fd)
    return ds, lyr


def _write_arcs(pipe, path, srs, fb):
    tracker = pipe.tracker
    tracker.start('arcs')
    n_total = max(pipe.summary()['arcs'], 1)
    fields = [ogr.FieldDefn(n, ogr.OFTInteger64) for n in ('arc_id', 'lpoly_id', 'rpoly_id')]
    fields.append(ogr.FieldDefn('smoothed', ogr.OFTInteger))
    ds, lyr = _create_output(path, 'arcs', srs, ogr.wkbLineString, fields)
    defn = lyr.GetLayerDefn()
    lyr.StartTransaction()
    n = 0
    for arc_id, lp, rp, state, wkb in pipe.iter_arcs():
        n += 1
        if n % 2000 == 0:
            if fb.isCanceled():
                break
            tracker.update('arcs', n / float(n_total), '{:,} / {:,} arcs'.format(n, n_total))
        f = ogr.Feature(defn)
        f.SetGeometry(ogr.CreateGeometryFromWkb(wkb))
        f.SetField(0, int(arc_id))
        f.SetField(1, int(lp))
        f.SetField(2, int(rp))
        f.SetField(3, 1 if state in (ARC_SMOOTHED, ARC_FALLBACK, ARC_FALLBACK2) else 0)
        lyr.CreateFeature(f)
    lyr.CommitTransaction()
    ds = None
    tracker.done('arcs', '{:,} arcs with lpoly_id / rpoly_id'.format(n))


def _emit(lyr, defn, attrs, wkb, multi):
    g = ogr.CreateGeometryFromWkb(wkb)
    if multi:
        parts = [ogr.ForceToMultiPolygon(g)]
    elif ogr.GT_Flatten(g.GetGeometryType()) == ogr.wkbMultiPolygon:
        parts = [g.GetGeometryRef(i).Clone() for i in range(g.GetGeometryCount())]
    else:
        parts = [g]
    for part in parts:
        f = ogr.Feature(defn)
        for i, v in attrs:
            f.SetField(i, v)
        f.SetGeometry(part)
        lyr.CreateFeature(f)


def _report(summary, fb):
    s = dict(summary, arcs_gentle=summary['arcs_fallback'] + summary['arcs_fallback2'])
    fb.pushInfo(
        'Done: {polygons:,} polygons, {arcs:,} arcs ({shared_arcs:,} shared), '
        '{arcs_smoothed:,} smoothed, {arcs_gentle:,} re-smoothed more gently, '
        '{arcs_reverted:,} reverted by repair, '
        '{polygons_original:,} polygon(s) kept original geometry, {seconds} s '
        '[{backend}]'.format(**s))
    if s.get('duplicate_edges'):
        fb.reportError('{:,} duplicated edges found: the input has overlapping polygons, so '
                       'it is not a clean coverage; results along those edges may be '
                       'wrong.'.format(s['duplicate_edges']))


def _smooth_values(kw):
    keys = ('iterations', 'sigma', 'pre_simplify', 'post_simplify', 'dp_tolerance',
            'points_per_100', 'degree', 'spline_smoothing')
    return dict((k, kw.get(k)) for k in keys)


# ── A) Smooth Polygons ────────────────────────────────────────────────────────

def smooth_polygons(input, output, layer=None, algorithm=DEFAULT_ALGORITHM, units='meters',
                    iterations=None, sigma=None, pre_simplify=None, post_simplify=None,
                    dp_tolerance=None, points_per_100=None, degree=None,
                    spline_smoothing=None, keep_outer=False, snap_tolerance=0.0,
                    tile_size=0.0, precision=0.0, work_dir=None, output_arcs=None,
                    output_layer=None, feedback=None, backend='auto', workers=0, pause=None):
    """Smooth a polygon coverage without gaps or overlaps (tool A).

    ``input``: any OGR vector source; ``layer``: layer name or index (default
    the first).  ``output``: .gpkg (default), .shp, .fgb, .geojson, ...
    Unset smoothing values take the Sentinel-2 defaults (params.DEFAULTS).
    ``workers``: parallel worker processes (0 = automatic: 50–75 % of the idle
    cores, small jobs in one process; 1 = no worker processes).
    ``pause``: optional geovanguard.pause.PauseControl — pause() / resume() from
    any thread holds the job at its next checkpoint with the workers frozen.
    Returns the pipeline summary (dict).
    """
    fb = feedback or SilentFeedback()
    src_ds = ogr.Open(input)
    if src_ds is None:
        raise ValueError('Cannot open vector source: {}'.format(input))
    src = src_ds.GetLayer(layer if layer is not None else 0)
    if src is None:
        raise ValueError('Layer {!r} not found in {}'.format(layer, input))
    srs = src.GetSpatialRef()
    ext = src.GetExtent()                      # (xmin, xmax, ymin, ymax)
    n_input = src.GetFeatureCount()
    factor = _factor(units, srs, ext, fb)
    precision = precision * factor
    if precision <= 0:
        precision = 1e-9 if srs is not None and srs.IsGeographic() else 1e-4
    tol = snap_tolerance * factor
    if tol <= 0:
        tol = 3 * precision
    origin = ((math.floor(ext[0]), math.floor(ext[2]))
              if all(math.isfinite(v) for v in ext) else (0.0, 0.0))
    tile_size = tile_size * factor
    topo_sig = {
        'tool': 'vector',
        'input': '{}|{}'.format(os.path.abspath(input), src.GetName()),
        'count': n_input,
        'extent': '{:.10g},{:.10g} : {:.10g},{:.10g}'.format(ext[0], ext[2], ext[1], ext[3]),
        'crs': (srs.GetAuthorityName(None) + ':' + srs.GetAuthorityCode(None)
                if srs is not None and srs.GetAuthorityCode(None) else
                (srs.ExportToWkt() if srs is not None else '')),
        'precision': precision, 'tol': tol, 'keep_outer': bool(keep_outer),
        'tile_size': tile_size,
    }
    smooth_sig = smooth_signature(algorithm, _smooth_values(locals()), factor)

    work, is_temp = _work_dir(work_dir)
    pipe = None
    try:
        pipe = SmoothPipeline(work, fb, _tracker(fb, (), output_arcs),
                              backend=get_backend(backend), workers=workers,
                              pause=pause)
        pipe.configure(topo_sig, smooth_sig, origin, precision, tol,
                       topology.FIXED_OUTER if keep_outer else topology.FIXED_NONE, tile_size)

        def records():
            n_invalid = 0
            src.ResetReading()
            src.SetIgnoredFields([src.GetLayerDefn().GetFieldDefn(i).GetName()
                                  for i in range(src.GetLayerDefn().GetFieldCount())])
            for f in src:
                g = f.GetGeometryRef()
                if g is None or g.IsEmpty():
                    continue
                if g.HasCurveGeometry():
                    g = g.GetLinearGeometry()
                if not g.IsValid():
                    n_invalid += 1
                    g = g.MakeValid()
                yield f.GetFID(), None, bytes(g.ExportToIsoWkb())
            src.SetIgnoredFields([])
            if n_invalid:
                fb.pushWarning('{:,} invalid input geometries were repaired with '
                               'MakeValid().'.format(n_invalid))

        summary = pipe.run(records, n_input)

        # ── output: original attributes joined back by feature id ────────────
        sdefn = src.GetLayerDefn()
        fields = [sdefn.GetFieldDefn(i) for i in range(sdefn.GetFieldCount())]
        multi = ogr.GT_Flatten(src.GetGeomType()) in (ogr.wkbMultiPolygon, ogr.wkbMultiSurface)
        out_ds, out = _create_output(output, output_layer or src.GetName() + '_smoothed', srs,
                                     ogr.wkbMultiPolygon if multi else ogr.wkbPolygon, fields)
        odefn = out.GetLayerDefn()
        n_total = max(summary['polygons'], 1)
        pipe.tracker.start('output')
        out.StartTransaction()
        k = 0
        for k, (_, fid, _, _, wkb) in enumerate(pipe.iter_results(), 1):
            if k % 1000 == 0:
                if fb.isCanceled():
                    raise Canceled()
                pipe.tracker.update('output', k / float(n_total),
                                    '{:,} / {:,} features'.format(k, n_total))
            if wkb is None:
                continue
            sf = src.GetFeature(fid)
            attrs = [(i, sf.GetField(i)) for i in range(len(fields))
                     if sf is not None and sf.IsFieldSetAndNotNull(i)]
            _emit(out, odefn, attrs, wkb, multi)
        out.CommitTransaction()
        out_ds = None
        pipe.tracker.done('output', '{:,} features with the source attributes'.format(k))
        if output_arcs:
            _write_arcs(pipe, output_arcs, srs, fb)
        _report(summary, fb)
        return summary
    except Canceled:
        fb.pushInfo('Canceled. Progress is kept in the work folder.' if not is_temp
                    else 'Canceled.')
        raise CanceledError('canceled')
    finally:
        if pipe is not None:
            pipe.close()
        if is_temp:
            shutil.rmtree(work, ignore_errors=True)


# ── B) Smooth Polygonization ──────────────────────────────────────────────────

def smooth_polygonization(raster, output, band=1, sieve=0, eight_connectedness=False,
                          keep_extent=True, algorithm=DEFAULT_ALGORITHM, units='meters',
                          iterations=None, sigma=None, pre_simplify=None, post_simplify=None,
                          dp_tolerance=None, points_per_100=None, degree=None,
                          spline_smoothing=None, tile_size=0.0, precision=0.0, work_dir=None,
                          output_arcs=None, output_layer='smoothed', feedback=None,
                          backend='auto', workers=0, pause=None):
    """Classified (integer) raster → smooth, gap-free polygons (tool B).

    Optional GDAL sieve (``sieve`` pixels) → GDAL polygonize → LPoly/RPoly
    smoothing.  Output field ``class_value``.  Returns the pipeline summary.
    """
    fb = feedback or SilentFeedback()
    ds = rst.open_raster(raster, band)
    info = rst.RasterInfo(ds, band)
    err = rst.integer_error(ds, band)
    if err:
        raise ValueError(err)
    srs = None
    if info.wkt:
        srs = osr.SpatialReference()
        srs.ImportFromWkt(info.wkt)
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    factor = _factor(units, srs, (info.xmin, info.xmax, info.ymin, info.ymax), fb)
    tile_size = tile_size * factor
    precision = precision * factor
    if precision <= 0:
        precision = info.pixel / 1000.0
    origin = (info.xmin, info.ymin)
    extent_q = (0, 0, int(round((info.xmax - info.xmin) / precision)),
                int(round((info.ymax - info.ymin) / precision)))
    raster_sig = {'path': os.path.abspath(raster), 'mtime': os.path.getmtime(raster)
                  if os.path.exists(raster) else None, 'band': band,
                  'sieve': sieve, 'eight': bool(eight_connectedness)}
    topo_sig = {'tool': 'raster', 'raster': raster_sig, 'precision': precision,
                'keep_extent': bool(keep_extent), 'tile_size': tile_size}
    smooth_sig = smooth_signature(algorithm, _smooth_values(locals()), factor)

    work, is_temp = _work_dir(work_dir)
    pipe = None
    try:
        tracker = _tracker(fb, (['sieve'] if sieve > 0 else []) + ['polygonize'], output_arcs)
        pipe = SmoothPipeline(work, fb, tracker, backend=get_backend(backend), workers=workers,
                              pause=pause)
        store = pipe.store
        gpkg = os.path.join(work, 'polygonized.gpkg')
        if (store.get('raster_sig') != raster_sig or not store.done('polygonize')
                or not os.path.exists(gpkg)):
            store.set('raster_sig', None)
            store.con.execute("DELETE FROM meta WHERE key='done:polygonize'")
            store.con.commit()
            src = ds
            try:
                if sieve > 0:
                    tracker.start('sieve', '{} px'.format(sieve))
                    src = rst.sieve(ds, band, sieve, eight_connectedness,
                                    os.path.join(work, 'sieved.tif'), fb,
                                    lambda f: tracker.update('sieve', f), pause)
                    tracker.done('sieve', 'regions < {} px merged into neighbours'.format(sieve))
                if fb.isCanceled():
                    raise Canceled()
                size = '{:,} × {:,} px'.format(info.width, info.height)
                tracker.start('polygonize', size)
                n = rst.polygonize(src, 1 if sieve > 0 else band, gpkg, eight_connectedness,
                                   fb, lambda f: tracker.update('polygonize', f, size), pause)
            except (RuntimeError, ValueError) as e:
                if fb.isCanceled():
                    raise Canceled()
                raise ValueError(str(e))
            if fb.isCanceled():
                raise Canceled()
            src = None
            tracker.done('polygonize', '{:,} raw polygons'.format(n))
            store.set('raster_sig', raster_sig)
            store.mark_done('polygonize')
        else:
            for step in (['sieve'] if sieve > 0 else []) + ['polygonize']:
                tracker.done(step, resumed=True)
        ds = None

        pipe.configure(topo_sig, smooth_sig, origin, precision, 0.0,
                       topology.FIXED_EXTENT if keep_extent else topology.FIXED_NONE,
                       tile_size, extent_q)
        _ds = ogr.Open(gpkg)
        n_hint = _ds.GetLayer(0).GetFeatureCount()
        _ds = None
        summary = pipe.run(lambda: rst.iter_polygons(gpkg), n_hint)

        fd = ogr.FieldDefn('class_value', ogr.OFTInteger64 if info.is_integer else ogr.OFTReal)
        out_ds, out = _create_output(output, output_layer, srs, ogr.wkbPolygon, [fd])
        odefn = out.GetLayerDefn()
        n_total = max(summary['polygons'], 1)
        tracker.start('output')
        out.StartTransaction()
        k = 0
        for k, (_, _, attr, _, wkb) in enumerate(pipe.iter_results(), 1):
            if k % 1000 == 0:
                if fb.isCanceled():
                    raise Canceled()
                tracker.update('output', k / float(n_total),
                               '{:,} / {:,} features'.format(k, n_total))
            if wkb is not None:
                _emit(out, odefn, [(0, attr)] if attr is not None else [], wkb, False)
        out.CommitTransaction()
        out_ds = None
        tracker.done('output', '{:,} polygons with class_value'.format(k))
        if output_arcs:
            _write_arcs(pipe, output_arcs, srs, fb)
        _report(summary, fb)
        return summary
    except Canceled:
        fb.pushInfo('Canceled. Progress is kept in the work folder.' if not is_temp
                    else 'Canceled.')
        raise CanceledError('canceled')
    finally:
        ds = None
        if pipe is not None:
            pipe.close()
        if is_temp:
            shutil.rmtree(work, ignore_errors=True)
