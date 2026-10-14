#!/usr/bin/env bash
# Extra timed run: GeoVanguard on the full map with peak memory (no time limit), then the
# shared-edge check of its output. Starts when $GV_BENCH/GO_TIMING exists.
. "$(dirname "${BASH_SOURCE[0]}")/config.sh"
LOG=$GV_BENCH/extra.log
echo "=== waiting for $GV_BENCH/GO_TIMING ($(date))" > $LOG
until [ -f $GV_BENCH/GO_TIMING ]; do sleep 20; done
echo "=== start $(date)" >> $LOG
r=$(D=$GV_BENCH/data O=$GV_BENCH/extra MEM=1 LIMIT=10800 bash "$B/run_one.sh" gv_gaussian:15 ftfull)
echo "$r" | grep RESULT >> $LOG
"$QPY" "$B/metrics.py" $GV_BENCH/data/ftfull.gpkg $GV_BENCH/extra/ftfull_gv_gaussian_p15.gpkg ftfull_gv $GV_BENCH/extra/ftfull_gv.json 2>&1 | grep -E "^METRICS|Error" >> $LOG
"$QPY" "$B/unmatched_check.py" $GV_BENCH/extra/ftfull_gv_gaussian_p15.gpkg 100 $GV_BENCH/extra/ftfull_unmatched.gpkg 2>&1 | grep -E "unmatched|Error" >> $LOG
"$QPY" "$B/unmatched_vs_nodata.py" "$FT_RASTER" $GV_BENCH/extra/ftfull_unmatched.gpkg 2>&1 | grep -E "sample|share|Error" >> $LOG
echo "=== extra done $(date)" >> $LOG
