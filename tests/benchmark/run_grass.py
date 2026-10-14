"""GRASS GIS v.generalize on one input polygon layer (run inside `grass --exec`).

usage: grass --tmp-project EPSG:32647 --exec python run_grass.py <method> <input.gpkg> <output.gpkg>
method: chaiken | sliding_averaging | snakes
Prints: RESULT grass_<method> <total s> import=<s> generalize=<s> export=<s>
"""
import os
import sys
import time

import grass.script as gs

METHOD, INP, OUTP = sys.argv[1:4]
METHOD, _, PARAM = METHOD.partition(':')
if os.path.exists(OUTP):
    os.remove(OUTP)
t0 = time.time()
gs.run_command('v.in.ogr', input=INP, output='src', flags='o', overwrite=True, quiet=True)
t1 = time.time()
params = {'chaiken': dict(threshold=1.0),
          'sliding_averaging': dict(threshold=1.0, look_ahead=7, slide=0.5),
          'snakes': dict(threshold=1.0, alpha=1.0, beta=1.0)}[METHOD]
if PARAM:
    vals = PARAM.split(',')
    if METHOD == 'sliding_averaging':
        params['look_ahead'] = int(vals[0])
        if len(vals) > 1:
            params['slide'] = float(vals[1])
    else:
        params[{'chaiken': 'threshold', 'snakes': 'alpha'}[METHOD]] = float(vals[0])
gs.run_command('v.generalize', input='src', output='gen', method=METHOD, type='boundary',
               overwrite=True, quiet=True, **params)
t2 = time.time()
gs.run_command('v.out.ogr', input='gen', output=OUTP, format='GPKG', type='area', quiet=True)
t3 = time.time()
print('RESULT grass_%s%s %.2f import=%.2f generalize=%.2f export=%.2f' % (
    METHOD, ':' + PARAM if PARAM else '', t3 - t0, t1 - t0, t2 - t1, t3 - t2), flush=True)
