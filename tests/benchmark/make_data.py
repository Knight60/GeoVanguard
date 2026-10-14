"""IoU benchmark data: a fine 'truth' raster and a coarse input polygonized by GDAL.

usage: python-qgis make_data.py <out dir> <forest raster> <green area shp>

  syn_multi_{A,B}   synthetic smooth field, 6 classes; truth 2 m, input 10 m (majority of 5x5)
  syn_single_{A,B}  same field type, one class + nodata; truth 2 m, input 10 m
  ft_multi_{A,B}    forest-type map; truth = 10 m map, input = 30 m (mode of 3x3)
  ga_single_{A,B}   green-area map (rasterized at 10 m); truth 10 m, input 30 m (majority of 3x3)
A = tuning set, B = test set (another seed / another area).
Single-class inputs keep nodata (0) for the background: GDAL polygonize skips
masked pixels, so the background becomes no polygon at all and every class
border is an outer polygon edge without a neighbour.
"""
import json
import os
import sys
import time

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()
OUT, FOREST, GREEN = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)
SRS = osr.SpatialReference()
SRS.ImportFromEPSG(32647)
info = {}


def write_tif(path, arr, x0, y0, px, nodata=0):
    drv = gdal.GetDriverByName('GTiff')
    ds = drv.Create(path, arr.shape[1], arr.shape[0], 1, gdal.GDT_UInt16, ['COMPRESS=DEFLATE'])
    ds.SetGeoTransform((x0, px, 0, y0, 0, -px))
    ds.SetProjection(SRS.ExportToWkt())
    b = ds.GetRasterBand(1)
    b.WriteArray(arr)
    b.SetNoDataValue(nodata)
    ds = None


def polygonize(tif, name):
    gpkg = os.path.join(OUT, name + '.gpkg')
    if os.path.exists(gpkg):
        os.remove(gpkg)
    ds = gdal.Open(tif)
    band = ds.GetRasterBand(1)
    out = ogr.GetDriverByName('GPKG').CreateDataSource(gpkg)
    lyr = out.CreateLayer(name, SRS, ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn('class_value', ogr.OFTInteger))
    gdal.Polygonize(band, band.GetMaskBand(), lyr, 0, [])
    n = lyr.GetFeatureCount()
    out = None
    gdal.VectorTranslate(os.path.join(OUT, name + '.shp'), gpkg, format='ESRI Shapefile')
    return n


def block_mode(a, k, single=False):
    """Mode of k x k blocks (single class: 1 where more than half the block is 1)."""
    h, w = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    b = a[:h, :w].reshape(h // k, k, w // k, k)
    if single:
        return ((b == 1).sum(axis=(1, 3)) * 2 > k * k).astype(np.uint16)
    classes = [c for c in np.unique(a) if c != 0]
    counts = np.stack([(b == c).sum(axis=(1, 3)) for c in classes])
    mode = np.array(classes, dtype=np.uint16)[counts.argmax(0)]
    mode[counts.max(0) == 0] = 0
    return mode


def save(name, truth, t_px, inp, i_px, x0, y0):
    write_tif(os.path.join(OUT, name + '_truth.tif'), truth, x0, y0, t_px)
    tif = os.path.join(OUT, name + '_in.tif')
    write_tif(tif, inp, x0, y0, i_px)
    n = polygonize(tif, name)
    info[name] = {'truth_px': t_px, 'input_px': i_px, 'polygons': n,
                  'size_km': round(inp.shape[1] * i_px / 1000.0, 2)}
    print('%-14s %6d polygons  input %dx%d @ %g m  truth @ %g m' % (
        name, n, inp.shape[1], inp.shape[0], i_px, t_px), flush=True)


# ── synthetic: smooth random field (sum of cosines) on a 2 m grid ────────────
L, TP, IP = 6000.0, 2.0, 10.0
n = int(L / TP)
c = (np.arange(n) + 0.5) * TP


def field(seed, lam_min, lam_max, k=60):
    rng = np.random.RandomState(seed)
    f = np.zeros((n, n), np.float32)
    for _ in range(k):
        lam = np.exp(rng.uniform(np.log(lam_min), np.log(lam_max)))
        th = rng.uniform(0, np.pi)
        kx, ky = 2 * np.pi / lam * np.cos(th), 2 * np.pi / lam * np.sin(th)
        ph = rng.uniform(0, 2 * np.pi)
        amp = (lam / lam_max) ** 0.5
        # cos(kx x + ky y + ph) = cos(a)cos(b) - sin(a)sin(b), a = kx x + ph, b = ky y
        a = kx * c + ph
        bb = ky * c
        f += amp * (np.outer(np.cos(bb), np.cos(a)) - np.outer(np.sin(bb), np.sin(a))).astype(np.float32)
    return f


for tag, seed in (('A', 11), ('B', 12)):
    t0 = time.time()
    f = field(seed, 250.0, 3000.0)
    qs = np.quantile(f, np.linspace(0, 1, 7)[1:-1])
    multi = (np.digitize(f, qs) + 1).astype(np.uint16)
    save('syn_multi_' + tag, multi, TP, block_mode(multi, 5), IP, 600000.0, 1500000.0 + L)
    g = field(seed + 100, 120.0, 1500.0)
    single = (g > np.quantile(g, 0.7)).astype(np.uint16)       # 30 % class, many small patches
    save('syn_single_' + tag, single, TP, block_mode(single, 5, single=True), IP, 600000.0, 1500000.0 + L)
    print('  field %s %.1fs' % (tag, time.time() - t0), flush=True)

# ── forest type: truth 10 m, input 30 m ───────────────────────────────────────
ds = gdal.Open(FOREST)
gt = ds.GetGeoTransform()
S = 1200
for tag, (xo, yo) in (('A', (2000, 2000)), ('B', (8000, 8000))):
    a = ds.GetRasterBand(1).ReadAsArray(xo, yo, S, S).astype(np.uint16)
    x0, y0 = gt[0] + xo * gt[1], gt[3] + yo * gt[5]
    save('ft_multi_' + tag, a, 10.0, block_mode(a, 3), 30.0, x0, y0)

# ── green area: rasterize at 10 m, west half = A, east half = B ──────────────
src = ogr.Open(GREEN)
lyr = src.GetLayer(0)
xmin, xmax, ymin, ymax = lyr.GetExtent()
xmin, ymax = np.floor(xmin / 30) * 30, np.ceil(ymax / 30) * 30
w, h = int(np.ceil((xmax - xmin) / 30)) * 3, int(np.ceil((ymax - ymin) / 30)) * 3
mem = gdal.GetDriverByName('MEM').Create('', w, h, 1, gdal.GDT_UInt16)
mem.SetGeoTransform((xmin, 10, 0, ymax, 0, -10))
mem.SetProjection(SRS.ExportToWkt())
gdal.RasterizeLayer(mem, [1], lyr, burn_values=[1])
g = mem.GetRasterBand(1).ReadAsArray()
half = (w // 2 // 3) * 3
for tag, sl, x0 in (('A', slice(0, half), xmin), ('B', slice(half, w - (w - half) % 3), xmin + half * 10)):
    a = g[:, sl]
    save('ga_single_' + tag, a, 10.0, block_mode(a, 3, single=True), 30.0, x0, ymax)

json.dump(info, open(os.path.join(OUT, 'datasets.json'), 'w'), indent=1)
