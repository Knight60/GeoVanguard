"""Geometry backend on QGIS' GEOS wrappers (QgsGeometry, QgsSpatialIndex)."""
from qgis.core import QgsGeometry, QgsSpatialIndex

from .geovanguard.backends.base import GeometryBackend
from .geovanguard.core import geom


def _from_wkb(wkb):
    g = QgsGeometry()
    g.fromWkb(wkb)
    return g


def _line(xy):
    return _from_wkb(geom.linestring_wkb(xy))


class QgisGeosBackend(GeometryBackend):
    name = 'qgis'

    def geos_version(self):
        from qgis.core import Qgis
        try:
            return Qgis.geosVersion()
        except AttributeError:
            return 'unknown'

    def is_simple(self, xy):
        return _line(xy).isSimple()

    def is_valid(self, wkb):
        return _from_wkb(wkb).isGeosValid()

    def noded_ends(self, xy):
        noded = QgsGeometry.unaryUnion([_line(xy)])
        if noded is None or noded.isNull():
            return []
        out = []
        for part in noded.asGeometryCollection():
            pl = part.asPolyline()
            if len(pl) < 2:
                continue
            out.append((pl[0].x(), pl[0].y()))
            out.append((pl[-1].x(), pl[-1].y()))
        return out

    def intersections(self, lines):
        geoms = []
        index = QgsSpatialIndex()
        for k, xy in enumerate(lines):
            line = _line(xy)
            geoms.append(line)
            index.addFeature(k, line.boundingBox())
        order = sorted(range(len(lines)), key=lambda i: -len(lines[i]))
        rank = dict((k, r) for r, k in enumerate(order))
        for k in order:
            line = geoms[k]
            cands = [j for j in index.intersects(line.boundingBox()) if rank[j] > rank[k]]
            if not cands:
                continue
            engine = QgsGeometry.createGeometryEngine(line.constGet())
            engine.prepareGeometry()
            for j in cands:
                other = geoms[j].constGet()
                if not engine.intersects(other):
                    continue
                inter = engine.intersection(other)
                if inter is None:
                    continue
                yield k, j, [(v.x(), v.y()) for v in inter.vertices()]
            del engine
