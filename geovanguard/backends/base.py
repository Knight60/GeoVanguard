"""Interface every geometry backend implements.

Coordinates are (n, 2) float64 numpy arrays in map units; polygons are WKB.
No backend object (QgsGeometry, shapely geometry) ever leaves the backend.
"""


class GeometryBackend(object):
    name = 'base'

    def geos_version(self):
        """GEOS version string (recorded with the results)."""
        raise NotImplementedError

    def is_simple(self, xy):
        """True if the line through ``xy`` does not cross or touch itself."""
        raise NotImplementedError

    def is_valid(self, wkb):
        """True if the (multi)polygon WKB is valid (GEOS rules)."""
        raise NotImplementedError

    def noded_ends(self, xy):
        """End points [(x, y), ...] of every part of the line ``xy`` after
        noding it with itself (unary union).  A self-crossing becomes a node
        where >= 3 part ends meet."""
        raise NotImplementedError

    def intersections(self, lines):
        """Yield ``(k, j, points)`` for every pair of lines that intersect.

        ``lines`` is a list of coordinate arrays; ``points`` are the vertices
        of their intersection.  Each pair is reported once.  Implementations
        must prepare the LARGER line of a pair and query the smaller ones
        against it (a polygon may have one 30 000-vertex outer ring and 25 000
        holes — converting the big ring for every hole exhausts memory).
        """
        raise NotImplementedError
