#!/usr/bin/env bash
# Night run: GeoVanguard with ONE worker on 224,021 and 385,679 polygons, peak memory, no 1 h limit.
# Starts when $GV_BENCH/GO_TIMING exists.
. "$(dirname "${BASH_SOURCE[0]}")/config.sh"
LOG=$GV_BENCH/night.log
echo "=== waiting for $GV_BENCH/GO_TIMING ($(date))" > $LOG
until [ -f $GV_BENCH/GO_TIMING ]; do sleep 20; done
echo "=== start $(date)" >> $LOG
for ds in ft16000 ftfull; do
  echo "--- $ds start $(date +%H:%M:%S)" >> $LOG
  r=$(D=$GV_BENCH/data O=$GV_BENCH/night MEM=1 LIMIT=21600 bash "$B/run_one.sh" gv_gaussian_1w:15 $ds)
  echo "$r" | grep RESULT >> $LOG
done
echo "=== night done $(date)" >> $LOG
