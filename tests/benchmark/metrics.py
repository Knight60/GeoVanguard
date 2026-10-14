"""Same metrics for every tool's output (QGIS Python: GDAL/OGR + shapely 2).

usage: python-qgis metrics.py <input polygons> <output polygons> <label> <json out>

Inside a frame 100 m in from the input extent (outer edges are treated
differently by the tools and are not a neighbour-to-neighbour property):
  overlap_m2   sum(area) - area(union)           area covered twice
  gap_m2       area(frame) - area(union)         area covered by no polygon
  gap_parts    number of separate gap pieces (slivers)
  area_total_% change of the total polygon area
  class_mean_% mean |change| of the per-class area, class_max_% the largest
  offset_m     sum_c area(class_in XOR class_out) / class boundary length
               = mean displacement of the class borders
On whole layers:
  invalid      output polygons that are not OGC-valid
  sharp_%      output vertices turning > 45 deg (staircase corners left)
  vertex_ratio output / input vertices; perim_ratio output / input perimeter
"""
import json
import math
import sys

import numpy as np
import shapely
from osgeo import ogr

ogr.UseExceptions()
INP, OUTP, LABEL, JS = sys.argv[1:5]
INSET = float(sys.argv[5]) if len(sys.argv) > 5 else 100.0


def load(path):
    ds = ogr.Open(path)
    lyr = ds.GetLayer(0)
    d = lyr.GetLayerDefn()
    names = [d.GetFieldDefn(k).GetName() for k in range(d.GetFieldCount())]
    cf = next(n for n in names if n.lower().startswith('class'))
    geoms, cls = [], []
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None or g.IsEmpty():
            continue
        geoms.append(shapely.from_wkb(bytes(g.ExportToIsoWkb())))
        cls.append(f.GetField(cf))
    return np.array(geoms, dtype=object), np.array(cls), lyr.GetExtent()


def sharp_share(geoms):
    sharp = total = 0
    for ring in shapely.get_rings(shapely.get_parts(geoms)):
        c = shapely.get_coordinates(ring)
        d = np.diff(c, axis=0)
        d = d[np.hypot(d[:, 0], d[:, 1]) > 1e-9]
        if len(d) < 3:
            continue
        a = np.arctan2(d[:, 1], d[:, 0])
        turn = np.abs((np.diff(np.concatenate([a, a[:1]])) + np.pi) % (2 * np.pi) - np.pi)
        sharp += int(np.count_nonzero(turn > math.radians(45)))
        total += len(turn)
    return 100.0 * sharp / max(total, 1), total, sharp


g_in, c_in, ext = load(INP)
g_out, c_out, _ = load(OUTP)

if len(g_in) > 100000:
    # large data (seam tests): per-class unions and the GEOS coverage validator take hours here
    # (one huge background polygon touches almost everything). Shared-edge matching instead: in a
    # closed coverage every interior segment occurs exactly twice (once per neighbour); a segment
    # that occurs once inside the frame marks a gap, an overlap or a mismatched border.
    import time
    t0 = time.time()
    invalid = int(np.count_nonzero(~shapely.is_valid(g_out)))
    rings = shapely.get_rings(shapely.get_parts(g_out))
    xy, idx = shapely.get_coordinates(rings, return_index=True)
    same = idx[1:] == idx[:-1]
    q = np.round(xy / 0.001).astype(np.int64)                 # 1 mm grid
    a, b = q[:-1][same], q[1:][same]
    swap = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1]))
    lo, hi = np.where(swap[:, None], b, a), np.where(swap[:, None], a, b)
    keys = np.ascontiguousarray(np.hstack([lo, hi])).view([('', np.int64)] * 4).ravel()
    _, inv, counts = np.unique(keys, return_inverse=True, return_counts=True)
    c = counts[inv]
    x0, y0, x1, y1 = ext[0] + INSET, ext[2] + INSET, ext[1] - INSET, ext[3] - INSET
    pa, pb = xy[:-1][same], xy[1:][same]
    inside = ((pa[:, 0] > x0) & (pa[:, 0] < x1) & (pa[:, 1] > y0) & (pa[:, 1] < y1) &
              (pb[:, 0] > x0) & (pb[:, 0] < x1) & (pb[:, 1] > y0) & (pb[:, 1] < y1))
    seg_len = np.hypot(*(pb - pa).T)
    single = inside & (c == 1)
    res = {'label': LABEL, 'mode': 'shared-edge matching (large data)', 'n_in': int(len(g_in)),
           'n_out': int(len(g_out)), 'invalid': invalid,
           'interior_segments': int(np.count_nonzero(inside)),
           'unmatched_segments': int(np.count_nonzero(single)),
           'unmatched_length_m': float(seg_len[single].sum()),
           'over_shared_segments': int(np.count_nonzero(inside & (c > 2))),
           'seconds': round(time.time() - t0, 1)}
    with open(JS, 'w') as f:
        json.dump(res, f, indent=1)
    print('METRICS', json.dumps(res), flush=True)
    raise SystemExit(0)
