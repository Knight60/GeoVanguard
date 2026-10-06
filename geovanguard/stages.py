"""Pure computation of the pipeline stages — no database, no QGIS.

Every function takes plain values (numpy arrays, bytes, dicts) and returns
plain values, so the same code runs in the main process (1 worker) or in
worker processes (geovanguard.workers); the database writes stay in the main
process.  Both ways give bit-identical results.
"""
import numpy as np

from .core import geom, smoothing, topology
from .core.store import (ARC_FALLBACK, ARC_FALLBACK2, ARC_ORIGINAL, ARC_REVERTED,
                         ARC_SMOOTHED, RES_INVALID, RES_OK, RES_WALK_FAILED)

USES_SMOOTH_BLOB = (ARC_SMOOTHED, ARC_FALLBACK, ARC_FALLBACK2)
# half widths (in arc vertices) of the gently smoothed windows around conflicts
REPAIR_WINDOWS = (8, 24, 72, 216)

def self_crossings(backend, xy, closed):
    """Points where a line crosses or touches itself (GEOS noding)."""
    ends = {}
    for key in backend.noded_ends(xy):
        ends[key] = ends.get(key, 0) + 1
    start = (float(xy[0, 0]), float(xy[0, 1]))
    end = (float(xy[-1, 0]), float(xy[-1, 1]))
    out = []
    for key, cnt in ends.items():
        # a self-crossing node joins >= 4 part ends; the arc ends join 1
        # (2 for a closed arc's start/end)
        if key in (start, end) and cnt <= (2 if closed else 1):
            continue
        if cnt >= 3:
            out.append(key)
    return out


def smoothable(c, closed):
    """Arcs with no interior vertex cannot change shape: skip them."""
    return len(c) >= 5 if closed else len(c) >= 3


def shift(xy, ox, oy):
    out = np.array(xy, dtype=np.float64, copy=True)
    out[:, 0] += ox
    out[:, 1] += oy
    return out



# ── S1 load ───────────────────────────────────────────────────────────────────

def load_batch(records, origin, g):
    """Quantise and orient a batch of input polygons ``[(src_fid, attr, wkb)]``.

    Returns ``(rows, skipped, n_vertices, bbox or None)`` with the polys table
    rows ``(src_fid, attr, minx, miny, maxx, maxy, geom_blob)``.
    """
    rows = []
    skipped = n_vert = 0
    bb_all = None
    for src_fid, attr, wkb in records:
        try:
            parts = geom.parse_polygonal_wkb(wkb)
        except ValueError:
            skipped += 1
            continue
        rings = geom.normalize_polygon(parts, origin, g)
        if not rings:
            skipped += 1
            continue
        bb = geom.rings_bbox(rings)
        n_vert += sum(len(r) for r in rings)
        if bb_all is None:
            bb_all = list(bb)
        else:
            bb_all = [min(bb_all[0], bb[0]), min(bb_all[1], bb[1]),
                      max(bb_all[2], bb[2]), max(bb_all[3], bb[3])]
        rows.append((src_fid, attr, bb[0], bb[1], bb[2], bb[3], geom.encode_rings(rings)))
    return rows, skipped, n_vert, bb_all


# ── S2 tiles ──────────────────────────────────────────────────────────────────

def _decode_ctx(ctx):
    return [(pid, geom.decode_rings(blob)) for pid, blob in ctx]


def insert_tile(ctx, tile, grid, tol_q):
    """Junction vertices to insert for one tile: ``[(poly_id, ring, seg, t, x, y)]``."""
    if not ctx:
        return []
    return topology.find_insertions(_decode_ctx(ctx), tile, grid, tol_q)


def densify_batch(items):
    """``[(poly_id, geom_blob, {ring: [(seg, t, x, y)]})]`` → ``[(blob, bbox, poly_id)]``."""
    out = []
    for pid, blob, ins in items:
        rings = geom.decode_rings(blob)
        new = [topology.densify_ring(r, ins.get(ri, [])) for ri, r in enumerate(rings)]
        out.append((geom.encode_rings(new), tuple(geom.rings_bbox(new)), pid))
    return out


def label_tile(ctx, tile, grid, fixed_mode, extent_q):
    """LPoly/RPoly arc pieces of one tile → ``(pieces table rows, duplicate edges)``."""
    if not ctx:
        return [], 0
    pieces, stt = topology.label_tile(_decode_ctx(ctx), tile, grid, fixed_mode, extent_q)
    rows = [(tile, p['lpoly'], p['rpoly'], int(p['fixed']), p['ring'], p['start'], p['end'],
             p['nseg'], int(p['closed']), int(p['cut_start']), int(p['cut_end']),
             geom.encode_coords(p['coords'])) for p in pieces]
    return rows, stt['duplicate_edges']


