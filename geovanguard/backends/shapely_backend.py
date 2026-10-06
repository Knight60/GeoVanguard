"""Geometry backend on shapely >= 2 (standalone, no QGIS)."""
import numpy as np
import shapely
from shapely import STRtree

from .base import GeometryBackend


class ShapelyBackend(GeometryBackend):
    name = 'shapely'

    def __init__(self):
        if int(shapely.__version__.split('.')[0]) < 2:
            raise ImportError('shapely >= 2 is required (found {})'.format(shapely.__version__))

    def geos_version(self):
        return shapely.geos_version_string

    def is_simple(self, xy):
        return bool(shapely.is_simple(shapely.linestrings(xy)))

    def is_valid(self, wkb):
        return bool(shapely.is_valid(shapely.from_wkb(wkb)))

    def noded_ends(self, xy):
        noded = shapely.unary_union(shapely.linestrings(xy))
        if noded is None or noded.is_empty:
            return []
        out = []
        for part in shapely.get_parts(noded):
            if shapely.get_type_id(part) != 1:     # LineString only
                continue
            c = shapely.get_coordinates(part)
            if len(c) < 2:
                continue
            out.append((float(c[0, 0]), float(c[0, 1])))
            out.append((float(c[-1, 0]), float(c[-1, 1])))
        return out

    def intersections(self, lines):
        geoms = [shapely.linestrings(xy) for xy in lines]
        tree = STRtree(geoms)
        order = sorted(range(len(lines)), key=lambda i: -len(lines[i]))
        rank = dict((k, r) for r, k in enumerate(order))
        for k in order:
            line = geoms[k]
            cands = [int(j) for j in tree.query(line) if rank[int(j)] > rank[k]]
            if not cands:
                continue
            shapely.prepare(line)
            for j in cands:
                other = geoms[j]
                if not shapely.intersects(line, other):
                    continue
                inter = shapely.intersection(line, other)
                if inter is None or inter.is_empty:
                    continue
                c = shapely.get_coordinates(inter)
                yield k, j, [(float(x), float(y)) for x, y in np.asarray(c)]
            shapely.destroy_prepared(line)
