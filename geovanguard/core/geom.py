"""Geometry encoding helpers: WKB parsing/writing, quantisation, ring orientation.

Polygons are held internally as a list of *open* rings (no closing vertex) of
quantised int64 coordinates.  Quantisation is ``q = rint((x - origin) / grid)``
so that vertices shared by neighbouring polygons compare exactly equal.

Orientation convention (OGC): exterior rings counter-clockwise, interior rings
clockwise, i.e. the polygon body is always on the LEFT of every ring edge.  The
LPoly/RPoly labelling in ``topology.py`` relies on this.
"""
import struct

import numpy as np

WKB_LINESTRING = 2
WKB_POLYGON = 3
WKB_MULTIPOLYGON = 6


# ── WKB reading ────────────────────────────────────────────────────────────────

def _wkb_type_dims(raw_type):
    """Return (base_type, n_dims) for ISO and EWKB type codes."""
    has_z = bool(raw_type & 0x80000000)
    has_m = bool(raw_type & 0x40000000)
    code = raw_type & 0x0FFFFFFF
    if code >= 3000:
        has_z = has_m = True
        code -= 3000
    elif code >= 2000:
        has_m = True
        code -= 2000
    elif code >= 1000:
        has_z = True
        code -= 1000
    return code, 2 + int(has_z) + int(has_m)


def _read_polygon(buf, off, fmt_i, dt, dims, parts):
    (n_rings,) = struct.unpack_from(fmt_i, buf, off)
    off += 4
    rings = []
    for _ in range(n_rings):
        (n_pts,) = struct.unpack_from(fmt_i, buf, off)
        off += 4
        arr = np.frombuffer(buf, dtype=dt, count=n_pts * dims, offset=off)
        off += 8 * n_pts * dims
        rings.append(arr.reshape(n_pts, dims)[:, :2].astype(np.float64))
    parts.append(rings)
    return off


def _read_geometry(buf, off, parts):
    """Append polygon parts found at ``off``; points and lines are skipped."""
    order = buf[off]
    fmt_i = '<I' if order == 1 else '>I'
    dt = np.dtype('<f8') if order == 1 else np.dtype('>f8')
    (raw_type,) = struct.unpack_from(fmt_i, buf, off + 1)
    off += 5
    if raw_type & 0x20000000:          # EWKB SRID flag
        off += 4
    code, dims = _wkb_type_dims(raw_type)
    if code == WKB_POLYGON:
        return _read_polygon(buf, off, fmt_i, dt, dims, parts)
    if code == 1:                      # Point (e.g. from makeValid collections)
        return off + 8 * dims
    if code == WKB_LINESTRING:
        (n_pts,) = struct.unpack_from(fmt_i, buf, off)
        return off + 4 + 8 * dims * n_pts
    if code in (4, 5, WKB_MULTIPOLYGON, 7):   # Multi* and GeometryCollection
        (n_geoms,) = struct.unpack_from(fmt_i, buf, off)
        off += 4
        for _ in range(n_geoms):
            off = _read_geometry(buf, off, parts)
        return off
    raise ValueError('Unsupported WKB geometry type {}'.format(raw_type))


def parse_linestring_wkb(data):
    """Parse a (2D/Z/M) LineString WKB into a float64 ``(n, 2)`` array."""
    buf = bytes(data)
    fmt_i = '<I' if buf[0] == 1 else '>I'
    dt = np.dtype('<f8') if buf[0] == 1 else np.dtype('>f8')
    (raw_type,) = struct.unpack_from(fmt_i, buf, 1)
    off = 5 + (4 if raw_type & 0x20000000 else 0)
    code, dims = _wkb_type_dims(raw_type)
    if code != WKB_LINESTRING:
        raise ValueError('Not a LineString WKB')
    (n_pts,) = struct.unpack_from(fmt_i, buf, off)
    arr = np.frombuffer(buf, dtype=dt, count=n_pts * dims, offset=off + 4)
    return arr.reshape(n_pts, dims)[:, :2].astype(np.float64)


def parse_polygonal_wkb(data):
    """Parse Polygon / MultiPolygon WKB into ``[[ring_xy, ...], ...]`` (one list per part).

    Rings are float64 ``(n, 2)`` arrays exactly as stored (usually closed).
    Z and M ordinates are dropped.
    """
    parts = []
    _read_geometry(bytes(data), 0, parts)
    return parts


# ── WKB writing ────────────────────────────────────────────────────────────────

def _ring_bytes(xy):
    xy = np.asarray(xy, dtype='<f8')
    if len(xy) and (xy[0, 0] != xy[-1, 0] or xy[0, 1] != xy[-1, 1]):
        xy = np.vstack([xy, xy[:1]])
    return struct.pack('<I', len(xy)) + np.ascontiguousarray(xy).tobytes()


def polygon_wkb(shell, holes=()):
    """WKB Polygon from an exterior ring and interior rings (open or closed float arrays)."""
    rings = [shell] + list(holes)
    out = [struct.pack('<BII', 1, WKB_POLYGON, len(rings))]
    out.extend(_ring_bytes(r) for r in rings)
    return b''.join(out)


def multipolygon_wkb(polygons):
    """WKB MultiPolygon from ``[(shell, holes), ...]``."""
    out = [struct.pack('<BII', 1, WKB_MULTIPOLYGON, len(polygons))]
    out.extend(polygon_wkb(shell, holes) for shell, holes in polygons)
    return b''.join(out)


def linestring_wkb(xy):
    xy = np.ascontiguousarray(xy, dtype='<f8')
    return struct.pack('<BII', 1, WKB_LINESTRING, len(xy)) + xy.tobytes()


# ── Quantisation and orientation ───────────────────────────────────────────────