# ── S4 smoothing of arcs (also used by the repair stage) ──────────────────────

class ArcSmoother(object):
    """Smooths arcs with the user's setting and its gentler fall-backs."""

    def __init__(self, algorithm, alg_params, backend):
        self.algorithm = algorithm
        self.alg_params = alg_params
        self.backend = backend

    def smooth_rows(self, rows, g):
        """Smooth a batch of arcs ``[(arc_id, closed, coords_blob), ...]``.

        Returns the arcs table updates ``[(smooth_blob or None, state, arc_id)]``.
        """
        coords = [geom.decode_coords(r[2]).astype(np.float64) * g for r in rows]
        closed = [bool(r[1]) for r in rows]
        todo = [k for k in range(len(rows)) if smoothable(coords[k], closed[k])]
        res = [None] * len(rows)
        for k, s in zip(todo, smoothing.smooth_batch([coords[k] for k in todo],
                                                     [closed[k] for k in todo],
                                                     self.algorithm, self.alg_params)):
            res[k] = s
        upd = []
        for k, (arc_id, _, _) in enumerate(rows):
            s = self._check_smoothed(coords[k], closed[k], res[k])
            state = ARC_SMOOTHED
            if s is None and res[k] is not None:
                # the smoothed arc crosses itself somewhere: fix it locally
                state, s = self._smooth_robust(coords[k], closed[k], res[k])
            if s is None:
                upd.append((None, ARC_ORIGINAL, arc_id))
            else:
                upd.append((geom.encode_coords(s, np.float64), state, arc_id))
        return upd

    def _smooth_robust(self, c, closed, smoothed):
        """Smooth an arc whose smoothed version crosses itself.

        Long arcs (e.g. the outer ring of a large polygon) usually cross
        themselves only at a few narrow places.  Instead of discarding the
        whole smoothing, the original arc is cut into pieces around each
        crossing: pieces away from the crossings get the normal smoothing,
        pieces around them only Chaikin corner cutting (which stays close to
        the original line).  The window grows until the result is simple.
        Last resort: the gentle ladder on the whole arc.
        Returns (state, coords or None).
        """
        crossings = self_crossings(self.backend, smoothed, closed)
        if len(crossings) and len(c) >= 40:
            for half_width in REPAIR_WINDOWS:
                s = self._smooth_windowed(c, closed, crossings, half_width)
                if s is not None:
                    return ARC_SMOOTHED, s
                if 2 * half_width * len(crossings) >= len(c):
                    break
        return self._step_down(c, closed, ARC_SMOOTHED)

    def _smooth_windowed(self, c, closed, crossings, half_width):
        pts = c[:-1] if closed else c
        n = len(pts)
        # nearest original vertex of each crossing
        near = sorted({int(np.argmin((pts[:, 0] - x) ** 2 + (pts[:, 1] - y) ** 2))
                       for x, y in crossings})
        bad = np.zeros(n, dtype=bool)
        for i in near:
            lo, hi = i - half_width, i + half_width
            if closed:
                bad[np.arange(lo, hi + 1) % n] = True
            else:
                bad[max(lo, 0):min(hi, n - 1) + 1] = True
        if bad.all():
            return None
        if closed:
            # start the sequence at a good vertex right after a bad window
            starts = np.nonzero(bad & ~np.roll(bad, -1))[0]
            k0 = (int(starts[0]) + 1) % n if len(starts) else 0
            pts = np.vstack([np.roll(pts, -k0, axis=0), np.roll(pts, -k0, axis=0)[:1]])
            bad = np.append(np.roll(bad, -k0), False)
        flags = bad.astype(np.int8)
        cuts = [0] + [i for i in range(1, len(pts) - 1) if flags[i] != flags[i - 1]] + [len(pts) - 1]
        good_parts = {}
        for gentle_alg, gentle in self._gentle_levels():
            parts = []
            for a, b in zip(cuts[:-1], cuts[1:]):
                piece = pts[a:b + 1]
                if not bad[a] and a in good_parts:
                    parts.append(good_parts[a])
                    continue
                alg, params = ((gentle_alg, gentle) if bad[a]
                               else (self.algorithm, self.alg_params))
                s = smoothing.smooth_arc(piece, False, alg, params) if len(piece) >= 3 else None
                s = piece.copy() if s is None else s
                s[0] = piece[0]
                s[-1] = piece[-1]
                if not bad[a]:
                    good_parts[a] = s
                parts.append(s)
            out = topology.join_coords(parts)
            if closed:
                # no need to rotate back: a closed arc has no node
                out[-1] = out[0]
            out = self._check_smoothed(c, closed, out)
            if out is not None:
                return out
        return None

    def _smooth_one(self, c, closed, algorithm=None, params=None):
        if not smoothable(c, closed):
            return None
        s = smoothing.smooth_arc(c, closed, algorithm or self.algorithm,
                                 self.alg_params if params is None else params)
        return self._check_smoothed(c, closed, s)

    def _check_smoothed(self, c, closed, s):
        """Accept a smoothed arc only if it keeps its nodes and stays simple."""
        if s is None:
            return None
        if closed:
            s[-1] = s[0]
            a0 = geom.signed_area2(c[:-1])
            a1 = geom.signed_area2(s[:-1])
            if len(s) < 4 or a1 == 0 or (a0 > 0) != (a1 > 0):
                return None
        else:
            s[0] = c[0]
            s[-1] = c[-1]
        if not self.backend.is_simple(s):
            return None
        return s

    def _gentle_levels(self):
        """Gentler variants of the user setting: [(algorithm, params), ...].

        Level 1 halves the strength (pre-simplify for Chaikin/splines, sigma
        for Gaussian); level 2 is Chaikin corner cutting without any
        simplification, which stays very close to the original line.
        """
        p = dict(self.alg_params)
        if self.algorithm == 'Gaussian':
            half = ('Gaussian', dict(p, sigma_m=p.get('sigma_m', 10.0) * 0.5))
        else:
            half = ('Chaikin', dict(p, pre_simplify_m=p.get('pre_simplify_m', 2.0) * 0.5))
        corner = ('Chaikin', dict(p, pre_simplify_m=0.0, post_simplify_m=0.0))
        return [half, corner]

    def _step_down(self, c, closed, state):
        """Next, gentler smoothing for a conflicting arc: (state, coords or None).

        Ladder: user setting → Chaikin with half the pre-simplify tolerance →
        Chaikin without simplification (pure corner cutting, stays very close
        to the original line) → original.  Douglas-Peucker (a simplifier, not a
        smoother) steps to half its tolerance, then to the original.
        """
        p = dict(self.alg_params)
        if self.algorithm == 'Douglas-Peucker':
            if state == ARC_SMOOTHED:
                p['tolerance_m'] = p.get('tolerance_m', 5.0) * 0.5
                return ARC_FALLBACK2, self._smooth_one(c, closed, 'Douglas-Peucker', p)
            return ARC_REVERTED, None
        half, corner = self._gentle_levels()
        steps = []
        if state == ARC_SMOOTHED:
            steps.append((ARC_FALLBACK, half))
        if state in (ARC_SMOOTHED, ARC_FALLBACK):
            steps.append((ARC_FALLBACK2, corner))
        for new_state, (alg, params) in steps:
            s = self._smooth_one(c, closed, alg, params)
            if s is not None:
                return new_state, s
        return ARC_REVERTED, None



