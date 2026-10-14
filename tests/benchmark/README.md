# GeoVanguard benchmark (SoftwareX manuscript)

Scripts behind Sections 3.3–3.4, Tables 1–4 and Figs. 4–6 of `manuscript/GeoVanguard_SoftwareX.md`:
GeoVanguard 1.0.1 against ArcGIS Pro 3.7.1 (Smooth Shared Edges, Smooth Polygon), QGIS 4.2.2 (Smooth),
GRASS GIS 8.5.0 (v.generalize) and Mapshaper 0.7.83, on synthetic and real classified maps.
The input data and all results are archived on Zenodo: https://doi.org/10.5281/zenodo.23355882

## Set-up

All paths and programs are set in `config.sh`; override any of them in the environment.

| Variable | Meaning |
| --- | --- |
| `GV_BENCH` | work root: `data/` (nested forest-type windows), `iou/` (truth + inputs of sets A/B), `eval/`, logs |
| `QPY` | QGIS Python (`python-qgis.bat`) — runs GeoVanguard, QGIS Smooth and all metrics |
| `GRASS` | GRASS GIS launcher (`grass85.bat`) |
| `ARC` | ArcGIS Pro Python (`arcgispro-py3`); the tools need a Standard or Advanced licence |
| `PYW`, `PYW_PATH` | a Python with `psutil` for `memwrap.py` / `cpuwrap.py` |
| `FT_RASTER`, `GREEN_SHP` | source maps, only to rebuild `data/` and `iou/` |

Mapshaper runs through `npx.cmd -y mapshaper` (Node.js). The Python scripts read `GV_BENCH` too;
`make_figs.py` writes to `<repo>/manuscript/figures`. Run the shell scripts with Git Bash.

To start from the Zenodo archive, unpack `inputs.zip` into `$GV_BENCH` (it holds `data/` and `iou/`)
and begin at step 2; unpack `results.zip` as well to redraw the figures without rerunning.

## Workflow

| Step | Script | Output in `$GV_BENCH` |
| --- | --- | --- |
| 1. Data | `prep.py "$FT_RASTER" $GV_BENCH/data <repo>` (nested windows), `make_data.py $GV_BENCH/iou "$FT_RASTER" "$GREEN_SHP"` (sets A/B) | `data/`, `iou/` |
| 2. Tuning (sets A) | `tune.sh`, `tune_extra.sh`, then `pick_best.py` | `iou_out/tune.csv`, `eval/best.json` |
| 3. Test sets B | `evaluate.sh` (calls `run_one.sh`) | `evaluate.log`, `eval/*.iou.json`, `*.met.json`, `*.bnd.json` |
| 4. Large data | `large_data.sh` (scaling, tile seams, resume), `extra_run.sh`, `night_run.sh` | `large_data.log`, `extra.log`, `night.log` |
| 5. Shape and overall | `shape_metrics.py`, `overall.py` | `eval/shape.json` |
| 6. Figures | `make_figs.py fig4a fig4b fig4 fig5 fig6` | `manuscript/figures/` |

Timed runs (steps 3–4) wait for the file `$GV_BENCH/GO_TIMING`, so that they start on an idle machine.
One run by hand: `O=$GV_BENCH/test bash run_one.sh gv_gaussian:15 ft1000 m` (`m` adds the topology
metrics; `MEM=1` measures peak memory, `CPU=1` the cores used; `LIMIT` is the time limit in seconds).

Runners: `run_qgis_env.py` (GeoVanguard, QGIS Smooth; `GV_PROGRESS=<file>` logs GeoVanguard's own
progress and ETA), `run_grass.py`, `run_arcgis.py`, Mapshaper inside `run_one.sh`.
Software versions: `versions_q.py` (QGIS, GDAL, GEOS), `versions_a.py` (ArcGIS Pro).

## Metrics

| Script | Measures | Used in |
| --- | --- | --- |
| `iou.py` | mIoU against the finer truth raster | Table 2, Fig. 4 |
| `metrics.py` | gaps, overlaps, invalid polygons, area change; shared-edge matching above 100,000 polygons | Tables 2, 4 |
| `boundary_metrics.py` | distance to the true borders, Boundary F1, border-length ratio, adjacency | Section 3.3 |
| `shape_metrics.py` | axis-parallel border length, orientation entropy, sharp turns per km | Table 3 |
| `overall.py` | mean ranks, Pareto front, GeoVanguard vs ArcGIS Smooth Shared Edges | Section 3.3, "Overall" |
| `memwrap.py`, `cpuwrap.py` | peak memory and cores of the whole process tree | Fig. 6, Section 3.4 |
| `digest.py` | SHA-256 of all output geometries (resume, tile sizes) | Table 4 |
| `compare_tiles.py`, `ct_why.py` | automatic vs 8 km tiles: identical bytes, vertex sets, distance, symmetric difference | Section 3.4, Table 4 note ¹ |
| `unmatched_check.py`, `unmatched_vs_nodata.py`, `nodata_count.py` | unmatched edges lie next to nodata holes of the input | Section 3.4 |
| `resume_test.py` | kill a run at 50 %, resume it, compare with an uninterrupted run | Table 4 |

## Test machine

Two Intel Xeon Bronze 3204 (12 cores, 1.9 GHz), 64 GB RAM, HDD RAID 5, Windows 11 Pro for Workstations.
