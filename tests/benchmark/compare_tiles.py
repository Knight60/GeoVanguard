"""How different are two GeoVanguard outputs of the same input (e.g. automatic vs 8 km tiles)?

usage: python-qgis compare_tiles.py <a.gpkg> <b.gpkg>
Features are matched by their order (both outputs keep the input order); the match is checked
with the class value and the polygon area. Reports identical polygons, and for the others the
Hausdorff distance, the area of the symmetric difference and the vertex counts.
"""
import sys
import time

import numpy as np
import shapely
from osgeo import ogr

ogr.UseExceptions()


def read(path):
    ds = ogr.Open(path)
    lyr = ds.GetLayer(0)
    d = lyr.GetLayerDefn()
    cf = next(d.GetFieldDefn(k).GetName() for k in range(d.GetFieldCount())
              if d.GetFieldDefn(k).GetName().lower().startswith('class'))
    w, c = [], []
    for f in lyr:
        w.append(bytes(f.GetGeometryRef().ExportToIsoWkb()))
        c.append(f.GetField(cf))
    return w, np.array(c)


t0 = time.time()
wa, ca = read(sys.argv[1])
wb, cb = read(sys.argv[2])
assert len(wa) == len(wb), 'different feature counts'
same_cls = np.mean(ca == cb)
ident = np.array([x == y for x, y in zip(wa, wb)])
ga = shapely.from_wkb(np.array(wa, dtype=object))
gb = shapely.from_wkb(np.array(wb, dtype=object))
area_a = shapely.area(ga)
rel_area = np.abs(shapely.area(gb) - area_a) / np.maximum(area_a, 1e-9)
print('polygons %d · same class in the same position %.4f · area agrees (< 1 %%) for %.4f' % (
    len(wa), same_cls, np.mean(rel_area < 0.01)))
print('bit-identical polygons: %d (%.2f %%)' % (ident.sum(), 100 * ident.mean()))
diff = np.nonzero(~ident)[0]
if len(diff):
    from scipy.spatial import cKDTree
    h = np.empty(len(diff))
    for k, i in enumerate(diff):            # vertex Hausdorff (borders are densely resampled by the smoother)
        pa, pb = shapely.get_coordinates(ga[i]), shapely.get_coordinates(gb[i])
        h[k] = max(cKDTree(pb).query(pa)[0].max(), cKDTree(pa).query(pb)[0].max())
    sd = shapely.area(shapely.symmetric_difference(shapely.make_valid(ga[diff]), shapely.make_valid(gb[diff])))
    va, vb = shapely.get_num_coordinates(ga[diff]), shapely.get_num_coordinates(gb[diff])
    print('differing polygons: %d · vertices %d .. %d' % (len(diff), va.min(), va.max()))
    print('  vertex Hausdorff distance (m): median %.3f · 95 %% %.3f · max %.3f' % (
        np.median(h), np.percentile(h, 95), h.max()))
    print('  share with distance < 1 m: %.1f %% · < 0.1 m: %.1f %%' % (100 * np.mean(h < 1), 100 * np.mean(h < 0.1)))
    print('  symmetric difference: total %.0f m2 = %.5f %% of the total area · max per polygon %.1f m2' % (
        sd.sum(), 100 * sd.sum() / area_a.sum(), sd.max()))
    print('  vertex count equal for %.1f %% of the differing polygons' % (100 * np.mean(va == vb)))
print('seconds %.0f' % (time.time() - t0))
