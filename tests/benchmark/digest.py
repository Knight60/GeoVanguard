"""SHA-256 of the sorted WKB of all features (order-independent fingerprint).

usage: python-qgis digest.py <layer> [<layer2>]
With two layers also prints the area of their symmetric difference per class.
"""
import hashlib
import sys

import numpy as np
import shapely
from osgeo import ogr

ogr.UseExceptions()


def read(path):
    ds = ogr.Open(path)                       # keep the data source alive
    lyr = ds.GetLayer(0)
    d = lyr.GetLayerDefn()
    cf = next(d.GetFieldDefn(k).GetName() for k in range(d.GetFieldCount())
              if d.GetFieldDefn(k).GetName().lower().startswith('class'))
    w, c = [], []
    for f in lyr:
        w.append(bytes(f.GetGeometryRef().ExportToIsoWkb()))
        c.append(f.GetField(cf))
    return w, np.array(c)


def sha(w):
    h = hashlib.sha256()
    for x in sorted(w):
        h.update(x)
    return h.hexdigest()


w1, c1 = read(sys.argv[1])
print('DIGEST %s %d %s' % (sys.argv[1], len(w1), sha(w1)))
if len(sys.argv) > 2:
    w2, c2 = read(sys.argv[2])
    print('DIGEST %s %d %s' % (sys.argv[2], len(w2), sha(w2)))
    if sha(w1) == sha(w2) or len(w1) > 100000:      # identical, or too large for class unions
        print('SAME %s' % (sha(w1) == sha(w2)))
        raise SystemExit(0)
    g1 = np.array([shapely.from_wkb(x) for x in w1], dtype=object)
    g2 = np.array([shapely.from_wkb(x) for x in w2], dtype=object)
    diff = 0.0
    for c in np.unique(c1):
        diff += shapely.symmetric_difference(shapely.union_all(g1[c1 == c]),
                                             shapely.union_all(g2[c2 == c])).area
    print('SAME %s symdiff_m2=%.3f' % (sha(w1) == sha(w2), diff))
