"""Staircase / naturalness of borders: length share on axis-parallel segments, orientation entropy,
sharp turns (>= 60 deg) per km. Pixel staircases: axis share ~1, entropy low, many 90-deg turns."""
import json
import os
import numpy as np, shapely
from osgeo import ogr
ogr.UseExceptions()
BENCH = os.environ.get('GV_BENCH', 'E:/tmp/gv_bench')
E = os.path.join(BENCH, 'eval'); I = os.path.join(BENCH, 'iou')
best = json.load(open(os.path.join(E, 'best.json')))
TOOLS = ['gv_gaussian', 'arc_sharededges', 'arc_smoothpolygon', 'qgis_smooth', 'grass_sliding_averaging', 'grass_chaiken', 'mapshaper']

def rings(path):
    ds = ogr.Open(path); lyr = ds.GetLayer(0)
    g = [shapely.from_wkb(bytes(f.GetGeometryRef().ExportToIsoWkb())) for f in lyr if f.GetGeometryRef()]
    return shapely.get_rings(shapely.get_parts(np.array(g, dtype=object)))

def stats(path):
    L = []; A = []; turns = 0; total = 0.0
    for r in rings(path):
        xy = shapely.get_coordinates(r)
        d = np.diff(xy, axis=0); l = np.hypot(d[:, 0], d[:, 1]); k = l > 1e-9
        d, l = d[k], l[k]
        if len(l) < 3: continue
        a = np.degrees(np.arctan2(d[:, 1], d[:, 0]))
        L.append(l); A.append(a % 180)
        t = np.abs((np.diff(np.r_[a, a[0]]) + 180) % 360 - 180)
        turns += np.count_nonzero(t >= 60); total += l.sum()
    l = np.concatenate(L); a = np.concatenate(A)
    off = np.minimum(a % 90, 90 - a % 90)
    axis = l[off < 1].sum() / l.sum()
    h, _ = np.histogram(a, bins=36, range=(0, 180), weights=l); p = h / h.sum(); p = p[p > 0]
    ent = -(p * np.log(p)).sum() / np.log(36)
    return axis, ent, turns / (total / 1000)

out = {}
for ds in ('syn_single_B', 'syn_multi_B', 'ga_single_B', 'ft_multi_B'):
    kind = ds[:-2]; rows = {}
    rows['input'] = stats(os.path.join(I, ds + '.gpkg'))
    for t in TOOLS:
        p = os.path.join(E, '%s_%s_p%s.gpkg' % (ds, t, best[kind][t]['param'].replace(',', '_')))
        if not os.path.exists(p): p = p[:-5] + '.shp'
        rows[t] = stats(p)
    out[ds] = rows
    print(ds)
    for k, (ax, en, tu) in rows.items():
        print('  %-24s axis-parallel %5.1f %%  orientation entropy %.3f  sharp turns %6.1f /km' % (k, 100 * ax, en, tu))
json.dump(out, open(os.path.join(os.environ.get('GV_BENCH', 'E:/tmp/gv_bench'), 'eval', 'shape.json'), 'w'), indent=1)