def signed_area2(ring):
    """Twice the signed area of an open ring (positive = counter-clockwise).

    Computed in float64 relative to the first vertex, so large quantised
    coordinates do not overflow.
    """
    if len(ring) < 3:
        return 0.0
    x = ring[:, 0].astype(np.float64) - float(ring[0, 0])
    y = ring[:, 1].astype(np.float64) - float(ring[0, 1])
    return float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def quantize_ring(xy, origin, grid):
    """Quantise a float ring to an open int64 ring without repeated vertices.

    Returns None when fewer than three distinct vertices remain.
    """
    q = np.empty((len(xy), 2), dtype=np.int64)
    q[:, 0] = np.rint((xy[:, 0] - origin[0]) / grid)
    q[:, 1] = np.rint((xy[:, 1] - origin[1]) / grid)
    if len(q) > 1 and q[0, 0] == q[-1, 0] and q[0, 1] == q[-1, 1]:
        q = q[:-1]
    if len(q) < 3:
        return None
    keep = np.any(q != np.roll(q, 1, axis=0), axis=1)
    if not keep.any():
        return None
    q = q[keep]
    return q if len(q) >= 3 else None


def normalize_polygon(parts, origin, grid):
    """Quantise and orient a parsed polygon; returns a flat list of open int64 rings.

    Exterior rings are made counter-clockwise and interior rings clockwise, so
    the polygon body is on the left of every directed ring edge.  Degenerate
    rings (zero area after quantisation) are dropped.
    """
    rings = []
    for part in parts:
        for idx, xy in enumerate(part):
            q = quantize_ring(xy, origin, grid)
            if q is None:
                continue
            area = signed_area2(q)
            if area == 0.0:
                continue
            want_ccw = idx == 0
            if (area > 0) != want_ccw:
                q = q[::-1].copy()
            rings.append(np.ascontiguousarray(q))
    return rings


def dequantize(q, origin, grid):
    xy = np.empty(q.shape, dtype=np.float64)
    xy[:, 0] = q[:, 0] * grid + origin[0]
    xy[:, 1] = q[:, 1] * grid + origin[1]
    return xy


# ── Compact blob encoding of quantised rings ──────────────────────────────────

def encode_rings(rings):
    """Encode open int64 rings as ``[n_rings, n_1..n_k, coords...]`` int64 bytes."""
    header = np.array([len(rings)] + [len(r) for r in rings], dtype=np.int64)
    chunks = [header] + [np.ascontiguousarray(r, dtype=np.int64).ravel() for r in rings]
    return np.concatenate(chunks).tobytes()


def decode_rings(blob):
    arr = np.frombuffer(blob, dtype=np.int64)
    n = int(arr[0])
    counts = arr[1:1 + n]
    rings = []
    off = 1 + n
    for c in counts:
        c = int(c)
        rings.append(arr[off:off + 2 * c].reshape(c, 2))
        off += 2 * c
    return rings


def encode_coords(xy, dtype=np.int64):
    return np.ascontiguousarray(xy, dtype=dtype).tobytes()


def decode_coords(blob, dtype=np.int64):
    return np.frombuffer(blob, dtype=dtype).reshape(-1, 2)


def rings_bbox(rings):
    """Bounding box (xmin, ymin, xmax, ymax) of quantised rings."""
    mins = np.min([r.min(axis=0) for r in rings], axis=0)
    maxs = np.max([r.max(axis=0) for r in rings], axis=0)
    return int(mins[0]), int(mins[1]), int(maxs[0]), int(maxs[1])


# ── Point in polygon / ring assembly helpers ──────────────────────────────────

def point_in_ring(pt, ring):
    """Even-odd ray casting test (float ring, open or closed)."""
    x, y = pt
    xs = ring[:, 0]
    ys = ring[:, 1]
    xs2 = np.roll(xs, -1)
    ys2 = np.roll(ys, -1)
    cond = (ys > y) != (ys2 > y)
    with np.errstate(divide='ignore', invalid='ignore'):
        xint = xs + (y - ys) * (xs2 - xs) / (ys2 - ys)
    return bool(np.count_nonzero(cond & (x < xint)) % 2)


def interior_probe(ring):
    """Midpoint of the longest edge of a hole ring.

    It lies strictly inside the enclosing shell unless the hole touches the
    shell along that edge, which would be invalid input.
    """
    nxt = np.roll(ring, -1, axis=0)
    seg = nxt - ring
    k = int(np.argmax(np.hypot(seg[:, 0], seg[:, 1])))
    return (ring[k] + nxt[k]) * 0.5


def assemble_polygons(ring_pairs):
    """Group rings into ``[(shell, [holes])]``.

    ``ring_pairs`` is ``[(topo_ring, out_ring), ...]``: the shell/hole decision
    and hole placement use the original (unsmoothed) ``topo_ring`` so smoothing
    can never change the polygon structure; the returned geometry uses
    ``out_ring``.  Counter-clockwise rings are shells, clockwise rings holes;
    each hole goes to the smallest shell containing it.  Returns None if a hole
    cannot be placed.
    """
    shells = []
    holes = []
    for topo, out in ring_pairs:
        topo = np.asarray(topo, dtype=np.float64)
        a = signed_area2(topo)
        if a > 0:
            shells.append((a, topo, out))
        elif a < 0:
            holes.append((topo, out))
    if not shells:
        return None
    if len(shells) == 1:
        return [(shells[0][2], [h[1] for h in holes])]
    shells.sort(key=lambda s: s[0])            # smallest first
    result = [(s[2], []) for s in shells]
    for h_topo, h_out in holes:
        probe = interior_probe(h_topo)
        for i, s in enumerate(shells):
            if point_in_ring(probe, s[1]):
                result[i][1].append(h_out)
                break
        else:
            return None
    return result
