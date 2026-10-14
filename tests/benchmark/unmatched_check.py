"""Are the unmatched segments real gaps/overlaps or collinear splits (T-junctions)?

usage: python-qgis unmatched_check.py <polygons> <frame inset m> <out.gpkg>
For every segment that occurs once inside the frame: does the boundary of some OTHER polygon
cover it (within 1 mm)? Covered = same border, only split into different vertices (no gap).
Not covered = a real mismatch. Writes the unmatched segments with a 'covered' flag.
"""
import sys

import numpy as np
import shapely
from osgeo import ogr, osr

ogr.UseExceptions()
PATH, INSET, OUT = sys.argv[1], float(sys.argv[2]), sys.argv[3]
ds = ogr.Open(PATH)
lyr = ds.GetLayer(0)
srs = lyr.GetSpatialRef()
ext = lyr.GetExtent()
g = np.array([shapely.from_wkb(bytes(f.GetGeometryRef().ExportToIsoWkb())) for f in lyr], dtype=object)
parts, pidx = shapely.get_parts(g, return_index=True)
rings, ridx = shapely.get_rings(parts, return_index=True)
owner = pidx[ridx]
xy, cidx = shapely.get_coordinates(rings, return_index=True)
same = cidx[1:] == cidx[:-1]
seg_owner = owner[cidx[:-1][same]]
q = np.round(xy / 0.001).astype(np.int64)
a, b = q[:-1][same], q[1:][same]
swap = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1]))
lo, hi = np.where(swap[:, None], b, a), np.where(swap[:, None], a, b)
keys = np.ascontiguousarray(np.hstack([lo, hi])).view([('', np.int64)] * 4).ravel()
_, inv, counts = np.unique(keys, return_inverse=True, return_counts=True)
pa, pb = xy[:-1][same], xy[1:][same]
x0, y0, x1, y1 = ext[0] + INSET, ext[2] + INSET, ext[1] - INSET, ext[3] - INSET
inside = ((pa[:, 0] > x0) & (pa[:, 0] < x1) & (pa[:, 1] > y0) & (pa[:, 1] < y1) &
          (pb[:, 0] > x0) & (pb[:, 0] < x1) & (pb[:, 1] > y0) & (pb[:, 1] < y1))
sel = np.nonzero(inside & (counts[inv] == 1))[0]
lines = shapely.linestrings(np.stack([pa[sel], pb[sel]], axis=1))
bnd = shapely.boundary(g)
tree = shapely.STRtree(bnd)
covered = []
for k, ln in enumerate(lines):
    near = tree.query(ln.buffer(0.002))
    near = near[near != seg_owner[sel[k]]]
    cov = sum(shapely.intersection(bnd[j], ln.buffer(0.002)).length for j in near)
    covered.append(cov >= 0.98 * ln.length)
covered = np.array(covered)
L = shapely.length(lines)
print('unmatched %d (%.0f m) · covered by a neighbour (split only) %d (%.0f m) · real mismatch %d (%.0f m)' % (
    len(sel), L.sum(), covered.sum(), L[covered].sum(), (~covered).sum(), L[~covered].sum()), flush=True)
out = ogr.GetDriverByName('GPKG').CreateDataSource(OUT)
ol = out.CreateLayer('unmatched', srs, ogr.wkbLineString)
ol.CreateField(ogr.FieldDefn('covered', ogr.OFTInteger))
for ln, cv in zip(lines, covered):
    f = ogr.Feature(ol.GetLayerDefn())
    f.SetGeometry(ogr.CreateGeometryFromWkb(shapely.to_wkb(ln)))
    f.SetField('covered', int(cv))
    ol.CreateFeature(f)
out = None
