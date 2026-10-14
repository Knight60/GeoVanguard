#!/usr/bin/env bash
# Best parameters (from tune.csv) on the test sets B: IoU + topology metrics + time,
# then run time versus size on the real 10 m forest clips with the syn_multi parameters.
. "$(dirname "${BASH_SOURCE[0]}")/config.sh"
IOU_D="$GV_BENCH/iou"
EO="$GV_BENCH/eval"
mkdir -p "$EO"
until grep -q "=== chaiken extra done" $GV_BENCH/tune.log 2>/dev/null; do sleep 20; done
python "$B/pick_best.py" $GV_BENCH/iou_out/tune.csv "$EO/best.json"
param() { python -c "import json,sys; b=json.load(open('$EO/best.json')); print(b[sys.argv[1]][sys.argv[2]]['param'])" "$1" "$2" 2>/dev/null; }
TOOLS="gv_gaussian arc_sharededges arc_smoothpolygon qgis_smooth grass_sliding_averaging grass_chaiken mapshaper"

echo "=== test sets"
for kind in syn_multi syn_single ft_multi ga_single; do
  ds=${kind}_B
  px=$(python -c "import json;print(json.load(open('$IOU_D/datasets.json'))['$ds']['input_px'])")
  inset=$(python -c "print(10*$px)")
  for t in $TOOLS; do
    p=$(param $kind $t); [ -z "$p" ] && continue
    run="$t:$p"; tn=${run//:/_p}; tn=${tn//,/_}
    D=$IOU_D O=$EO LIMIT=3600 bash "$B/run_one.sh" "$run" "$ds" | grep RESULT
    out="$EO/${ds}_${tn}.gpkg"; [ -f "$out" ] || out="${out%.gpkg}.shp"
    [ -f "$out" ] || continue
    "$QPY" "$B/iou.py" "$IOU_D/${ds}_truth.tif" "$out" $px "${ds}_$t" "$EO/${ds}_${t}.iou.json" 2>/dev/null | grep IOU
    "$QPY" "$B/metrics.py" "$IOU_D/$ds.gpkg" "$out" "${ds}_$t" "$EO/${ds}_${t}.met.json" $inset 2>&1 | grep -E "^METRICS|Error" | cut -c1-200
    "$QPY" "$B/boundary_metrics.py" "$IOU_D/${ds}_truth.tif" "$out" "$IOU_D/$ds.gpkg" $px "${ds}_$t" "$EO/${ds}_${t}.bnd.json" 2>&1 | grep -E "^BOUNDARY|Error" | cut -c1-200
  done
  "$QPY" "$B/iou.py" "$IOU_D/${ds}_truth.tif" "$IOU_D/$ds.gpkg" $px "${ds}_input" "$EO/${ds}_input.iou.json" 2>/dev/null | grep IOU
  "$QPY" "$B/boundary_metrics.py" "$IOU_D/${ds}_truth.tif" "$IOU_D/$ds.gpkg" "$IOU_D/$ds.gpkg" $px "${ds}_input" "$EO/${ds}_input.bnd.json" 2>&1 | grep -E "^BOUNDARY|Error" | cut -c1-200
done

echo "=== waiting for an idle machine: create $GV_BENCH/GO_TIMING to start the timed runs ($(date))"
until [ -f $GV_BENCH/GO_TIMING ]; do sleep 30; done
echo "=== scaling (real forest map, 10 m; syn_multi parameters) $(date)"
export D="$GV_BENCH/data" O="$GV_BENCH/scale"
for t in gv_gaussian gv_gaussian_1w $TOOLS; do
  [ $t = gv_gaussian ] && continue
  base=${t%_1w}; p=$(param syn_multi $base); [ -z "$p" ] && continue
  for ds in ft1000 ft2000 ft4000 ft8000 ft16000 ftfull; do
    r=$(MEM=1 LIMIT=3600 bash "$B/run_one.sh" "$t:$p" $ds $([ $ds = ft8000 ] && echo m))
    echo "$r" | cut -c1-200
    case "$r" in *FAILED*) echo "--- $t stops at $ds"; break ;; esac
  done
done
echo "=== evaluate done $(date)"
