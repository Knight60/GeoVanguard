"""Distance from each unmatched segment to the nearest nodata pixel of the input raster."""
import sys

import numpy as np
import shapely
from osgeo import gdal, ogr
from scipy import ndimage

ogr.UseExceptions()
ds = gdal.Open(sys.argv[1])
gt = ds.GetGeoTransform()
nd = ds.GetRasterBand(1).ReadAsArray() == 0
dist = ndimage.distance_transform_edt(~nd) * gt[1]          # metres to the nearest nodata pixel
src = ogr.Open(sys.argv[2])
d = []
for f in src.GetLayer(0):
    ln = shapely.from_wkb(bytes(f.GetGeometryRef().ExportToIsoWkb()))
    for x, y in shapely.get_coordinates(ln.interpolate(np.linspace(0, 1, 5), normalized=True)):
        c, r = int((x - gt[0]) / gt[1]), int((y - gt[3]) / gt[5])
        d.append(dist[min(max(r, 0), nd.shape[0] - 1), min(max(c, 0), nd.shape[1] - 1)])
d = np.array(d)
print('sample points %d · distance to nearest nodata pixel: median %.1f m, 95 %% %.1f m, max %.1f m' % (
    len(d), np.median(d), np.percentile(d, 95), d.max()))
print('share within 50 m of nodata: %.1f %%' % (100 * np.mean(d <= 50)))
