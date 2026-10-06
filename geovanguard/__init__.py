"""GeoVanguard — topology-preserving smoothing of polygon coverages.

LPoly/RPoly arc topology: every shared boundary is smoothed exactly once, so
neighbouring polygons never get gaps or overlaps.  Works on large data (tiles
+ sqlite checkpoints, resumable).

    import geovanguard as gv
    gv.smooth_polygons('coverage.gpkg', 'smoothed.gpkg')
    gv.smooth_polygonization('classified.tif', 'smoothed.gpkg', sieve=4)

The same engine runs inside the GeoVanguard QGIS plugin.
Developed by Pisut Nakmuenwai.
"""
__version__ = '1.0.0'
__all__ = ['smooth_polygons', 'smooth_polygonization', '__version__']


def smooth_polygons(*args, **kwargs):
    """See :func:`geovanguard.api.smooth_polygons`."""
    from .api import smooth_polygons as fn
    return fn(*args, **kwargs)


def smooth_polygonization(*args, **kwargs):
    """See :func:`geovanguard.api.smooth_polygonization`."""
    from .api import smooth_polygonization as fn
    return fn(*args, **kwargs)
