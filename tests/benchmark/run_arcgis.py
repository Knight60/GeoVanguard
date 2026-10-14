"""ArcGIS Pro: Smooth Polygon (per feature) and Smooth Shared Edges (topological).

usage: arcgispro-py3 python run_arcgis.py <tool> <input.shp> <output.gpkg> <scratch dir>
tool: arc_smoothpolygon | arc_sharededges
Prints: RESULT <tool> <seconds of the smoothing tool only>
"""
import os
import sys
import time

import arcpy

LABEL, INP, OUTP, SCR = sys.argv[1:5]
TOOL, _, PARAM = LABEL.partition(':')
TOL = '%s Meters' % (PARAM or 50)
arcpy.env.overwriteOutput = True
os.makedirs(SCR, exist_ok=True)
t_all = time.time()
gdb = os.path.join(SCR, TOOL + '.gdb')  # one scratch gdb per tool
if not arcpy.Exists(gdb):
    arcpy.management.CreateFileGDB(SCR, TOOL + '.gdb')
src = os.path.join(gdb, 'src')
arcpy.conversion.ExportFeatures(INP, src)
res = os.path.join(gdb, 'res')
part = None
if os.environ.get('ARC_PARTITIONS'):          # large data: Esri cartographic partitions
    part = os.path.join(gdb, 'partitions')
    arcpy.cartography.CreateCartographicPartitions(src, part, int(os.environ['ARC_PARTITIONS']), 'FEATURES')
    arcpy.env.cartographicPartitions = part
t0 = time.time()
if TOOL == 'arc_smoothpolygon':
    arcpy.cartography.SmoothPolygon(src, res, 'PAEK', TOL, error_option='NO_CHECK')
else:
    arcpy.cartography.SmoothSharedEdges(src, 'PAEK', TOL)    # edits src in place
    res = src
sec = time.time() - t0
if os.path.exists(OUTP):
    os.remove(OUTP)
out_shp = os.path.splitext(OUTP)[0] + '.shp'
arcpy.conversion.ExportFeatures(res, out_shp)
if part:
    out_part = os.path.splitext(OUTP)[0] + '_partitions.shp'
    arcpy.conversion.ExportFeatures(part, out_part)
print('RESULT %s %.2f total_with_io=%.2f%s' % (LABEL, sec, time.time() - t_all,
                                              ' partitions=%s' % os.environ['ARC_PARTITIONS'] if part else ''),
      flush=True)
