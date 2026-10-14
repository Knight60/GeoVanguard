"""Count nodata (0) pixels in a classified raster and where they are."""
import sys

import numpy as np
from osgeo import gdal

ds = gdal.Open(sys.argv[1])
a = ds.GetRasterBand(1).ReadAsArray()
nd = a == 0
print('nodata pixels %d of %d (%.4f %%)' % (nd.sum(), a.size, 100.0 * nd.mean()))
if nd.any():
    r, c = np.nonzero(nd)
    print('rows %d-%d, cols %d-%d' % (r.min(), r.max(), c.min(), c.max()))
