"""GeoVanguard and QGIS 'Smooth' on one input polygon layer (QGIS Python).

usage: python-qgis run_qgis_env.py <tool> <input.gpkg> <output.gpkg> <repo root>
tool: gv_gaussian | gv_chaikin | gv_gaussian_1w | qgis_smooth
Prints one line: RESULT <tool> <seconds>
"""
import os
import sys
import time

TOOL, INP, OUTP, REPO = sys.argv[1:5]
TOOL, _, PARAM = TOOL.partition(':')
sys.path.insert(0, REPO)
if os.path.exists(OUTP):
    os.remove(OUTP)

if TOOL.startswith('gv_'):
    import shutil
    import geovanguard as gv
    keep = os.environ.get('GV_WORK')          # resume test: fixed work folder, kept
    work = keep or OUTP + '.work'
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    kw = dict(algorithm='Chaikin' if 'chaikin' in TOOL else 'Gaussian', work_dir=work)
    if TOOL.endswith('_1w'):
        kw['workers'] = 1
    if PARAM:
        kw['iterations' if 'chaikin' in TOOL else 'sigma'] = float(PARAM)
    if os.environ.get('GV_TILE'):
        kw['tile_size'] = float(os.environ['GV_TILE'])
    if os.environ.get('GV_PROGRESS'):         # GeoVanguard's own step text and ETA, appended to a file
        from geovanguard.feedback import ConsoleFeedback
        kw['feedback'] = ConsoleFeedback(stream=open(os.environ['GV_PROGRESS'], 'a', encoding='utf8'))
    t0 = time.time()
    summary = gv.smooth_polygons(INP, OUTP, **kw)
    sec = time.time() - t0
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    print('SUMMARY', {k: v for k, v in summary.items() if not isinstance(v, (list, dict))})
else:
    from qgis.core import QgsApplication
    app = QgsApplication([], False)
    app.initQgis()
    sys.path.append(os.path.join(QgsApplication.prefixPath(), 'python', 'plugins'))
    import processing
    from processing.core.Processing import Processing
    Processing.initialize()
    t0 = time.time()
    processing.run('native:smoothgeometry', {'INPUT': INP, 'ITERATIONS': int((PARAM or '3').split(',')[0]),
                                             'OFFSET': float((PARAM + ',0.25').split(',')[1]) if PARAM else 0.25,
                                             'MAX_ANGLE': 180, 'OUTPUT': OUTP})
    sec = time.time() - t0
wk = summary.get('workers') if TOOL.startswith('gv_') else None
print('RESULT %s %.2f%s' % (sys.argv[1], sec, ' workers=%s' % wk if wk is not None else ''), flush=True)
