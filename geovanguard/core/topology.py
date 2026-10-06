"""LPoly/RPoly arc topology, ported from the original ForestType BuildTopology.py
and the reconstruction part of SmoothPolygon.py.

Terminology (same as the original):
  * poly_id   – global 1-based polygon id, 0 = "universe" (outside / nodata)
  * arc       – maximal piece of boundary with the same polygon on each side
  * lpoly_id  – polygon on the LEFT of the arc in its stored direction
  * rpoly_id  – polygon on the RIGHT (0 for the outer/universe boundary)

All rings are open int64 rings oriented so the polygon is on the left of every
edge (see geom.normalize_polygon).  A directed edge p→q of polygon A is shared
with polygon B exactly when B owns the reversed edge q→p, which makes the
LPoly/RPoly assignment an exact hash lookup once both rings carry the same
vertices.  ``find_insertions`` (the port of ``_insert_junctions``) guarantees
that by inserting every neighbour vertex that lies on an edge.

Large data: work is done tile by tile.  A vertex belongs to the tile that
contains it and an edge to the tile that contains its midpoint, so every
vertex/edge is processed exactly once whatever the tile size.  Arc pieces cut
by a tile edge are re-joined in ``merge_pieces``.
"""
import math
from collections import defaultdict

import numpy as np

_V32 = np.dtype((np.void, 32))

FIXED_NONE = 'none'          # smooth every arc
FIXED_OUTER = 'outer'        # keep arcs with rpoly_id == 0 straight
FIXED_EXTENT = 'extent'      # keep universe arcs lying on the raster extent straight


