"""Core (numpy-only) tests: run with any Python that has numpy.

    python tests/test_core.py
"""
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from geovanguard.core import geom, smoothing, topology  # noqa: E402


# ── tiny in-memory driver (the real one in pipeline.py uses sqlite) ───────────

def _bbox(rings):
    return geom.rings_bbox(rings)


def _intersects(bb, tb, pad=0):
    return not (bb[2] < tb[0] - pad or bb[0] > tb[2] + pad or
                bb[3] < tb[1] - pad or bb[1] > tb[3] + pad)


def build_arcs(polys, tile_q, tol=0, fixed_mode=topology.FIXED_NONE, extent=None):
    """polys: {pid: [open int64 rings oriented]} -> list of arc dicts."""
    allr = [r for rings in polys.values() for r in rings]
    xmin, ymin, xmax, ymax = geom.rings_bbox(allr)
    grid = topology.TileGrid.for_extent(xmin, ymin, xmax, ymax, tile_q)

    def ctx(t, pad=0):
        tb = grid.tile_bbox(t)
        return [(pid, rings) for pid, rings in sorted(polys.items())
                if _intersects(_bbox(rings), tb, pad)]

    ins = defaultdict(list)
    for t in range(grid.n_tiles):
        for pid, ri, s, tt, x, y in topology.find_insertions(ctx(t, tol), t, grid, tol):
            ins[(pid, ri)].append((s, tt, x, y))
    dens = {pid: [topology.densify_ring(r, ins.get((pid, ri), [])) for ri, r in enumerate(rings)]
            for pid, rings in polys.items()}

    pieces = []
    for t in range(grid.n_tiles):
        p, _ = topology.label_tile(
            [(pid, rings) for pid, rings in sorted(dens.items())
             if _intersects(_bbox(rings), grid.tile_bbox(t))],
            t, grid, fixed_mode, extent)
        pieces.extend(p)

    arcs = []
    cut = []
    for i, p in enumerate(pieces):
        if p['cut_start'] or p['cut_end']:
            cut.append((i, p['lpoly'], p['rpoly'], p['fixed'], p['ring'], p['start'],
                        p['end'], p['nseg'], p['cut_start'], p['cut_end']))
        else:
            arcs.append(dict(p))
    for chain, closed in topology.merge_pieces(cut):
        first = pieces[chain[0]]
        coords = topology.join_coords([pieces[i]['coords'] for i in chain])
        if closed:
            coords = topology.canonical_closed(coords)
        arcs.append({'lpoly': first['lpoly'], 'rpoly': first['rpoly'],
                     'fixed': first['fixed'], 'closed': closed, 'coords': coords})
    return arcs, dens, len(pieces)


def reconstruct(arcs, pid, smooth=None):
    use = []
    for a in arcs:
        c = a['coords']
        out = smooth(a) if smooth else c.astype(np.float64)
        if a['lpoly'] == pid:
            use.append((c, out))
        if a['rpoly'] == pid:
            use.append((c[::-1], out[::-1]))
    rings = topology.walk_faces(use)
    if rings is None:
        return None
    return geom.assemble_polygons(rings)


def area_of(polys):
    return sum(geom.signed_area2(np.asarray(s, float)) / 2 +
               sum(geom.signed_area2(np.asarray(h, float)) / 2 for h in holes)
               for s, holes in polys)


def sq(x0, y0, x1, y1):
    """CCW open square."""
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.int64)


def orient(rings):
    out = []
    for i, r in enumerate(rings):
        a = geom.signed_area2(r)
        if (a > 0) != (i == 0):
            r = r[::-1].copy()
        out.append(r)
    return out


# ── test cases ────────────────────────────────────────────────────────────────

def case_gdal_strip():
    # A's bottom edge has no vertex at the B|C junction (x=3) — like GDAL output.
    return {1: [sq(0, 2, 6, 4)], 2: [sq(0, 0, 3, 2)], 3: [sq(3, 0, 6, 2)]}


def case_grid9():
    polys = {}
    pid = 1
    for i in range(3):
        for j in range(3):
            polys[pid] = [sq(i * 10, j * 10, i * 10 + 10, j * 10 + 10)]
            pid += 1
    return polys


def case_island():
    # 1: frame with a hole, 2: fills the hole exactly, 3: neighbour of the frame,
    # 4: frame-with-hole whose hole is nodata (universe)
    return {1: orient([sq(0, 0, 10, 10), sq(3, 3, 7, 7)]),
            2: [sq(3, 3, 7, 7)],
            3: [sq(10, 0, 20, 10)],
            4: orient([sq(20, 0, 30, 10), sq(23, 3, 27, 7)])}


def case_pinch():
    # polygon 1 = two squares touching at a corner (MultiPolygon), 2 fills around
    p1 = [sq(0, 0, 5, 5), sq(5, 5, 10, 10)]
    p2a = sq(5, 0, 10, 5)
    p2b = sq(0, 5, 5, 10)
    return {1: p1, 2: [p2a], 3: [p2b]}


