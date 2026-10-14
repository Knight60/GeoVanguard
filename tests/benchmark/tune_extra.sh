#!/usr/bin/env bash
# Extend the grid where the best value sat on its edge (first tuning pass).
. "$(dirname "${BASH_SOURCE[0]}")/config.sh"
export D="$GV_BENCH/iou"
export O="$GV_BENCH/iou_out"
CSV="$O/tune.csv"
mul() { python -c "import sys; v=float(sys.argv[1])*float(sys.argv[2]); print(('%g' % v))" "$1" "$2"; }
for ds in syn_multi_A syn_single_A ft_multi_A ga_single_A; do
  px=$(python -c "import json;print(json.load(open('$D/datasets.json'))['$ds']['input_px'])")
  RUNS=""
  for p in 3,0.5 5,0.4 5,0.5 8,0.4 2,0.4; do RUNS="$RUNS qgis_smooth:$p"; done
  for p in 3,0.25 3,0.35 5,0.25 5,0.35; do RUNS="$RUNS grass_sliding_averaging:$p"; done
  for f in 1.5 2 3; do RUNS="$RUNS grass_chaiken:$(mul $f $px)"; done
  for f in 0.05 0.1; do RUNS="$RUNS mapshaper:$(mul $f $px)"; done
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
echo "=== extra done $(date)"