class TileGrid(object):
    """Regular tile grid in quantised coordinates."""

    def __init__(self, x0, y0, tile_q, nx, ny):
        self.x0 = int(x0)
        self.y0 = int(y0)
        self.tile_q = int(max(1, tile_q))
        self.nx = int(max(1, nx))
        self.ny = int(max(1, ny))

    @classmethod
    def for_extent(cls, xmin, ymin, xmax, ymax, tile_q):
        tile_q = int(max(1, tile_q))
        nx = int((xmax - xmin) // tile_q) + 1
        ny = int((ymax - ymin) // tile_q) + 1
        return cls(xmin, ymin, tile_q, nx, ny)

    @property
    def n_tiles(self):
        return self.nx * self.ny

    def tile_of(self, qx, qy):
        ix = np.clip((np.asarray(qx) - self.x0) // self.tile_q, 0, self.nx - 1)
        iy = np.clip((np.asarray(qy) - self.y0) // self.tile_q, 0, self.ny - 1)
        return iy * self.nx + ix

    def tile_bbox(self, tile_id):
        """Inclusive query box of a tile; edge tiles extend to infinity."""
        iy, ix = divmod(int(tile_id), self.nx)
        big = 2 ** 62
        xmin = self.x0 + ix * self.tile_q if ix > 0 else -big
        ymin = self.y0 + iy * self.tile_q if iy > 0 else -big
        xmax = self.x0 + (ix + 1) * self.tile_q if ix < self.nx - 1 else big
        ymax = self.y0 + (iy + 1) * self.tile_q if iy < self.ny - 1 else big
        return xmin, ymin, xmax, ymax

    def to_dict(self):
        return {'x0': self.x0, 'y0': self.y0, 'tile_q': self.tile_q,
                'nx': self.nx, 'ny': self.ny}

    @classmethod
    def from_dict(cls, d):
        return cls(d['x0'], d['y0'], d['tile_q'], d['nx'], d['ny'])


# ── helpers ────────────────────────────────────────────────────────────────────

def _expand_ranges(lo, hi):
    """For per-item index ranges [lo, hi) return (item_index, position) pairs."""
    counts = np.maximum(hi - lo, 0)
    total = int(counts.sum())
    if total == 0:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    item = np.repeat(np.arange(len(lo)), counts)
    starts = np.repeat(lo, counts)
    offs = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
    return item, starts + offs


def _context_segments(context):
    """Concatenate all ring edges of the context polygons."""
    a_list, b_list, pid_l, ring_l, seg_l = [], [], [], [], []
    for pid, rings in context:
        for ri, r in enumerate(rings):
            n = len(r)
            a_list.append(r)
            b_list.append(np.roll(r, -1, axis=0))
            pid_l.append(np.full(n, pid, np.int64))
            ring_l.append(np.full(n, ri, np.int64))
            seg_l.append(np.arange(n, dtype=np.int64))
    if not a_list:
        return None
    return (np.vstack(a_list), np.vstack(b_list), np.concatenate(pid_l),
            np.concatenate(ring_l), np.concatenate(seg_l))


# ── S2a: junction insertion (port of _insert_junctions) ───────────────────────

def _axis_insertions(a, b, idx, vert, axis):
    """Exact search of vertices lying strictly inside axis-parallel edges.

    axis=1: horizontal edges (constant y); axis=0: vertical edges (constant x).
    """
    if len(idx) == 0 or len(vert) == 0:
        return None
    fix_c, run_c = (1, 0) if axis == 1 else (0, 1)
    const = a[idx, fix_c]
    lo_v = np.minimum(a[idx, run_c], b[idx, run_c]) + 1
    hi_v = np.maximum(a[idx, run_c], b[idx, run_c]) - 1

    levels = np.unique(vert[:, fix_c])
    rmin = int(vert[:, run_c].min())
    width = int(vert[:, run_c].max()) - rmin + 1
    rank = np.searchsorted(levels, vert[:, fix_c])
    keys = rank * width + (vert[:, run_c] - rmin)
    order = np.argsort(keys, kind='stable')
    keys = keys[order]

    r = np.searchsorted(levels, const)
    ok = r < len(levels)
    ok[ok] = levels[r[ok]] == const[ok]
    if not ok.any():
        return None
    r = r[ok]
    sel = idx[ok]
    lo_off = np.clip(lo_v[ok] - rmin, 0, width)
    hi_off = np.clip(hi_v[ok] - rmin, -1, width - 1)
    lo = np.searchsorted(keys, r * width + lo_off, side='left')
    hi = np.searchsorted(keys, r * width + hi_off, side='right')
    hi = np.where(hi_off < lo_off, lo, hi)
    item, pos = _expand_ranges(lo, hi)
    if len(item) == 0:
        return None
    seg = sel[item]
    v = vert[order[pos]]
    span = (b[seg, run_c] - a[seg, run_c]).astype(np.float64)
    t = (v[:, run_c] - a[seg, run_c]) / span
    return seg, t, v


def _general_insertions(a, b, idx, vert, tol, max_pairs=2000000):
    """Vertices within ``tol`` of the interior of arbitrary edges."""
    if len(idx) == 0 or len(vert) == 0:
        return None
    order = np.argsort(vert[:, 0], kind='stable')
    vx = vert[order, 0]
    out_seg, out_t, out_v = [], [], []
    xmin = np.minimum(a[idx, 0], b[idx, 0]) - tol
    xmax = np.maximum(a[idx, 0], b[idx, 0]) + tol
    lo_all = np.searchsorted(vx, xmin, side='left')
    hi_all = np.searchsorted(vx, xmax, side='right')
    counts = hi_all - lo_all
    start = 0
    while start < len(idx):
        csum = np.cumsum(counts[start:])
        stop = start + max(1, int(np.searchsorted(csum, max_pairs, side='right')))
        item, pos = _expand_ranges(lo_all[start:stop], hi_all[start:stop])
        start_item = start
        start = stop
        if len(item) == 0:
            continue
        seg = idx[start_item + item]
        v = vert[order[pos]]
        pa = a[seg].astype(np.float64)
        d = (b[seg] - a[seg]).astype(np.float64)
        w = v.astype(np.float64) - pa
        l2 = d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1]
        t = (w[:, 0] * d[:, 0] + w[:, 1] * d[:, 1]) / np.where(l2 > 0, l2, 1.0)
        cross = d[:, 0] * w[:, 1] - d[:, 1] * w[:, 0]
        if tol > 0:
            near = np.abs(cross) <= tol * np.sqrt(l2)
        else:
            near = cross == 0
        inside = (t > 0) & (t < 1) & (l2 > 0)
        not_end = np.any(v != a[seg], axis=1) & np.any(v != b[seg], axis=1)
        keep = near & inside & not_end
        if keep.any():
            out_seg.append(seg[keep])
            out_t.append(t[keep])
            out_v.append(v[keep])
    if not out_seg:
        return None
    return np.concatenate(out_seg), np.concatenate(out_t), np.vstack(out_v)


def find_insertions(context, tile_id, grid, tol=0):
    """Find neighbour vertices lying on ring edges (port of ``_insert_junctions``).

    Only vertices that belong to ``tile_id`` are considered, so each
    (vertex, edge) incidence is found in exactly one tile.

    Returns a list of ``(poly_id, ring_idx, seg_idx, t, x, y)``.
    """
    segs = _context_segments(context)
    if segs is None:
        return []
    a, b, pid, ring, seg = segs
    all_v = np.vstack([r for _, rings in context for r in rings])
    vt = grid.tile_of(all_v[:, 0], all_v[:, 1])
    vert = all_v[vt == tile_id]
    if len(vert) == 0:
        return []
    vert = np.unique(vert, axis=0)

    # Only edges whose bbox (+tol) overlaps the vertex bbox can receive vertices.
    vmin = vert.min(axis=0) - tol
    vmax = vert.max(axis=0) + tol
    emin = np.minimum(a, b)
    emax = np.maximum(a, b)
    cand = np.nonzero(np.all(emax >= vmin, axis=1) & np.all(emin <= vmax, axis=1))[0]
    if len(cand) == 0:
        return []

    horiz = a[cand, 1] == b[cand, 1]
    vert_e = (a[cand, 0] == b[cand, 0]) & ~horiz
    other = ~horiz & ~vert_e

    found = []
    for res in (_axis_insertions(a, b, cand[horiz], vert, 1),
                _axis_insertions(a, b, cand[vert_e], vert, 0),
                _general_insertions(a, b, cand[other], vert, tol)):
        if res is not None:
            found.append(res)
    if tol > 0:
        # near-miss vertices beside axis-parallel edges (vector data only)
        res = _general_insertions(a, b, cand[~other], vert, tol)
        if res is not None:
            s, t, v = res
            on_line = np.where(a[s, 1] == b[s, 1], v[:, 1] == a[s, 1], v[:, 0] == a[s, 0])
            if (~on_line).any():
                found.append((s[~on_line], t[~on_line], v[~on_line]))

    out = []
    for s, t, v in found:
        for k in range(len(s)):
            e = int(s[k])
            out.append((int(pid[e]), int(ring[e]), int(seg[e]), float(t[k]),
                        int(v[k, 0]), int(v[k, 1])))
    return out


def densify_ring(ring, insertions):
    """Insert ``[(seg_idx, t, x, y), ...]`` into an open ring."""
    if not insertions:
        return ring
    ins = sorted(set(insertions))
    pos = np.array([s + 1 for s, _, _, _ in ins], dtype=np.int64)
    vals = np.array([(x, y) for _, _, x, y in ins], dtype=np.int64)
    out = np.insert(ring, pos, vals, axis=0)
    keep = np.any(out != np.roll(out, 1, axis=0), axis=1)
    return np.ascontiguousarray(out[keep])


# ── S2b: LPoly/RPoly labelling and arc pieces (port of _build_tile_topology) ──

def _runs(lab):
    """Circular runs of equal non-negative labels: [(start, end_incl, closed)]."""
    n = len(lab)
    prev = np.roll(lab, 1)
    brk = np.nonzero(lab != prev)[0]
    if len(brk) == 0:
        return [(0, n - 1, True)] if lab[0] >= 0 else []
    runs = []
    nb = len(brk)
    for k in range(nb):
        s = int(brk[k])
        if lab[s] < 0:
            continue
        e = (int(brk[(k + 1) % nb]) - 1) % n
        runs.append((s, e, False))
    return runs


def label_tile(context, tile_id, grid, fixed_mode=FIXED_NONE, extent_q=None):
    """Build LPoly/RPoly arc pieces for the edges owned by one tile.

    An edge belongs to the tile containing its midpoint.  For each owned edge
    p→q of polygon A, the right-hand polygon is the owner of q→p (0 if none).
    Every shared boundary is emitted once, from the side with the smaller
    poly_id (``lpoly_id < rpoly_id``); universe edges are emitted by their only
    owner.  This replaces the original's "emit both directions then dedup".

    Returns ``(pieces, stats)`` where each piece is a dict with keys
    lpoly, rpoly, fixed, ring, start, end, nseg, closed, cut_start, cut_end, coords.
    """
    rings_meta = []
    keys = []
    owners = []
    for pid, rings in context:
        for ri, r in enumerate(rings):
            nxt = np.roll(r, -1, axis=0)
            mx = (r[:, 0] + nxt[:, 0]) // 2
            my = (r[:, 1] + nxt[:, 1]) // 2
            in_t = grid.tile_of(mx, my) == tile_id
            if not in_t.any():
                continue
            rings_meta.append((pid, ri, r, nxt, in_t))
            keys.append(np.hstack([r[in_t], nxt[in_t]]))
            owners.append(np.full(int(in_t.sum()), pid, np.int64))

    stats = {'edges': 0, 'duplicate_edges': 0, 'shared_edges': 0}
    if not rings_meta:
        return [], stats

    k_all = np.ascontiguousarray(np.vstack(keys), dtype=np.int64)
    o_all = np.concatenate(owners)
    kv = k_all.view(_V32).ravel()
    order = np.argsort(kv, kind='stable')
    skv = kv[order]
    stats['edges'] = len(kv)
    stats['duplicate_edges'] = int(np.count_nonzero(skv[1:] == skv[:-1]))
    rev = np.ascontiguousarray(k_all[:, [2, 3, 0, 1]]).view(_V32).ravel()
    pos = np.minimum(np.searchsorted(skv, rev), len(skv) - 1)
    found = skv[pos] == rev
    nb_all = np.where(found, o_all[order[pos]], 0)
    stats['shared_edges'] = int(np.count_nonzero(found))

    if fixed_mode == FIXED_OUTER:
        fixed_all = nb_all == 0
    elif fixed_mode == FIXED_EXTENT and extent_q is not None:
        ex0, ey0, ex1, ey1 = extent_q
        x0, y0, x1, y1 = k_all[:, 0], k_all[:, 1], k_all[:, 2], k_all[:, 3]
        on_ext = (((x0 == x1) & ((x0 == ex0) | (x0 == ex1))) |
                  ((y0 == y1) & ((y0 == ey0) | (y0 == ey1))))
        fixed_all = (nb_all == 0) & on_ext
    else:
        fixed_all = np.zeros(len(nb_all), dtype=bool)

    pieces = []
    off = 0
    for pid, ri, r, nxt, in_t in rings_meta:
        m = int(in_t.sum())
        nb = nb_all[off:off + m]
        fx = fixed_all[off:off + m]
        off += m
        emit = (nb == 0) | (nb > pid)
        lab = np.full(len(r), -1, np.int64)
        idx_in = np.nonzero(in_t)[0]
        lab[idx_in[emit]] = nb[emit] * 2 + fx[emit].astype(np.int64)
        if not (lab >= 0).any():
            continue
        n = len(r)
        for s, e, closed in _runs(lab):
            if closed:
                coords = canonical_closed(np.vstack([r, r[:1]]))
                cut_s = cut_e = False
            else:
                length = (e - s) % n + 1
                vidx = (s + np.arange(length + 1)) % n
                coords = r[vidx]
                cut_s = not in_t[(s - 1) % n]
                cut_e = not in_t[(e + 1) % n]
            label = int(lab[s])
            pieces.append({
                'lpoly': int(pid), 'rpoly': label // 2, 'fixed': bool(label % 2),
                'ring': int(ri), 'start': int(s), 'end': int(e), 'nseg': int(n),
                'closed': bool(closed), 'cut_start': bool(cut_s), 'cut_end': bool(cut_e),
                'coords': np.ascontiguousarray(coords, dtype=np.int64),
            })
    return pieces, stats


# ── S3: join pieces cut by tile edges ─────────────────────────────────────────

def merge_pieces(metas):
    """Chain arc pieces that were cut by tile edges.

    ``metas``: iterable of tuples
    ``(piece_id, lpoly, rpoly, fixed, ring, start, end, nseg, cut_start, cut_end)``
    for pieces that have at least one cut end.  A piece continues into the
    piece of the same polygon ring that starts at the next edge index and has
    the same (rpoly, fixed) label.

    Returns a list of ``(piece_ids, closed)``.
    """
    metas = list(metas)
    info = {}
    by_start = {}
    for m in metas:
        pid_ = m[0]
        info[pid_] = m
        if m[8]:
            by_start[(m[1], m[4], m[5])] = pid_
    nxt = {}
    for m in metas:
        if not m[9]:
            continue
        q = by_start.get((m[1], m[4], (m[6] + 1) % m[7]))
        if q is not None:
            qm = info[q]
            if qm[2] == m[2] and qm[3] == m[3]:
                nxt[m[0]] = q
    has_prev = set(nxt.values())
    chains = []
    visited = set()
    for m in sorted(metas):
        p = m[0]
        if p in visited or p in has_prev:
            continue
        chain = [p]
        visited.add(p)
        while chain[-1] in nxt:
            q = nxt[chain[-1]]
            if q in visited:
                break
            chain.append(q)
            visited.add(q)
        chains.append((chain, False))
    for m in sorted(metas):
        p = m[0]
        if p in visited:
            continue
        chain = [p]
        visited.add(p)
        while chain[-1] in nxt and nxt[chain[-1]] not in visited:
            q = nxt[chain[-1]]
            chain.append(q)
            visited.add(q)
        closed = nxt.get(chain[-1]) == p
        chains.append((chain, closed))
    return chains


def join_coords(parts):
    """Concatenate consecutive coordinate arrays that share end/start vertices."""
    if len(parts) == 1:
        return parts[0]
    return np.vstack([parts[0]] + [p[1:] for p in parts[1:]])


def canonical_closed(coords):
    """Rotate a closed arc (first == last) to start at its smallest vertex.

    A closed arc has no node, so its start vertex is arbitrary — but it does
    matter to Douglas-Peucker (which always keeps the start vertex).  A
    canonical start makes the result independent of the tile size.
    """
    ring = coords[:-1]
    k = int(np.lexsort((ring[:, 1], ring[:, 0]))[0])
    if k:
        ring = np.roll(ring, -k, axis=0)
    return np.ascontiguousarray(np.vstack([ring, ring[:1]]))


# ── S5: reconstruct a polygon by walking its LPoly/RPoly arcs ─────────────────

def _first_dir(c):
    d = c[1:] - c[0]
    nz = np.nonzero(np.any(d != 0, axis=1))[0]
    v = d[nz[0]] if len(nz) else np.array([1, 0])
    return math.atan2(float(v[1]), float(v[0]))


def _last_dir(c):
    d = c[-1] - c[:-1]
    nz = np.nonzero(np.any(d != 0, axis=1))[0]
    v = d[nz[-1]] if len(nz) else np.array([1, 0])
    return math.atan2(float(v[1]), float(v[0]))


def walk_faces(arcs):
    """Assemble rings for one polygon from its directed arcs.

    ``arcs``: list of ``(topo_coords, out_coords)``.  ``topo_coords`` is the
    original quantised arc, already oriented so the polygon is on its LEFT
    (forward when the polygon is lpoly, reversed when it is rpoly);
    ``out_coords`` is the geometry to emit (smoothed or original) in the same
    direction with identical end points.

    At a node with several outgoing arcs the next arc is the first one
    clockwise from the reverse of the incoming direction — the standard
    "face on the left" rule — evaluated on the original geometry so that
    smoothing cannot change the topology.

    Returns ``[(topo_ring, out_ring), ...]`` (open rings) or None if the arcs
    do not form closed rings.
    """
    n = len(arcs)
    if n == 0:
        return None
    start_key = []
    end_key = []
    out_by_node = defaultdict(list)
    for i, (tc, _) in enumerate(arcs):
        sk = (int(tc[0, 0]), int(tc[0, 1]))
        ek = (int(tc[-1, 0]), int(tc[-1, 1]))
        start_key.append(sk)
        end_key.append(ek)
        out_by_node[sk].append(i)

    first_dir = {}
    used = [False] * n
    rings = []
    for i0 in range(n):
        if used[i0]:
            continue
        used[i0] = True
        seq = [i0]
        cur = i0
        while True:
            cands = out_by_node.get(end_key[cur], ())
            if not cands:
                return None
            if len(cands) == 1:
                nxt = cands[0]
            else:
                back = _last_dir(arcs[cur][0]) + math.pi
                best = None
                best_ang = None
                for j in cands:
                    if j not in first_dir:
                        first_dir[j] = _first_dir(arcs[j][0])
                    ang = (back - first_dir[j]) % (2 * math.pi)
                    if ang <= 1e-12:
                        ang = 2 * math.pi
                    if best_ang is None or ang < best_ang:
                        best, best_ang = j, ang
                nxt = best
            if nxt == i0:
                break
            if used[nxt]:
                return None
            used[nxt] = True
            seq.append(nxt)
            cur = nxt
        for loop in _split_loops(seq, start_key):
            topo = np.vstack([arcs[j][0][:-1] for j in loop])
            out = np.vstack([arcs[j][1][:-1] for j in loop])
            rings.append((topo, out))
    return rings


def _split_loops(seq, start_key):
    """Split an arc cycle that passes a node twice into simple loops.

    The face-on-left walk joins a hole that touches its shell at a node into
    one self-touching ring, which OGC/GEOS consider invalid; splitting at the
    repeated node yields the valid shell + hole (or shell + shell) rings.
    """
    stack = []
    pos = {}
    loops = []
    for j in seq:
        node = start_key[j]
        k = pos.get(node)
        if k is not None:
            loop = stack[k:]
            loops.append(loop)
            for a in loop:
                pos.pop(start_key[a], None)
            stack = stack[:k]
        pos[node] = len(stack)
        stack.append(j)
    if stack:
        loops.append(stack)
    return loops