# ── S5 reconstruction ─────────────────────────────────────────────────────────

def group_arcs(rows, g, lo=None, hi=None, wanted=None):
    """Orient each arc so the polygon is on its left and group by poly_id."""
    by_pid = {}
    for lp, rp, cblob, sblob, state in rows:
        q = geom.decode_coords(cblob)
        if state in USES_SMOOTH_BLOB and sblob is not None:
            out = geom.decode_coords(sblob, np.float64)
        else:
            out = q.astype(np.float64) * g
        for pid, forward in ((lp, True), (rp, False)):
            if pid == 0:
                continue
            if wanted is not None:
                if pid not in wanted:
                    continue
            elif not (lo <= pid < hi):
                continue
            if forward:
                by_pid.setdefault(pid, []).append((q, out))
            else:
                by_pid.setdefault(pid, []).append((q[::-1], out[::-1]))
    return by_pid


def rebuild(pids, arcs_by_pid, origin, backend):
    """Reconstruct the given polygons; returns result rows (poly_id, status, wkb)."""
    ox, oy = origin
    out = []
    for pid in pids:
        items = arcs_by_pid.get(pid)
        if not items:
            out.append((pid, RES_WALK_FAILED, None))
            continue
        rings = topology.walk_faces(items)
        polys = geom.assemble_polygons(rings) if rings is not None else None
        if polys is None:
            out.append((pid, RES_WALK_FAILED, None))
            continue
        shifted = [(shift(s, ox, oy), [shift(h, ox, oy) for h in holes]) for s, holes in polys]
        wkb = (geom.polygon_wkb(*shifted[0]) if len(shifted) == 1
               else geom.multipolygon_wkb(shifted))
        ok = backend.is_valid(wkb)
        out.append((pid, RES_OK if ok else RES_INVALID, wkb if ok else None))
    return out


def rebuild_range(arc_rows, pids, g, lo, hi, origin, backend):
    """Reconstruct the polygons ``lo <= poly_id < hi`` from their arcs."""
    return rebuild(pids, group_arcs(arc_rows, g, lo=lo, hi=hi), origin, backend)
