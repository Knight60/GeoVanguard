"""IoU of smoothed polygons against the fine truth raster.

usage: python-qgis iou.py <truth.tif> <polygons> <input px> <label> [json out]
The polygons are rasterized on the truth grid (pixel centres). Inside a frame
10 input pixels in from the edge:
  IoU_c = |out_c & truth_c| / |out_c | truth_c| for every class c in the truth
  mIoU  = mean over classes;  single class: mIoU = IoU of that class
  OA    = share of frame pixels whose class is right (background 0 counts)
"""
import json
import sys

import numpy as np
from osgeo import gdal, ogr

gdal.UseExceptions()
ogr.UseExceptions()
TRUTH, POLY, IPX, LABEL = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
JS = sys.argv[5] if len(sys.argv) > 5 else None

t = gdal.Open(TRUTH)
truth = t.GetRasterBand(1).ReadAsArray()
gt = t.GetGeoTransform()
mem = gdal.GetDriverByName('MEM').Create('', t.RasterXSize, t.RasterYSize, 1, gdal.GDT_UInt16)
mem.SetGeoTransform(gt)
mem.SetProjection(t.GetProjection())
src = ogr.Open(POLY)
lyr = src.GetLayer(0)
d = lyr.GetLayerDefn()
field = next(d.GetFieldDefn(k).GetName() for k in range(d.GetFieldCount())
             if d.GetFieldDefn(k).GetName().lower().startswith('class'))
gdal.RasterizeLayer(mem, [1], lyr, options=['ATTRIBUTE=' + field])
out = mem.GetRasterBand(1).ReadAsArray()

m = int(round(10 * IPX / gt[1]))
truth, out = truth[m:-m, m:-m], out[m:-m, m:-m]
ious = {}
for c in np.unique(truth):
    if c == 0:
        continue
    a, b = truth == c, out == c
    ious[int(c)] = float(np.count_nonzero(a & b) / max(np.count_nonzero(a | b), 1))
res = {'label': LABEL, 'mIoU': float(np.mean(list(ious.values()))), 'OA': float(np.mean(truth == out)),
       'iou': ious}
if JS:
    json.dump(res, open(JS, 'w'), indent=1)
print('IOU %s mIoU=%.5f OA=%.5f' % (LABEL, res['mIoU'], res['OA']), flush=True)
