"""Entry points of the worker processes (module-level → picklable on spawn).

Each worker builds the same geometry backend as the main process once
(``init``), then runs pure stage functions from geovanguard.stages.  The main
process can call the same functions directly (``init_local``), which is how a
1-worker run executes — one code path, identical results.
"""
import importlib

from . import stages

_BACKEND = None
_SMOOTHERS = {}


def backend_spec(backend):
    """``(module, class name)`` to rebuild ``backend`` in a worker."""
    cls = type(backend)
    return cls.__module__, cls.__name__


def init(spec):
    """ProcessPoolExecutor initializer: build the backend from its spec."""
    global _BACKEND
    module, name = spec
    _BACKEND = getattr(importlib.import_module(module), name)()


def init_local(backend):
    """Use ``backend`` directly (serial run in the main process)."""
    global _BACKEND
    _BACKEND = backend
    _SMOOTHERS.clear()


def ping():
    return _BACKEND.name


def load(records, origin, g):
    return stages.load_batch(records, origin, g)


def insert(ctx, tile, grid, tol_q):
    return stages.insert_tile(ctx, tile, grid, tol_q)


def densify(items):
    return stages.densify_batch(items)


def label(ctx, tile, grid, fixed_mode, extent_q):
    return stages.label_tile(ctx, tile, grid, fixed_mode, extent_q)


def smooth(rows, g, algorithm, alg_params_items):
    key = (algorithm, alg_params_items)
    sm = _SMOOTHERS.get(key)
    if sm is None:
        sm = stages.ArcSmoother(algorithm, dict(alg_params_items), _BACKEND)
        _SMOOTHERS.clear()
        _SMOOTHERS[key] = sm
    return sm.smooth_rows(rows, g)


def rebuild(arc_rows, pids, g, lo, hi, origin):
    return stages.rebuild_range(arc_rows, pids, g, lo, hi, origin, _BACKEND)