frame = shapely.box(ext[0] + INSET, ext[2] + INSET, ext[1] - INSET, ext[3] - INSET)

invalid = int(np.count_nonzero(~shapely.is_valid(g_out)))
g_out_v = shapely.make_valid(g_out)
cut_out = shapely.intersection(g_out_v, frame)
cut_in = shapely.intersection(g_in, frame)
union_out = shapely.union_all(cut_out)
fa = frame.area
overlap = float(shapely.area(cut_out).sum() - union_out.area)
# single class: the background is nodata (no polygons), so 'not covered' is not a gap
coverage = shapely.union_all(cut_in).area / fa
full = coverage > 0.999
gaps = shapely.difference(frame, union_out) if full else shapely.Polygon()
gap_parts = [p for p in shapely.get_parts(gaps) if p.area > 0.01]

def polygonal(g):
    """Polygon parts only (clipping to the frame also yields lines and points)."""
    parts = [p for p in shapely.get_parts(g) if p.geom_type == 'Polygon']
    return shapely.union_all(parts) if parts else shapely.Polygon()


offset_num = offset_den = 0.0
class_changes = []
abs_change = 0.0
a_in_tot = a_out_tot = 0.0
for c in np.unique(c_in):
    u_in = polygonal(shapely.union_all(cut_in[c_in == c]))
    sel = cut_out[c_out == c]
    u_out = polygonal(shapely.union_all(sel)) if len(sel) else shapely.Polygon()
    a_in, a_out = u_in.area, float(shapely.area(sel).sum())
    a_in_tot += a_in
    a_out_tot += a_out
    if a_in > 0:
        class_changes.append(100.0 * (a_out - a_in) / a_in)
        abs_change += abs(a_out - a_in)
    offset_num += shapely.symmetric_difference(u_in, u_out).area
    offset_den += shapely.intersection(u_in.boundary, frame.buffer(-1.0)).length

sharp, n_out_v, n_sharp = sharp_share(g_out)
_, n_in_v, _ = sharp_share(g_in)
km = float(shapely.length(g_out_v).sum()) / 2000.0     # each border is counted by both sides
res = {
    'label': LABEL,
    'n_in': int(len(g_in)), 'n_out': int(len(g_out)), 'invalid': invalid,
    'overlap_m2': overlap, 'overlap_ppm': 1e6 * overlap / fa,
    'coverage': coverage,
    'gap_m2': float(gaps.area) if full else None, 'gap_ppm': 1e6 * gaps.area / fa if full else None,
    'gap_parts': len(gap_parts) if full else None,
    'area_total_%': 100.0 * (a_out_tot - a_in_tot) / a_in_tot,
    'class_mean_%': float(np.mean(np.abs(class_changes))),
    'class_max_%': float(np.max(np.abs(class_changes))),
    'class_wmean_%': 100.0 * abs_change / a_in_tot,
    'offset_m': offset_num / offset_den,
    'sharp_%': sharp,
    'sharp_per_km': n_sharp / 2.0 / km,
    'vertex_ratio': n_out_v / n_in_v,
    'perim_ratio': float(shapely.length(g_out_v).sum() / shapely.length(g_in).sum()),
}
with open(JS, 'w') as f:
    json.dump(res, f, indent=1)
print('METRICS', json.dumps(res), flush=True)
