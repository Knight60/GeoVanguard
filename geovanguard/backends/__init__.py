"""Geometry backends: the few GEOS operations the pipeline needs.

The pipeline itself is numpy + sqlite only; validity checks, line noding and
pairwise intersections go through a backend.  Standalone uses shapely 2; the
QGIS plugin passes its own backend on QgsGeometry (geovanguard_qgis).  Both
wrap GEOS, so the results are the same.
"""
from .base import GeometryBackend


def get_backend(backend='auto'):
    """A backend instance from a name ('auto' / 'shapely') or an instance."""
    if isinstance(backend, GeometryBackend):
        return backend
    if backend in (None, 'auto', 'shapely'):
        from .shapely_backend import ShapelyBackend
        return ShapelyBackend()
    raise ValueError('unknown geometry backend: {!r}'.format(backend))
