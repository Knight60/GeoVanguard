#!/usr/bin/env bash
# run_one.sh <tool> <dataset> [metrics]  — run one tool on one dataset, append to results.log
set -u
. "$(dirname "${BASH_SOURCE[0]}")/config.sh"
D="${D:-$GV_BENCH/data}"
O="${O:-$GV_BENCH/out}"
TOOL=$1; DS=$2; MET=${3:-}
mkdir -p "$O"
# snakes ran > 1 h on the 1,029-polygon synthetic set: run it there only
if [ "$TOOL" = grass_snakes ] && [ "$DS" != syn1000 ]; then
  echo "$DS RESULT $TOOL FAILED skipped (> 3600 s on syn1000)" | tee -a "$O/results.log"; exit 1
fi
IN="$D/$DS.gpkg"; TN=${TOOL//:/_p}; TN=${TN//,/_}; OUT="$O/${DS}_${TN}.gpkg"
LIMIT=${LIMIT:-3600}
# MEM=1: wrap the tool in memwrap.py (peak memory of the whole process tree)
W=()
if [ "${CPU:-}" = 1 ]; then
  W=(env PYTHONPATH="$PYW_PATH" "$PYW" "$B/cpuwrap.py")
elif [ "${MEM:-}" = 1 ]; then
  W=(env PYTHONPATH="$PYW_PATH" "$PYW" "$B/memwrap.py")
fi
case $TOOL in
  gv_*|qgis_smooth*) R=$(timeout $LIMIT "${W[@]}" "$QPY" "$B/run_qgis_env.py" "$TOOL" "$IN" "$OUT" "$REPO" 2>&1) ;;
  grass_*) R=$(timeout $LIMIT "${W[@]}" "$GRASS" --tmp-project EPSG:32647 --exec python "$B/run_grass.py" "${TOOL#grass_}" "$IN" "$OUT" 2>&1) ;;
  mapshaper*)
    P=${TOOL#mapshaper}; P=${P#:}; P=${P:-10}
    rm -f "$O/${DS}_${TN}.shp"
    S0=$(date +%s.%N)
    R=$(timeout $LIMIT "${W[@]}" npx.cmd -y mapshaper@0.7.83 "$D/$DS.shp" -simplify interval=$P keep-shapes -o "$O/${DS}_${TN}.shp" force 2>&1)
    S1=$(date +%s.%N)
    R="$R
RESULT $TOOL $(python -c "print('%.2f' % ($S1 - $S0))")"
    OUT="$O/${DS}_${TN}.shp" ;;
  arc_*) R=$(timeout $LIMIT "${W[@]}" "$ARC" "$B/run_arcgis.py" "$TOOL" "$D/$DS.shp" "$OUT" "$O/arcscratch" 2>&1); OUT="${OUT%.gpkg}.shp" ;;
esac
RC=$?
LINE=$(echo "$R" | grep '^RESULT' | tail -1)
PK=$(echo "$R" | grep '^PEAK_MB' | tail -1 | awk '{print $2}')
[ -n "$LINE" ] && [ -n "$PK" ] && LINE="$LINE peak_mb=$PK"
CC=$(echo "$R" | grep '^CPU_CORES' | tail -1 | cut -d' ' -f2-)
[ -n "$LINE" ] && [ -n "$CC" ] && LINE="$LINE cores: $CC"
[ -z "$LINE" ] && LINE="RESULT $TOOL FAILED rc=$RC"
echo "$DS $LINE" | tee -a "$O/results.log"
[ -z "$LINE" -o "${LINE#*FAILED}" != "$LINE" ] && { echo "$R" | tail -15; exit 1; }
if [ -n "$MET" ]; then
  "$QPY" "$B/metrics.py" "$IN" "$OUT" "${DS}_${TOOL}" "$O/${DS}_${TN}.json" 2>&1 | grep -E '^METRICS|Error|Traceback' | tee -a "$O/metrics.log"
fi
