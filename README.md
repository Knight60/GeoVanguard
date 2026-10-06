<p align="center"><img src="geovanguard_qgis/help/logo@2x.png" alt="GeoVanguard" width="420"></p>

# GeoVanguard

GeoVanguard helps you process large, complex remote-sensing (RS) and GIS data.
It combines proven open-source components — QGIS, GDAL/OGR, GEOS, NumPy and
SQLite — into efficient workflows that scale to very large datasets: the work is
split into tiles and shared across your CPU cores, it can be paused and resumed,
and an interrupted run continues where it stopped.

GeoVanguard comes in two forms that share one engine and give bit-identical results:

| Form | For | Install | Needs |
| --- | --- | --- | --- |
| **QGIS plugin** `geovanguard_qgis` | QGIS users (Processing Toolbox, models, batch) | QGIS *Plugins › Manage and Install Plugins* › "GeoVanguard" | QGIS 3.40+ or 4.x only |
| **Python package** `geovanguard` | scripts, servers, pipelines | from this repository (`pip`/`conda` packages planned) | Python, numpy, GDAL, shapely ≥ 2 |

## Tools

### Smoothing Topology Preserver

Smooths the jagged outlines of polygon maps — for example land-cover or
forest-type maps made from Sentinel-2 classifications — while neighbouring
polygons stay perfectly joined: no gaps or overlaps appear, holes are smoothed
like outer edges and areas are preserved, also for maps with hundreds of
thousands of polygons.

| Tool | Input | What it does |
| --- | --- | --- |
| **Smooth Polygons** (`geovanguard:smooth_polygons`) | polygon map (vector, no overlaps) | smooths all outlines without gaps or overlaps; attributes are kept |
| **Smooth Polygonization** (`geovanguard:smooth_polygonization`) | classified (integer) raster | optional removal of small patches → pixels to polygons → the same smoothing |

Smoothing methods, best first: Gaussian (recommended), Chaikin, B-Spline, Bezier,
Douglas-Peucker. Defaults suit 10 m Sentinel-2 maps; distances are in meters.

Developed by **Pisut Nakmuenwai** · License: [GPL-2.0-or-later](LICENSE) ·
cite with [CITATION.cff](CITATION.cff).

## How it works (LPoly/RPoly)

Diagram: [WorkFlow.svg](WorkFlow.svg) (source: [WorkFlow.mmd](WorkFlow.mmd)).

1. Polygons are stored in an on-disk work database with a global `poly_id`.
2. Tile by tile, every boundary edge is matched with its reverse edge → each edge
   knows its left (LPoly) and right (RPoly) polygon. Missing junction vertices
   (GDAL does not put a vertex where three polygons meet on a straight edge) are
   inserted first.
3. Edges with the same LPoly/RPoly are joined into arcs; each shared arc is stored once.
4. Every arc is smoothed exactly once (Gaussian / Chaikin / B-Spline / Bezier / Douglas-Peucker).
5. Each polygon is rebuilt from its own arcs (forward if LPoly, reversed if RPoly),
   so neighbours always share identical boundaries.
6. Any polygon that would become invalid gets its arcs re-smoothed more gently
   around the conflicts, or reverted, and its neighbours are rebuilt.

Large data: memory is bounded by the tile size; every stage checkpoints to the
work folder, so an interrupted run resumes, and changing only the smoothing
settings re-uses the topology.

## Standalone use

Until the `pip`/`conda` packages are published, put the repository folder on
`PYTHONPATH` (any Python with numpy, GDAL and shapely ≥ 2, e.g. the OSGeo4W or
conda-forge Python).

```python
import geovanguard as gv
gv.smooth_polygons('coverage.gpkg', 'smoothed.gpkg')                     # Gaussian, sigma 12.5 m
gv.smooth_polygonization('classified.tif', 'smoothed.gpkg', sieve=4,
                         algorithm='Chaikin', work_dir='work/forest')    # resumable
```

All distances are in meters (converted to the layer CRS) unless `units='crs'`.
Progress on the console: pass `feedback=geovanguard.feedback.ConsoleFeedback()`.

## Requirements

- **Plugin:** QGIS 3.40+ or 4.x (tested on 3.40.7 and 4.2.2). Only what ships with
  QGIS is used: `qgis.core`, GDAL/OGR, numpy, sqlite3; the engine is bundled in
  the plugin ZIP.
- **Standalone:** numpy, GDAL Python bindings (`osgeo`), shapely ≥ 2.
- `scipy` is optional (B-Spline/Bezier fall back to Chaikin without it).

## Development

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_plugin.ps1   # junctions into QGIS 3 + 4 profiles
powershell -ExecutionPolicy Bypass -File tools\package_plugin.ps1   # dist\geovanguard_qgis-<version>.zip
```

The plugin imports the engine from its sub-folder `geovanguard_qgis/geovanguard`,
a junction to `geovanguard/` while developing (git-ignored) and a copy in the ZIP.
Then enable **GeoVanguard** in *Plugins › Manage and Install Plugins*.

## Tests

```powershell
$q = "C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat"
& $q tests\test_core.py                 # numpy only
& $q tests\run_qgis_tests.py            # plugin end-to-end
& $q tests\run_gui_test.py              # Processing dialog
cd tests; & $q result_hashes.py plugin.json                          # exact result fingerprints
& $q standalone_hashes.py sa.json --compare plugin.json              # standalone == plugin, no qgis import
```

## Layout

```text
geovanguard/          standalone engine (numpy + sqlite; GEOS via backends/) — never imports qgis
  core/               geometry encoding, topology (LPoly/RPoly), smoothing, work store
  backends/           GeometryBackend interface + shapely backend
  pipeline.py api.py params.py progress.py feedback.py raster.py
geovanguard_qgis/     QGIS plugin: Processing provider, algorithms, GUI wrappers, QGIS backend
tests/                core, end-to-end, GUI and parity tests
tools/                install / package / ZIP check helpers
```