def case_long_shared():
    # long staircase boundary that crosses many tiles
    top = [[0, 50]]
    bot = []
    x = 0
    y = 20
    pts = [(0, y)]
    for k in range(19):
        x += 5
        pts.append((x, y))
        y += 1 if k % 2 == 0 else -1
        pts.append((x, y))
    pts.append((100, y))
    line = np.array(pts, dtype=np.int64)
    upper = np.vstack([line, [[100, 50], [0, 50]]])
    lower = np.vstack([[[0, 0], [100, 0]], line[::-1]])
    up = upper if geom.signed_area2(upper) > 0 else upper[::-1].copy()
    lo = lower if geom.signed_area2(lower) > 0 else lower[::-1].copy()
    del top, bot
    return {1: [up], 2: [lo]}


def case_touching_hole():
    # A's hole touches its shell at v=(20,0), which is a node (B|C below, D1|D2 in the hole)
    a = orient([sq(0, 0, 40, 40), np.array([[20, 0], [10, 10], [30, 10]], dtype=np.int64)])
    d1 = np.array([[20, 0], [20, 10], [10, 10]], dtype=np.int64)
    d2 = np.array([[20, 0], [30, 10], [20, 10]], dtype=np.int64)
    return {1: a, 2: [sq(0, -10, 20, 0)], 3: [sq(20, -10, 40, 0)], 4: [d1], 5: [d2]}


CASES = [case_gdal_strip, case_grid9, case_island, case_pinch, case_long_shared,
         case_touching_hole]


def check_case(fn, tile_q):
    polys = fn()
    arcs, dens, n_pieces = build_arcs(polys, tile_q)
    # every shared boundary exactly once: no arc appears twice
    seen = set()
    for a in arcs:
        c = a['coords']
        key = tuple(map(tuple, c)) if tuple(c[0]) <= tuple(c[-1]) else tuple(map(tuple, c[::-1]))
        assert key not in seen, 'duplicate arc'
        seen.add(key)
        assert a['lpoly'] != a['rpoly']
        assert a['rpoly'] == 0 or a['lpoly'] < a['rpoly']
    for pid, rings in polys.items():
        res = reconstruct(arcs, pid)
        assert res is not None, '{} pid {} walk failed (tile {})'.format(fn.__name__, pid, tile_q)
        want = sum(geom.signed_area2(r) / 2 for r in rings)
        got = area_of(res)
        assert abs(want - got) < 1e-9, '{} pid {} area {} != {}'.format(fn.__name__, pid, got, want)
        n_rings = sum(1 + len(h) for _, h in res)
        assert n_rings == len(rings), '{} pid {} rings {} != {}'.format(
            fn.__name__, pid, n_rings, len(rings))
    return len(arcs), n_pieces


def test_topology_exact():
    for fn in CASES:
        ref = None
        for tile_q in (1000, 7, 3, 2, 1):
            n_arcs, n_pieces = check_case(fn, tile_q)
            if ref is None:
                ref = n_arcs
            assert n_arcs == ref, '{}: arc count {} at tile {} != {}'.format(
                fn.__name__, n_arcs, tile_q, ref)
        print('  ok  {:<18} arcs={}'.format(fn.__name__, ref))


def test_lr_semantics():
    arcs, _, _ = build_arcs(case_gdal_strip(), 1000)
    pairs = sorted((a['lpoly'], a['rpoly']) for a in arcs)
    # A|B, A|C, B|C shared once each; outer arcs: A, B, C to universe
    assert pairs == [(1, 0), (1, 2), (1, 3), (2, 0), (2, 3), (3, 0)], pairs
    # interior ring: polygon body is on the LEFT (fix of the original)
    arcs, _, _ = build_arcs(case_island(), 1000)
    hole_arc = [a for a in arcs if a['lpoly'] == 4 and a['closed'] and
                geom.signed_area2(a['coords'][:-1]) < 0]
    assert len(hole_arc) == 1 and hole_arc[0]['rpoly'] == 0
    print('  ok  lpoly/rpoly semantics')


def test_smooth_shared():
    """After smoothing, neighbours share the exact same boundary coordinates."""
    polys = case_long_shared()
    arcs, _, _ = build_arcs(polys, 9, fixed_mode=topology.FIXED_OUTER)
    cache = {}

    def sm(a):
        k = id(a)
        if k not in cache:
            c = a['coords'].astype(np.float64)
            if a['fixed']:
                cache[k] = c
                return c
            s = smoothing.smooth_arc(c, a['closed'], 'Chaikin',
                                     {'pre_simplify_m': 0, 'iterations': 3, 'post_simplify_m': 0})
            cache[k] = c if s is None else s
        return cache[k]

    r1 = reconstruct(arcs, 1, sm)
    r2 = reconstruct(arcs, 2, sm)
    s1 = {tuple(p) for p in r1[0][0]}
    s2 = {tuple(p) for p in r2[0][0]}
    shared = s1 & s2
    assert len(shared) > 40, len(shared)
    total = abs(area_of(r1)) + abs(area_of(r2))
    assert abs(total - 100 * 50) < 1e-6, total
    print('  ok  smoothed shared boundary ({} common vertices, total area preserved)'.format(len(shared)))


