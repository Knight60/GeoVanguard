"""Worker processes for the tile / batch stages.

Worker count (same intent as the original ForestType scripts, Utils.resolve_workers):
sample the current CPU usage, then use 50 % of the IDLE cores on machines
with <= 8 cores and 75 % on larger ones — a machine already busy with other
jobs gets proportionally fewer workers.  Each worker also needs memory for
one tile, so the count is capped by the available RAM.

Workers compute only; the main process keeps reading and writing the work
database, in the same order as a 1-worker run, so results are bit-identical
and checkpoints / resume work the same way.

Spawned processes: inside QGIS ``sys.executable`` is the QGIS program, so the
Python interpreter shipped with QGIS is used instead (``pythonw`` on Windows,
no console windows).  If workers cannot be started the run continues in the
main process.
"""
import concurrent.futures as cf
import ctypes
import multiprocessing
import os
import sys
import time
from collections import deque

from . import workers

RAM_PER_WORKER = 1.0e9        # bytes per worker (one tile ≈ 1 M vertices + Python)
MAX_WORKERS = 61              # Windows WaitForMultipleObjects limit
PARALLEL_MIN_POLYGONS = 20000  # automatic mode: smaller jobs run in one process


# ── machine state (psutil if present, else OS calls; no extra packages needed) ─

def _cpu_times():
    """(idle, total) CPU time counters, or None."""
    if sys.platform == 'win32':
        class FT(ctypes.Structure):
            _fields_ = [('lo', ctypes.c_uint32), ('hi', ctypes.c_uint32)]
        idle, kern, user = FT(), FT(), FT()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern),
                                                     ctypes.byref(user)):
            return None
        v = [(t.hi << 32) | t.lo for t in (idle, kern, user)]
        return v[0], v[1] + v[2]          # kernel time includes idle time
    try:
        with open('/proc/stat') as f:
            vals = [int(x) for x in f.readline().split()[1:]]
        return vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)
    except (OSError, ValueError, IndexError):
        return None


def cpu_busy_percent(interval=0.5):
    """System-wide CPU usage (%) over ``interval`` seconds; 0 if unknown (idle)."""
    try:
        import psutil
        return float(psutil.cpu_percent(interval=interval))
    except ImportError:
        pass
    a = _cpu_times()
    if a is None:
        try:                                    # macOS / BSD: 1-minute load average
            return min(100.0, 100.0 * os.getloadavg()[0] / (os.cpu_count() or 1))
        except (AttributeError, OSError):
            return 0.0
    time.sleep(interval)
    b = _cpu_times()
    if b is None or b[1] <= a[1]:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - (b[0] - a[0]) / float(b[1] - a[1]))))


def available_ram():
    """Available physical memory in bytes, or None if unknown."""
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except ImportError:
        pass
    if sys.platform == 'win32':
        class MS(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong),
                        ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong),
                        ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
        ms = MS()
        ms.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return int(ms.ullAvailPhys)
        return None
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def auto_workers(cpu_count, busy_pct):
    """50 % of the idle cores on <= 8-core machines, 75 % on larger ones (>= 1)."""
    idle_cores = cpu_count * max(0.0, 100.0 - busy_pct) / 100.0
    frac = 0.5 if cpu_count <= 8 else 0.75
    return max(1, int(idle_cores * frac))


def resolve_workers(requested=0):
    """``(workers, note)``.  ``requested`` <= 0 → automatic; else capped at the core count."""
    cpu = os.cpu_count() or 1
    if requested > 0:
        n = min(requested, cpu, MAX_WORKERS)
        return n, '{} worker process(es) (set by user; {} cores)'.format(n, cpu)
    busy = cpu_busy_percent()
    n = auto_workers(cpu, busy)
    note = '{} worker process(es) (automatic: {} cores, CPU {:.0f}% busy → {:.0%} of idle cores'.format(
        n, cpu, busy, 0.5 if cpu <= 8 else 0.75)
    ram = available_ram()
    if ram is not None:
        cap = max(1, int(ram * 0.75 / RAM_PER_WORKER))
        if cap < n:
            n = cap
            note = '{} worker process(es) (automatic: {} cores, CPU {:.0f}% busy; limited by {:.1f} GB free RAM'.format(
                n, cpu, busy, ram / 1e9)
    n = min(n, MAX_WORKERS)
    return n, note + ')'


# ── pool ──────────────────────────────────────────────────────────────────────

def _python_executable():
    """A Python interpreter for spawned workers (QGIS: its bundled Python)."""
    exe = sys.executable or ''
    if os.path.basename(exe).lower().startswith('python'):
        if sys.platform == 'win32':
            w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
            if os.path.exists(w) and not _has_console():
                return w
        return exe
    names = (['pythonw.exe', 'python.exe'] if sys.platform == 'win32'
             else ['bin/python3', 'bin/python', 'python3'])
    for base in (sys.exec_prefix, sys.base_exec_prefix, os.path.dirname(exe)):
        for name in names:
            path = os.path.join(base, name)
            if os.path.isfile(path):
                return path
    return None


