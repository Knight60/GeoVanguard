"""Synthetic test data shared by the QGIS and standalone tests (numpy + GDAL only)."""
import numpy as np
from osgeo import gdal, osr


def make_raster(path, size=240, classes=6, nodata_hole=True, seed=1):
    rng = np.random.RandomState(seed)
    a = rng.rand(size, size)
    k = 9
    for _ in range(3):   # cheap blur → blobby regions
        a = (np.cumsum(np.cumsum(np.pad(a, k, mode='edge'), 0), 1))
        a = (a[2 * k:, 2 * k:] - a[:-2 * k, 2 * k:] - a[2 * k:, :-2 * k] + a[:-2 * k, :-2 * k])
        a = a[:size, :size] / (4.0 * k * k)
    qs = np.quantile(a, np.linspace(0, 1, classes + 1)[1:-1])
    cls = (np.digitize(a, qs) + 1).astype(np.uint8)
    if nodata_hole:
        cls[size // 3:size // 3 + 25, size // 3:size // 3 + 40] = 0
        cls[:12, -30:] = 0
    drv = gdal.GetDriverByName('GTiff')
    ds = drv.Create(path, size, size, 1, gdal.GDT_Byte)
    ds.SetGeoTransform((600000.0, 10.0, 0.0, 1500000.0, 0.0, -10.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32647)
    ds.SetProjection(srs.ExportToWkt())
    b = ds.GetRasterBand(1)
    b.WriteArray(cls)
    b.SetNoDataValue(0)
    ds = None
    return cls
