"""Smoothing parameters and defaults shared by the QGIS tools and standalone API.

Defaults are tuned on a polygonized Sentinel-2 classification (10 m pixels),
distances in meters (parameter comparisons on real Sentinel-2 maps).  A pre-simplify of
about one pixel removes the pixel staircase (a diagonal step deviates
10/sqrt(2) = 7.07 m, so anything below that keeps every step).  Gaussian
sliding average treats every arc the same way (Chaikin's result depends on
which staircase corners Douglas-Peucker keeps, so irregular staircases stayed
wavy).
"""
from .core import smoothing

DEFAULT_ALGORITHM = 'Gaussian'

# user-facing values; distances in the chosen distance unit (meters by default)
DEFAULTS = {
    'iterations': 4,
    'sigma': 12.5,
    'pre_simplify': 10.0,
    'post_simplify': 1.0,
    'dp_tolerance': 5.0,
    'points_per_100': 5.0,
    'degree': 3,
    'spline_smoothing': 0.0,
}

# parameters each method actually uses (the GUI shows only these)
RELEVANT = {
    'Gaussian': ('sigma', 'post_simplify'),
    'Chaikin': ('iterations', 'pre_simplify', 'post_simplify'),
    'B-Spline': ('pre_simplify', 'points_per_100', 'degree', 'spline_smoothing'),
    'Bezier': ('pre_simplify', 'points_per_100'),
    'Douglas-Peucker': ('dp_tolerance',),
}


def algorithm_name(value):
    """Canonical algorithm name from a name (any case, '-'/'_' alike) or an index."""
    if isinstance(value, int):
        return smoothing.ALGORITHMS[value]
    key = str(value).lower().replace('_', '-').replace(' ', '-')
    for name in smoothing.ALGORITHMS:
        if name.lower() == key or name.lower().replace('-', '') == key.replace('-', ''):
            return name
    raise ValueError('unknown smoothing algorithm {!r}; choose one of: {}'.format(
        value, ', '.join(smoothing.ALGORITHMS)))


def smooth_signature(algorithm, values, factor):
    """``{'algorithm', 'params'}`` for the pipeline.

    ``values`` holds the user-facing parameters (missing keys → DEFAULTS);
    ``factor`` converts the user's distance unit to layer CRS units.
    """
    v = dict(DEFAULTS)
    v.update(dict((k, x) for k, x in values.items() if x is not None))
    params = {
        'iterations': int(v['iterations']),
        'pre_simplify_m': float(v['pre_simplify']) * factor,
        'post_simplify_m': float(v['post_simplify']) * factor,
        'tolerance_m': float(v['dp_tolerance']) * factor,
        'sigma_m': float(v['sigma']) * factor,
        # points per 100 user units → points per 100 layer units
        'n_points_per_100m': float(v['points_per_100']) / factor,
        'degree': int(v['degree']),
        'smoothing': float(v['spline_smoothing']),
    }
    return {'algorithm': algorithm_name(algorithm), 'params': params}
