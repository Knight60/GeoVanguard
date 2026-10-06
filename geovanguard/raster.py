"""Raster → polygons with the GDAL bundled in QGIS (replaces rasterio/cv2 in
the original ForestType Vectorize.py).

GDAL Polygonize works scan-line by scan-line and writes straight to an OGR
layer, so memory use stays low on large rasters.  The optional sieve filter
(GDAL SieveFilter) plays the role of the original small-polygon elimination.
"""
import os
import time

from osgeo import gdal, ogr, osr

INTEGER_TYPES = (gdal.GDT_Byte, gdal.GDT_UInt16, gdal.GDT_Int16,
                 gdal.GDT_UInt32, gdal.GDT_Int32)
for _name in ('GDT_Int8', 'GDT_Int64', 'GDT_UInt64'):
    if hasattr(gdal, _name):
        INTEGER_TYPES += (getattr(gdal, _name),)


class RasterInfo(object):
    def __init__(self, ds, band_no):
        gt = ds.GetGeoTransform()
        if gt[2] != 0 or gt[4] != 0:
            raise ValueError('Rotated rasters are not supported.')
        self.gt = gt
        self.width = ds.RasterXSize
        self.height = ds.RasterYSize
        self.xmin = gt[0]
        self.xmax = gt[0] + gt[1] * self.width
        y0 = gt[3]
        y1 = gt[3] + gt[5] * self.height
        self.ymin = min(y0, y1)
        self.ymax = max(y0, y1)
        self.pixel = min(abs(gt[1]), abs(gt[5]))
        band = ds.GetRasterBand(band_no)
        self.data_type = band.DataType
        self.is_integer = band.DataType in INTEGER_TYPES
        self.nodata = band.GetNoDataValue()
        self.wkt = ds.GetProjection()


# GDAL Polygonize / SieveFilter read pixels as 32-bit signed integers
INT32_MIN, INT32_MAX = -2 ** 31, 2 ** 31 - 1
WIDE_INTEGER_TYPES = tuple(getattr(gdal, n) for n in ('GDT_UInt32', 'GDT_Int64', 'GDT_UInt64')
                           if hasattr(gdal, n))


def integer_error(ds, band_no):
    """None if the band holds integer classes GDAL can polygonize, else a message.

    Every integer type is accepted (Byte/UInt8, Int8, UInt16, Int16, UInt32,
    Int32, UInt64, Int64).  For UInt32/Int64/UInt64 the actual value range is
    checked, because GDAL Polygonize works on Int32 and would silently wrap
    larger class values.
    """
    band = ds.GetRasterBand(band_no)
    dtype = gdal.GetDataTypeName(band.DataType)
    if band.DataType not in INTEGER_TYPES:
        return ('Band {} is {} — this tool needs an integer classified raster '
                '(Byte/UInt8, Int8, UInt16, Int16, UInt32, Int32, UInt64, Int64). '
                'Convert it first, e.g. with Raster › Conversion › Translate '
                '(output data type: an integer type).').format(band_no, dtype)
    if band.DataType in WIDE_INTEGER_TYPES:
        lo, hi = band.ComputeRasterMinMax(False)
        if lo < INT32_MIN or hi > INT32_MAX:
            return ('Band {} ({}) has class values {:.0f}–{:.0f}, outside the 32-bit range '
                    'GDAL Polygonize can handle ({}–{}). Re-code the classes to smaller '
                    'values first.').format(band_no, dtype, lo, hi, INT32_MIN, INT32_MAX)
    return None


def open_raster(path, band_no):
    ds = gdal.Open(path)
    if ds is None:
        raise ValueError('GDAL cannot open raster: {}'.format(path))
    if band_no < 1 or band_no > ds.RasterCount:
        raise ValueError('Band {} does not exist.'.format(band_no))
    return ds


def _callback(feedback, report, pause=None):
    """GDAL progress callback: report(fraction); returning 0 cancels GDAL.

    While ``pause`` (a PauseControl) is paused the callback blocks, so GDAL
    stops where it is until resumed (or canceled).
    """
    def cb(complete, message, data):
        report(complete)
        while pause is not None and pause.is_paused() and not feedback.isCanceled():
            time.sleep(0.3)
        return 0 if feedback.isCanceled() else 1
    return cb


def sieve(ds, band_no, threshold, eight, out_path, feedback, report, pause=None):
    """GDAL SieveFilter into a tiled GeoTIFF; returns the opened dataset."""
    src = ds.GetRasterBand(band_no)
    drv = gdal.GetDriverByName('GTiff')
    dst_ds = drv.Create(out_path, ds.RasterXSize, ds.RasterYSize, 1, src.DataType,
                        ['TILED=YES', 'COMPRESS=LZW', 'BIGTIFF=IF_SAFER'])
    dst_ds.SetGeoTransform(ds.GetGeoTransform())
    dst_ds.SetProjection(ds.GetProjection())
    dst = dst_ds.GetRasterBand(1)
    nodata = src.GetNoDataValue()
    if nodata is not None:
        dst.SetNoDataValue(nodata)
    err = gdal.SieveFilter(src, src.GetMaskBand(), dst, int(threshold), 8 if eight else 4,
                           callback=_callback(feedback, report, pause))
    if err != 0:
        raise ValueError('GDAL SieveFilter failed (code {}).'.format(err))
    dst_ds.FlushCache()
    return dst_ds


def polygonize(ds, band_no, out_path, eight, feedback, report, pause=None):
    """GDAL Polygonize into a GeoPackage layer 'raw' with field 'class_value'."""
    band = ds.GetRasterBand(band_no)
    info = RasterInfo(ds, band_no)
    if os.path.exists(out_path):
        ogr.GetDriverByName('GPKG').DeleteDataSource(out_path)
    out = ogr.GetDriverByName('GPKG').CreateDataSource(out_path)
    srs = None
    if info.wkt:
        srs = osr.SpatialReference()
        srs.ImportFromWkt(info.wkt)
    lyr = out.CreateLayer('raw', srs=srs, geom_type=ogr.wkbPolygon,
                          options=['SPATIAL_INDEX=NO'])
    lyr.CreateField(ogr.FieldDefn('class_value',
                                  ogr.OFTInteger64 if info.is_integer else ogr.OFTReal))
    options = ['8CONNECTED=8'] if eight else []
    fn = gdal.Polygonize if info.is_integer else gdal.FPolygonize
    lyr.StartTransaction()
    err = fn(band, band.GetMaskBand(), lyr, 0, options, callback=_callback(feedback, report, pause))
    lyr.CommitTransaction()
    n = lyr.GetFeatureCount()
    out = None
    if err != 0:
        raise ValueError('GDAL Polygonize failed (code {}).'.format(err))
    return n


def iter_polygons(path):
    """Yield (fid, class_value, wkb) from the polygonized GeoPackage."""
    ds = ogr.Open(path)
    lyr = ds.GetLayer(0)
    lyr.ResetReading()
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        yield f.GetFID(), f.GetField(0), bytes(g.ExportToIsoWkb())
    ds = None
