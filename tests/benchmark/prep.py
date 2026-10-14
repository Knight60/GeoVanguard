"""Benchmark inputs: nested clips of the forest-type raster + one synthetic raster,
each polygonized once with GDAL (4-connected, as GeoVanguard does) so that every
tool smooths exactly the same polygon coverage.

usage: python-qgis prep.py <forest raster> <out dir> <repo root>
"""
import os
import sys
import time

from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()
SRC, OUT, REPO = sys.argv[1:4]
sys.path.insert(0, os.path.join(REPO, 'tests'))
import synth  # noqa: E402

X0, Y0 = 250, 0                      # largest nodata-free square starts here
SIZES = [1000, 2000, 4000, 8000, 16000]
os.makedirs(OUT, exist_ok=True)


def polygonize(tif, name):
    gpkg = os.path.join(OUT, name + '.gpkg')
    shp = os.path.join(OUT, name + '.shp')
    for p in (gpkg,):
        if os.path.exists(p):
            os.remove(p)
    ds = gdal.Open(tif)
    band = ds.GetRasterBand(1)
    srs = osr.SpatialReference(wkt=ds.GetProjection())
    drv = ogr.GetDriverByName('GPKG')
    out = drv.CreateDataSource(gpkg)
    lyr = out.CreateLayer(name, srs, ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn('class_value', ogr.OFTInteger))
    t0 = time.time()
    gdal.Polygonize(band, band.GetMaskBand(), lyr, 0, [])
    n = lyr.GetFeatureCount()
    out = None
    gdal.VectorTranslate(shp, gpkg, format='ESRI Shapefile')
    print('%-10s %8d polygons  polygonize %.1fs' % (name, n, time.time() - t0), flush=True)


for s in SIZES:
    tif = os.path.join(OUT, 'ft%d.tif' % s)
    gdal.Translate(tif, SRC, srcWin=[X0, Y0, s, s], creationOptions=['COMPRESS=DEFLATE'])
    polygonize(tif, 'ft%d' % s)

syn = os.path.join(OUT, 'syn1000.tif')
synth.make_raster(syn, size=1000, classes=6, nodata_hole=False, seed=7)
polygonize(syn, 'syn1000')
