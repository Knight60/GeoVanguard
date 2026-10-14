"""Run a command and report the peak memory of its whole process tree.

usage: python memwrap.py <command> [args...]
Passes the command's output through and prints a final line: PEAK_MB <MB>
(sum of the working sets of the command and all its child processes,
sampled every 0.25 s).
"""
import subprocess
import sys
import threading
import time

import psutil

proc = subprocess.Popen(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
peak = [0]


def sample():
    root = psutil.Process(proc.pid)
    while proc.poll() is None:
        try:
            procs = [root] + root.children(recursive=True)
            total = 0
            for p in procs:
                try:
                    total += p.memory_info().rss
                except psutil.Error:
                    pass
            peak[0] = max(peak[0], total)
        except psutil.Error:
            pass
        time.sleep(0.25)


th = threading.Thread(target=sample, daemon=True)
th.start()
for line in proc.stdout:
    sys.stdout.write(line.decode('utf-8', 'replace'))
proc.wait()
th.join(timeout=1)
print('PEAK_MB %.0f' % (peak[0] / 1048576.0), flush=True)
sys.exit(proc.returncode)
