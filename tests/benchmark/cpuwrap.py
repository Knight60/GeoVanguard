"""Run a command and report how many CPU cores its whole process tree used.

usage: python cpuwrap.py <command> [args...]
Prints CPU_CORES avg=<CPU time / wall time> peak=<max over 2 s windows> max_procs=<largest tree>.
CPU time of processes that exit is kept from their last sample.
"""
import subprocess
import sys
import threading
import time

import psutil

proc = subprocess.Popen(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
seen = {}                         # pid -> last cpu seconds (user + system)
state = {'peak': 0.0, 'procs': 0}


def total():
    try:
        root = psutil.Process(proc.pid)
        tree = [root] + root.children(recursive=True)
    except psutil.Error:
        tree = []
    state['procs'] = max(state['procs'], len(tree))
    for p in tree:
        try:
            c = p.cpu_times()
            seen[p.pid] = c.user + c.system
        except psutil.Error:
            pass
    return sum(seen.values())


def sample():
    t_prev, c_prev = time.time(), total()
    while proc.poll() is None:
        time.sleep(2.0)
        t, c = time.time(), total()
        state['peak'] = max(state['peak'], (c - c_prev) / max(t - t_prev, 1e-6))
        t_prev, c_prev = t, c


t0 = time.time()
th = threading.Thread(target=sample, daemon=True)
th.start()
for line in proc.stdout:
    sys.stdout.write(line.decode('utf-8', 'replace'))
proc.wait()
th.join(timeout=3)
wall = time.time() - t0
print('CPU_CORES avg=%.2f peak=%.2f max_procs=%d wall=%.1f' % (total() / wall, state['peak'], state['procs'], wall),
      flush=True)
sys.exit(proc.returncode)
