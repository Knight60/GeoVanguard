# Settings for the benchmark scripts, sourced by every *.sh here.
# Each value can be overridden in the environment, e.g.  GV_BENCH=/data/gv_bench bash tune.sh
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
B="${B:-$HERE}"                                          # this folder (the scripts)
REPO="${REPO:-$(cd "$HERE/../.." && pwd)}"               # repository root (geovanguard package)
GV_BENCH="${GV_BENCH:-E:/tmp/gv_bench}"                  # work root: data/, iou/, eval/, logs
export GV_BENCH

# software (paths of the test machine)
QPY="${QPY:-/c/Program Files/QGIS 4.2.2/bin/python-qgis.bat}"        # QGIS 4.2.2 Python
GRASS="${GRASS:-/c/Program Files/QGIS 4.2.2/bin/grass85.bat}"         # GRASS GIS 8.5.0
ARC="${ARC:-/c/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe}"   # ArcGIS Pro 3.7.1
PYW="${PYW:-/c/Users/pisut/AppData/Local/Programs/Python/Python310/python.exe}"      # any Python with psutil
PYW_PATH="${PYW_PATH:-}"                                 # extra PYTHONPATH for PYW (e.g. where psutil is)
# Mapshaper runs through `npx.cmd -y mapshaper@0.7.83` (Node.js 23.8.0)

# source data (only needed to rebuild data/ and iou/ with prep.py and make_data.py)
FT_RASTER="${FT_RASTER:-D:/Developing/ForestType/Output/Sampling_20260504012328_1D_9000/05_Cleaned_MLP_Full.tif}"
GREEN_SHP="${GREEN_SHP:-E:/Temp/Region5TonRailI-Clip2.shp}"   # green-area polygons (single class)
