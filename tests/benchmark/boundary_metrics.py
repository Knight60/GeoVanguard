"""Boundary accuracy and structure against the fine truth raster.

usage: python-qgis boundary_metrics.py <truth.tif> <output polygons> <input polygons> <input px> <label> <json>

Raster part (output rasterized on the truth grid, frame 10 input px in):
  dist_mean_m / dist_p95_m / hausdorff_m  distance from output borders to the true borders
                                           (symmetric: both directions pooled; max of both for Hausdorff)
  bf1          Boundary F1 at a tolerance of 0.5 input px (precision: output border pixels near a
               true border; recall: true border pixels near an output border)
  perim_ratio_truth  output / true border length (same rasterization on both sides, so the
               pixel bias cancels; staircase input is > 1, a good smoothing is close to 1)
Vector part:
  n_in / n_out, adjacency pairs (neighbours sharing a border of positive length) in / out
"""
import json
import sys

import numpy as np
import shapely
from osgeo import gdal, ogr
from scipy import ndimage

gdal.UseExceptions()
ogr.UseExceptions()
TRUTH, OUTP, INP, IPX, LABEL, JS = sys.argv[1:7]
IPX = float(IPX)


def rasterize(path, ref):
    mem = gdal.GetDriverByName('MEM').Create('', ref.RasterXSize, ref.RasterYSize, 1, gdal.GDT_UInt16)
    mem.SetGeoTransform(ref.GetGeoTransform())
    mem.SetProjection(ref.GetProjection())
    ds = ogr.Open(path)                       # keep the data source alive
    lyr = ds.GetLayer(0)
    d = lyr.GetLayerDefn()
    f = next(d.GetFieldDefn(k).GetName() for k in range(d.GetFieldCount())
             if d.GetFieldDefn(k).GetName().lower().startswith('class'))
    gdal.RasterizeLayer(mem, [1], lyr, options=['ATTRIBUTE=' + f])
    return mem.GetRasterBand(1).ReadAsArray()


def borders(a):
    b = np.zeros(a.shape, bool)
    b[:, 1:] |= a[:, 1:] != a[:, :-1]
    b[1:, :] |= a[1:, :] != a[:-1, :]
    return b


t = gdal.Open(TRUTH)
px = t.GetGeoTransform()[1]
truth = t.GetRasterBand(1).ReadAsArray()
out = rasterize(OUTP, t)
m = int(round(10 * IPX / px))
bt, bo = borders(truth)[m:-m, m:-m], borders(out)[m:-m, m:-m]
dt_t = ndimage.distance_transform_edt(~bt) * px      # distance to the nearest true border
dt_o = ndimage.distance_transform_edt(~bo) * px      # distance to the nearest output border
d_o2t, d_t2o = dt_t[bo], dt_o[bt]
pooled = np.concatenate([d_o2t, d_t2o])
tol = 0.5 * IPX
prec = float(np.mean(d_o2t <= tol)) if d_o2t.size else 0.0
rec = float(np.mean(d_t2o <= tol)) if d_t2o.size else 0.0


def adjacency(path):
    ds = ogr.Open(path)
    lyr = ds.GetLayer(0)
    g = np.array([shapely.from_wkb(bytes(f.GetGeometryRef().ExportToIsoWkb())) for f in lyr], dtype=object)
    g = shapely.make_valid(g)
    tree = shapely.STRtree(g)
    i, j = tree.query(g, predicate='intersects')
    keep = i < j
    i, j = i[keep], j[keep]
    if not len(i):
        return len(g), 0
    shared = shapely.length(shapely.intersection(shapely.boundary(g[i]), shapely.boundary(g[j])))
    return len(g), int(np.count_nonzero(shared > 1e-6))


n_in, adj_in = adjacency(INP)
n_out, adj_out = adjacency(OUTP)
res = {
    'label': LABEL,
    'dist_mean_m': float(pooled.mean()), 'dist_p95_m': float(np.percentile(pooled, 95)),
    'hausdorff_m': float(max(d_o2t.max(), d_t2o.max())),
    'bf1': 2 * prec * rec / (prec + rec) if prec + rec else 0.0, 'bf_precision': prec, 'bf_recall': rec,
    'perim_ratio_truth': float(bo.sum() / max(bt.sum(), 1)),
    'n_in': n_in, 'n_out': n_out, 'adj_in': adj_in, 'adj_out': adj_out,
}
json.dump(res, open(JS, 'w'), indent=1)
print('BOUNDARY ' + json.dumps(res), flush=True)
