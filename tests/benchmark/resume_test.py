"""Interrupt a GeoVanguard run part-way, resume it, compare with an uninterrupted run.

usage: python resume_test.py <sigma> <input.gpkg> <out dir> <kill fraction>
(Python with psutil). Prints RESUME lines with the times.
"""
import os
import shutil
import subprocess
import sys
import time

import psutil

B = os.path.dirname(os.path.abspath(__file__))
QPY = r'C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat'
REPO = r'D:\Developing\PisutTools'
SIGMA, INP, OUT, FRAC = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
os.makedirs(OUT, exist_ok=True)


def run(work, out, kill_after=None):
    env = dict(os.environ, GV_WORK=work)
    t0 = time.time()
    p = subprocess.Popen([QPY, os.path.join(B, 'run_qgis_env.py'), 'gv_gaussian:' + SIGMA, INP, out, REPO],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if kill_after:
        try:
            p.wait(timeout=kill_after)
        except subprocess.TimeoutExpired:
            parent = psutil.Process(p.pid)
            for c in parent.children(recursive=True):
                c.kill()
            parent.kill()
            p.wait()
            return time.time() - t0, 'killed'
    p.communicate()
    return time.time() - t0, 'finished'


ref_work, res_work = os.path.join(OUT, 'work_ref'), os.path.join(OUT, 'work_resume')
for w in (ref_work, res_work):
    shutil.rmtree(w, ignore_errors=True)
ref_out, res_out = os.path.join(OUT, 'ref.gpkg'), os.path.join(OUT, 'resumed.gpkg')
t_ref, _ = run(ref_work, ref_out)
print('RESUME uninterrupted %.1f s' % t_ref, flush=True)
t_a, state = run(res_work, res_out, kill_after=FRAC * t_ref)
print('RESUME first part %.1f s (%s at %.0f %%)' % (t_a, state, 100 * FRAC), flush=True)
t_b, _ = run(res_work, res_out)
print('RESUME after restart %.1f s · total %.1f s · overhead %.1f s' % (t_b, t_a + t_b, t_a + t_b - t_ref),
      flush=True)
d = subprocess.run([QPY, os.path.join(B, 'digest.py'), ref_out, res_out], capture_output=True, text=True)
print('\n'.join(l for l in d.stdout.splitlines() if l.startswith(('DIGEST', 'SAME'))), flush=True)
