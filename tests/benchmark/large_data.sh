#!/usr/bin/env bash
# Large data: tile seams (ft16000, three tile sizes) and interrupt/resume (ftfull).
. "$(dirname "${BASH_SOURCE[0]}")/config.sh"
SIG=$(python -c "import json; print(json.load(open('$GV_BENCH/eval/best.json'))['syn_multi']['gv_gaussian']['param'])")
echo "=== scaling, GeoVanguard automatic workers (missed in evaluate.sh)"
for ds in ft1000 ft2000 ft4000 ft8000 ft16000 ftfull; do
  r=$(D="$GV_BENCH/data" O="$GV_BENCH/scale" MEM=1 LIMIT=3600 bash "$B/run_one.sh" gv_gaussian:$SIG $ds $([ $ds = ft8000 ] && echo m))
  echo "$r" | cut -c1-200
  case "$r" in *FAILED*) echo "--- gv_gaussian stops at $ds"; break ;; esac
done
export D="$GV_BENCH/data" O="$GV_BENCH/seams"
mkdir -p "$O"
echo "=== tile seams (ft16000, sigma $SIG)"
for tile in 0 8000 2000; do
  if [ $tile = 0 ]; then unset GV_TILE; else export GV_TILE=$tile; fi
  r=$(LIMIT=3600 bash "$B/run_one.sh" gv_gaussian:$SIG ft16000 | grep RESULT)
  echo "tile=$tile $r"
  mv "$O/ft16000_gv_gaussian_p$SIG.gpkg" "$O/tile$tile.gpkg"
  "$QPY" "$B/metrics.py" "$D/ft16000.gpkg" "$O/tile$tile.gpkg" "tile$tile" "$O/tile$tile.json" 2>&1 | grep -E "^METRICS|Error" | cut -c1-220
done
unset GV_TILE
for tile in 8000 2000; do "$QPY" "$B/digest.py" "$O/tile0.gpkg" "$O/tile$tile.gpkg" 2>/dev/null | grep -E "^SAME"; done
echo "=== ArcGIS Smooth Shared Edges on ft16000: without and with cartographic partitions"
TOL=$(python -c "import json; print(json.load(open('$GV_BENCH/eval/best.json'))['syn_multi']['arc_sharededges']['param'])")
r=$(MEM=1 LIMIT=3600 bash "$B/run_one.sh" arc_sharededges:$TOL ft16000 | grep RESULT); echo "no partitions: $r"
[ -f "$O/ft16000_arc_sharededges_p$TOL.shp" ] && for e in shp shx dbf prj cpg; do mv "$O/ft16000_arc_sharededges_p$TOL.$e" "$O/arc_nopart.$e" 2>/dev/null; done
r=$(ARC_PARTITIONS=20000 MEM=1 LIMIT=3600 bash "$B/run_one.sh" arc_sharededges:$TOL ft16000 | grep RESULT); echo "partitions 20000: $r"
[ -f "$O/ft16000_arc_sharededges_p$TOL.shp" ] && for e in shp shx dbf prj cpg; do mv "$O/ft16000_arc_sharededges_p$TOL.$e" "$O/arc_part.$e" 2>/dev/null; done
for f in arc_nopart arc_part; do
  [ -f "$O/$f.shp" ] && "$QPY" "$B/metrics.py" "$D/ft16000.gpkg" "$O/$f.shp" "$f" "$O/$f.json" 2>&1 | grep -E "^METRICS|Error" | cut -c1-220
done
[ -f "$O/arc_part.shp" ] && [ -f "$O/arc_nopart.shp" ] && "$QPY" "$B/digest.py" "$O/arc_nopart.shp" "$O/arc_part.shp" 2>/dev/null | grep -E "^SAME"
echo "=== interrupt and resume (ftfull, killed at 50 %)"
PYTHONPATH="$PYW_PATH" "$PYW" "$B/resume_test.py" "$SIG" "$D/ftfull.gpkg" "$GV_BENCH/resume" 0.5
echo "=== large data done $(date)"