def test_smoothing_endpoints():
    c = np.array([[0, 0], [1, 2], [3, 3], [5, 1], [8, 0]], float)
    for alg in smoothing.ALGORITHMS:
        out = smoothing.smooth_arc(c, False, alg, {})
        assert out is not None, alg
        assert tuple(out[0]) == (0, 0) and tuple(out[-1]) == (8, 0), (alg, out[0], out[-1])
    ring = np.array([[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]], float)
    for alg in smoothing.ALGORITHMS:
        out = smoothing.smooth_arc(ring, True, alg, {'pre_simplify_m': 0, 'tolerance_m': 0.1})
        assert out is not None and tuple(out[0]) == tuple(out[-1]), alg
    print('  ok  smoothing keeps arc end points / closes rings')


def test_batch_equals_single():
    """smooth_batch must return exactly what smooth_arc returns, arc by arc."""
    rng = np.random.RandomState(42)
    arcs, closed = [], []
    for k in range(400):
        n = int(rng.choice([2, 3, 4, 6, 12, 40, 300]))
        steps = np.round(rng.rand(n, 2) * 4 - 1)          # staircase-like, many ties
        c = np.cumsum(steps, axis=0) * 10.0
        if k % 3 == 0 and n >= 4:
            ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
            c = np.column_stack([np.cos(ang), np.sin(ang)]) * (10 + rng.rand(n, 1) * 5)
            c = np.round(c, 1)
            c = np.vstack([c, c[:1]])
            closed.append(True)
        else:
            closed.append(False)
        arcs.append(c)
    params = {'pre_simplify_m': 2.0, 'iterations': 4, 'post_simplify_m': 1.0, 'tolerance_m': 5.0}
    for alg in smoothing.BATCH_ALGORITHMS:
        for p in (params, dict(params, pre_simplify_m=0, post_simplify_m=0)):
            batch = smoothing.smooth_batch(arcs, closed, alg, p)
            for k, (c, r) in enumerate(zip(arcs, closed)):
                one = smoothing.smooth_arc(c, r, alg, p)
                if one is None or batch[k] is None:
                    assert one is None and batch[k] is None, (alg, k)
                else:
                    assert one.shape == batch[k].shape and np.array_equal(one, batch[k]), \
                        '{} arc {} differs'.format(alg, k)
    big = np.cumsum(rng.rand(5000, 2) - 0.5, axis=0)       # > 256 points: vectorised DP path
    keep_py = smoothing._dp_keep_py(big[:, 0].tolist(), big[:, 1].tolist(), 0.3 ** 2)
    assert np.array_equal(smoothing.simplify_dp(big, 0.3), big[np.array(keep_py)])
    print('  ok  batch smoothing identical to per-arc smoothing (Chaikin, Douglas-Peucker)')


def test_wkb_roundtrip():
    shell = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    hole = np.array([[2, 2], [2, 4], [4, 4], [4, 2]], float)
    w = geom.multipolygon_wkb([(shell, [hole]), (shell + 20, [])])
    parts = geom.parse_polygonal_wkb(w)
    assert len(parts) == 2 and len(parts[0]) == 2 and len(parts[0][0]) == 5
    rings = geom.normalize_polygon(parts, (0.0, 0.0), 0.5)
    assert geom.signed_area2(rings[0]) > 0 and geom.signed_area2(rings[1]) < 0
    blob = geom.encode_rings(rings)
    back = geom.decode_rings(blob)
    assert all(np.array_equal(a, b) for a, b in zip(rings, back))
    print('  ok  WKB / blob round trip')



def test_total_eta_steady_within_step():
    """A step slower than its weight must not make the Total ETA climb."""
    import re
    from geovanguard import progress

    class FB(object):
        def setProgress(self, p):
            pass

        def setProgressText(self, t):
            pass

        def pushInfo(self, m):
            pass

    clock = [0.0]
    real_time = progress.time.time
    progress.time.time = lambda: clock[0]
    try:
        tr = progress.StepTracker(FB(), ['polygonize', 'load', 'insert'])
        tr.start('polygonize')
        clock[0] = 60.0                       # fast step: 10 s per weight unit
        tr.done('polygonize')
        tr.start('load')
        totals = []
        for k in range(1, 10):                # slow step: 4.5x the weight's share
            clock[0] = 60.0 + 27.0 * k
            text = tr.eta('load', k / 10.0)
            totals.append(int(re.search(r'Total ETA ~(\d+):(\d+)', text).group(1)) * 60 +
                          int(re.search(r'Total ETA ~(\d+):(\d+)', text).group(2)))
        assert text.startswith('Step ETA '), text
        assert all(b <= a for a, b in zip(totals, totals[1:])), totals
    finally:
        progress.time.time = real_time
    print('  ok  Total ETA counts down within a slow step:', totals[0], '→', totals[-1], 's')


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            print(name)
            fn()
    print('ALL CORE TESTS PASSED')