def _has_console():
    if sys.platform != 'win32':
        return True
    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


class Pool(object):
    """Ordered task runner: worker processes, or the main process when n == 1."""

    def __init__(self, n_workers, backend, log=None):
        self.n = max(1, int(n_workers))
        self.backend = backend
        self.log = log or (lambda msg: None)
        self.executor = None
        self._main_file = None
        workers.init_local(backend)
        if self.n > 1:
            try:
                self._start()
            except Exception as err:            # noqa: BLE001 — fall back, never fail the run
                self.log('Worker processes could not be started ({}); continuing in one '
                         'process.'.format(err))
                self._shutdown()
                self.n = 1

    def _start(self):
        exe = _python_executable()
        if exe is None:
            raise RuntimeError('no Python interpreter found for worker processes')
        ctx = multiprocessing.get_context('spawn')
        ctx.set_executable(exe)
        # spawned workers would re-run the main script (e.g. a test or a user
        # script without a __main__ guard) — they only need this package
        main = sys.modules.get('__main__')
        if main is not None and getattr(main, '__file__', None):
            self._main_file = main.__file__
            del main.__file__
        self.executor = cf.ProcessPoolExecutor(
            max_workers=self.n, mp_context=ctx, initializer=workers.init,
            initargs=(workers.backend_spec(self.backend),))
        # start every worker now and check it can build the backend
        list(self.executor.map(_ping_after, [0.05] * self.n))

    def imap(self, fn, tasks, max_pending=None, poll=None):
        """Yield ``fn(*task)`` for each task, in task order.

        ``poll`` is called about every 0.5 s while waiting for a result (it may
        raise to stop, e.g. on cancel, or block, e.g. while paused).
        """
        if self.executor is None:
            for task in tasks:
                yield fn(*task)
            return
        max_pending = max_pending or 2 * self.n
        pending = deque()
        it = iter(tasks)

        def result(f):
            while True:
                try:
                    return f.result(timeout=0.5)
                except cf.TimeoutError:
                    if poll is not None:
                        poll()

        try:
            for task in it:
                pending.append(self.executor.submit(fn, *task))
                if len(pending) >= max_pending:
                    yield result(pending.popleft())
            while pending:
                yield result(pending.popleft())
        finally:
            for f in pending:
                f.cancel()

    def _worker_pids(self):
        return [p.pid for p in list(getattr(self.executor, '_processes', {}).values())
                if p.pid] if self.executor is not None else []

    def suspend(self):
        """Freeze the worker processes (CPU use drops to zero; nothing is lost)."""
        for pid in self._worker_pids():
            _suspend_process(pid, True)

    def resume(self):
        for pid in self._worker_pids():
            _suspend_process(pid, False)

    def _shutdown(self, abort=False):
        if self.executor is not None:
            if abort:
                # stop now: kill the workers (also suspended ones) instead of
                # letting running tasks finish
                for proc in list(getattr(self.executor, '_processes', {}).values()):
                    try:
                        proc.kill()
                    except (OSError, ValueError):  # the process has already ended
                        continue
            try:
                self.executor.shutdown(wait=not abort, cancel_futures=True)
            except TypeError:                    # Python < 3.9
                self.executor.shutdown(wait=not abort)
            self.executor = None
        if self._main_file is not None:
            main = sys.modules.get('__main__')
            if main is not None:
                main.__file__ = self._main_file
            self._main_file = None

    def close(self, abort=False):
        """Stop the workers; ``abort`` kills running tasks instead of waiting."""
        self._shutdown(abort)


def _suspend_process(pid, suspend):
    """Suspend / resume a whole process (Windows: NtSuspendProcess; else SIGSTOP/SIGCONT)."""
    try:
        if sys.platform == 'win32':
            k32 = ctypes.windll.kernel32
            ntdll = ctypes.windll.ntdll
            k32.OpenProcess.restype = ctypes.c_void_p
            handle = k32.OpenProcess(0x0800, False, pid)      # PROCESS_SUSPEND_RESUME
            if not handle:
                return False
            try:
                fn = ntdll.NtSuspendProcess if suspend else ntdll.NtResumeProcess
                return fn(ctypes.c_void_p(handle)) == 0
            finally:
                k32.CloseHandle(ctypes.c_void_p(handle))
        import signal
        os.kill(pid, signal.SIGSTOP if suspend else signal.SIGCONT)
        return True
    except Exception:                                           # noqa: BLE001
        return False


def _ping_after(delay):
    time.sleep(delay)          # keeps tasks from all landing on the first worker
    return workers.ping()
