#!/usr/bin/env bash
# Parameter grid per tool on the tuning sets (A); IoU against the truth -> tune.csv
# usage: tune.sh [dataset ...]   (default: the four A sets)
. "$(dirname "${BASH_SOURCE[0]}")/config.sh"
export D="$GV_BENCH/iou"
export O="$GV_BENCH/iou_out"
mkdir -p "$O"
CSV="$O/tune.csv"
[ -f "$CSV" ] || echo "dataset,tool,param,seconds,mIoU,OA" > "$CSV"
SETS=${*:-"syn_multi_A syn_single_A ft_multi_A ga_single_A"}

mul() { python -c "import sys; v=float(sys.argv[1])*float(sys.argv[2]); print(('%g' % v))" "$1" "$2"; }

for ds in $SETS; do
  px=$(python -c "import json;print(json.load(open('$D/datasets.json'))['$ds']['input_px'])")
  "$QPY" "$B/iou.py" "$D/${ds}_truth.tif" "$D/$ds.gpkg" $px input 2>/dev/null | \
    sed -n 's/^IOU input mIoU=\([0-9.]*\) OA=\([0-9.]*\)/'"$ds"',input,-,0,\1,\2/p' >> "$CSV"
  RUNS=""
  for f in 0.5 0.75 1 1.25 1.5 2 2.5 3; do RUNS="$RUNS gv_gaussian:$(mul $f $px)"; done
  for p in 1,0.25 2,0.25 3,0.25 5,0.25 8,0.25 3,0.1 3,0.4; do RUNS="$RUNS qgis_smooth:$p"; done
  for la in 3 5 7 9 13; do for s in 0.5 1.0; do RUNS="$RUNS grass_sliding_averaging:$la,$s"; done; done
  for f in 0.1 0.2 0.5 1; do RUNS="$RUNS grass_chaiken:$(mul $f $px)"; done
  for f in 1 2 3 5 7.5 10 15; do RUNS="$RUNS arc_sharededges:$(mul $f $px) arc_smoothpolygon:$(mul $f $px)"; done
  for f in 0.2 0.5 1 1.5 2 3; do RUNS="$RUNS mapshaper:$(mul $f $px)"; done
  for run in $RUNS; do
    line=$(LIMIT=900 bash "$B/run_one.sh" "$run" "$ds" | grep RESULT | tail -1)
    sec=$(echo "$line" | awk '{print $4}')
    tn=${run//:/_p}; tn=${tn//,/_}
    out="$O/${ds}_${tn}.gpkg"; [ -f "$out" ] || out="${out%.gpkg}.shp"
    if [ "$sec" = FAILED ] || [ ! -f "$out" ]; then
      echo "$ds,${run%%:*},\"${run#*:}\",FAILED,," >> "$CSV"; continue
    fi
    r=$("$QPY" "$B/iou.py" "$D/${ds}_truth.tif" "$out" $px x 2>/dev/null | sed -n 's/^IOU x mIoU=\([0-9.]*\) OA=\([0-9.]*\)/\1,\2/p')
    echo "$ds,${run%%:*},\"${run#*:}\",$sec,$r" | tee -a "$CSV"
  done
done
echo "=== tune done $(date)"
