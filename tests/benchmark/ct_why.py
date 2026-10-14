import sys, numpy as np, shapely
from osgeo import ogr
ogr.UseExceptions()
def read(p):
    ds = ogr.Open(p); lyr = ds.GetLayer(0)
    return [bytes(f.GetGeometryRef().ExportToIsoWkb()) for f in lyr]
wa, wb = read(sys.argv[1]), read(sys.argv[2])
diff = [i for i in range(len(wa)) if wa[i] != wb[i]]
same_set = start = ringord = maxd = 0
for i in diff:
    a, b = shapely.from_wkb(wa[i]), shapely.from_wkb(wb[i])
    ca, cb = shapely.get_coordinates(a), shapely.get_coordinates(b)
    if ca.shape == cb.shape:
        maxd = max(maxd, np.abs(np.sort(ca, axis=0) - np.sort(cb, axis=0)).max())
    if set(map(tuple, ca)) == set(map(tuple, cb)): same_set += 1
    ra, rb = shapely.get_rings(a), shapely.get_rings(b)
    if [r.wkb for r in ra] != [r.wkb for r in rb] and sorted(r.wkb for r in ra) == sorted(r.wkb for r in rb): ringord += 1
print('differing', len(diff), '· same vertex set exactly', same_set, '· only ring order differs', ringord, '· max coord diff after sorting %.3g m' % maxd)
