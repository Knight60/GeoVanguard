"""Arc smoothing algorithms, ported from the original ForestType SmoothPolygon.py (_alg_*).

Differences from the original:
  * shapely ``simplify(preserve_topology=True)`` is replaced by our own
    Douglas-Peucker (``simplify_dp``).  Self-intersections are checked by the
    caller afterwards (the original also re-checked ``is_simple``).
  * scipy stays optional, exactly as in the original: B-Spline and Bezier
    fall back to Chaikin when scipy is not importable.
  * Parameter keys keep their original names (``*_m``) but are expressed in
    layer units.
  * Chaikin and Douglas-Peucker also have batch versions (``smooth_batch``)
    that process thousands of arcs per numpy call — the single-process
    replacement for the original ProcessPoolExecutor.  They return exactly
    the same coordinates as the per-arc functions.

Every function takes float ``(n, 2)`` coordinates.  Open arcs keep their two
end points exactly (they are topology nodes shared with other arcs).  Closed
arcs (``is_ring``) are passed with the closing vertex repeated and returned
closed.
"""
import numpy as np

# ordered best → worst for pixel-staircase boundaries (parameter comparisons
# on Sentinel-2 classifications): this is also the order of the dialog's drop-down
ALGORITHMS = ('Gaussian', 'Chaikin', 'B-Spline', 'Bezier', 'Douglas-Peucker')
BATCH_ALGORITHMS = ('Chaikin', 'Douglas-Peucker')


# ── Douglas-Peucker (replacement for shapely simplify) ─────────────────────────
# Distances are compared squared, with the same arithmetic in the scalar and
# the batch version, so both make bit-identical decisions.

def _dp_keep_py(xs, ys, tol2):
    """Scalar Douglas-Peucker (fast for short lines); returns keep flags."""
    n = len(xs)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax = xs[i]
        ay = ys[i]
        dx = xs[j] - ax
        dy = ys[j] - ay
        l2 = dx * dx + dy * dy
        best = -1.0
        bk = -1
        for k in range(i + 1, j):
            ex = xs[k] - ax
            ey = ys[k] - ay
            if l2 > 0.0:
                t = (ex * dx + ey * dy) / l2
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
            else:
                t = 0.0
            fx = ex - t * dx
            fy = ey - t * dy
            d = fx * fx + fy * fy
            if d > best:
                best = d
                bk = k
        if best > tol2:
            keep[bk] = True
            stack.append((i, bk))
            stack.append((bk, j))
    return keep


def _dp_keep_batch(xs, ys, starts, ends, tol2):
    """Level-synchronous Douglas-Peucker over many polylines at once.

    ``xs, ys``: concatenated coordinates; polyline i spans
    ``starts[i]..ends[i]`` (inclusive); ``tol2``: squared tolerance per
    polyline.  All open intervals of all polylines are split together, one
    numpy pass per recursion level.
    """
    keep = np.zeros(len(xs), dtype=bool)
    keep[starts] = True
    keep[ends] = True
    i_arr = np.asarray(starts, dtype=np.int64)
    j_arr = np.asarray(ends, dtype=np.int64)
    t_arr = np.asarray(tol2, dtype=np.float64)
    while len(i_arr):
        cnt = j_arr - i_arr - 1
        live = cnt > 0
        if not live.all():
            i_arr, j_arr, t_arr, cnt = i_arr[live], j_arr[live], t_arr[live], cnt[live]
            if not len(i_arr):
                break
        offs = np.cumsum(cnt) - cnt
        total = int(offs[-1] + cnt[-1])
        seg = np.repeat(np.arange(len(i_arr)), cnt)
        k = np.arange(total) - offs[seg] + i_arr[seg] + 1
        ax = xs[i_arr][seg]
        ay = ys[i_arr][seg]
        dx = (xs[j_arr] - xs[i_arr])[seg]
        dy = (ys[j_arr] - ys[i_arr])[seg]
        ex = xs[k] - ax
        ey = ys[k] - ay
        l2 = dx * dx + dy * dy
        pos = l2 > 0.0
        t = np.zeros(total)
        t[pos] = np.clip((ex[pos] * dx[pos] + ey[pos] * dy[pos]) / l2[pos], 0.0, 1.0)
        fx = ex - t * dx
        fy = ey - t * dy
        d2 = fx * fx + fy * fy
        mx = np.maximum.reduceat(d2, offs)
        first = np.minimum.reduceat(np.where(d2 == mx[seg], np.arange(total), total), offs)
        split = mx > t_arr
        if not split.any():
            break
        m = k[first[split]]
        keep[m] = True
        i_arr = np.concatenate([i_arr[split], m])
        j_arr = np.concatenate([m, j_arr[split]])
        t_arr = np.concatenate([t_arr[split], t_arr[split]])
    return keep


def simplify_dp(pts, tol):
    """Douglas-Peucker simplification of an open or closed coordinate sequence.

    A closed sequence (first == last) is treated like GEOS treats a closed line:
    the shared start/end vertex is kept.
    """
    pts = np.asarray(pts, dtype=np.float64)
    if tol <= 0 or len(pts) < 3:
        return pts
    if len(pts) <= 256:
        keep = _dp_keep_py(pts[:, 0].tolist(), pts[:, 1].tolist(), tol * tol)
        return pts[np.array(keep)]
    keep = _dp_keep_batch(pts[:, 0].copy(), pts[:, 1].copy(), [0], [len(pts) - 1], [tol * tol])
    return pts[keep]


def simplify_dp_many(lines, tols):
    """Batch Douglas-Peucker; ``lines``: list of (n, 2) arrays, ``tols``: per line."""
    if not lines:
        return []
    lens = np.array([len(p) for p in lines], dtype=np.int64)
    starts = np.cumsum(lens) - lens
    ends = starts + lens - 1
    allp = np.vstack(lines)
    tol2 = np.asarray(tols, dtype=np.float64) ** 2
    keep = _dp_keep_batch(allp[:, 0].copy(), allp[:, 1].copy(), starts, ends, tol2)
    return [allp[s:e + 1][keep[s:e + 1]] for s, e in zip(starts, ends)]


def _open_ring(coords):
    pts = np.asarray(coords, dtype=np.float64)
    if len(pts) > 1 and pts[0, 0] == pts[-1, 0] and pts[0, 1] == pts[-1, 1]:
        pts = pts[:-1]
    return pts


def _simplify_ring(pts, tol):
    """Simplify an open ring (no closing vertex); returns an open ring."""
    closed = np.vstack([pts, pts[:1]])
    s = simplify_dp(closed, tol)
    return s[:-1]


def _close(pts):
    return np.vstack([pts, pts[:1]])


# ── Algorithms ─────────────────────────────────────────────────────────────────

def _dp_alg_tol(pts, is_ring, tol):
    """Tolerance capped at 5 % of the arc length (as in the original _alg_dp)."""
    if is_ring:
        length = float(np.hypot(*(np.roll(pts, -1, axis=0) - pts).T).sum())
    else:
        length = float(np.hypot(*np.diff(pts, axis=0).T).sum())
    return min(tol, length * 0.05) if length > 0 else tol


def alg_dp(coords, is_ring, params):
    tol = float(params.get('tolerance_m', 5.0))
    if is_ring:
        pts = _open_ring(coords)
        if len(pts) < 3:
            return np.asarray(coords, dtype=np.float64)
        s = _simplify_ring(pts, _dp_alg_tol(pts, True, tol))
        return _close(s if len(s) >= 3 else pts)
    pts = np.asarray(coords, dtype=np.float64)
    return simplify_dp(pts, _dp_alg_tol(pts, False, tol))


def alg_chaikin(coords, is_ring, params):
    pre_tol = float(params.get('pre_simplify_m', 2.0))
    iterations = int(params.get('iterations', 4))
    post_tol = float(params.get('post_simplify_m', 1.0))

    if is_ring:
        pts = _open_ring(coords)
        if len(pts) < 3:
            return np.asarray(coords, dtype=np.float64)
        # A ring that the simplifier would collapse (e.g. a one-pixel hole) is
        # smoothed unsimplified instead of being left unsmoothed (the original
        # returned it unchanged, so small patches stayed square).
        if pre_tol > 0:
            s = _simplify_ring(pts, pre_tol)
            if len(s) >= 3:
                pts = s
        arr = pts
        for _ in range(iterations):
            nxt = np.roll(arr, -1, axis=0)
            new = np.empty((len(arr) * 2, 2), dtype=np.float64)
            new[0::2] = 0.75 * arr + 0.25 * nxt
            new[1::2] = 0.25 * arr + 0.75 * nxt
            arr = new
        if post_tol > 0:
            s = _simplify_ring(arr, post_tol)
            if len(s) >= 3:
                arr = s
        return _close(arr)

    pts = np.asarray(coords, dtype=np.float64)
    if pre_tol > 0 and len(pts) > 2:
        pts = simplify_dp(pts, pre_tol)
    if len(pts) < 2:
        return np.asarray(coords, dtype=np.float64)
    arr = pts
    for _ in range(iterations):
        n = len(arr)
        p0, p1 = arr[:-1], arr[1:]
        new = np.empty((2 * n, 2), dtype=np.float64)
        new[0] = arr[0]
        new[-1] = arr[-1]
        new[1:2 * n - 1:2] = 0.75 * p0 + 0.25 * p1
        new[2:2 * n - 1:2] = 0.25 * p0 + 0.75 * p1
        arr = new
    if post_tol > 0 and len(arr) > 2:
        arr = simplify_dp(arr, post_tol)
    return arr


def _chord(pts):
    d = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    total = float(d[-1])
    return (d / total if total > 0 else d), total


def alg_bspline(coords, is_ring, params):
    try:
        from scipy import interpolate as sci_interp
    except ImportError:
        return alg_chaikin(coords, is_ring,
                           {'pre_simplify_m': params.get('pre_simplify_m', 3.0),
                            'iterations': 4, 'post_simplify_m': 0.5})

    pre_tol = float(params.get('pre_simplify_m', 3.0))
    degree = int(params.get('degree', 3))
    n_per_100m = float(params.get('n_points_per_100m', 5.0))
    smoothing = float(params.get('smoothing', 0.0))

    if is_ring:
        pts = _open_ring(coords)
        if pre_tol > 0 and len(pts) >= 3:
            pts = _simplify_ring(pts, pre_tol)
        k = min(degree, len(pts) - 1)
        if k < 1 or len(pts) < 3:
            return np.asarray(coords, dtype=np.float64)
        try:
            tck, _ = sci_interp.splprep([pts[:, 0], pts[:, 1]], s=smoothing, k=k, per=True)
        except Exception:
            return np.asarray(coords, dtype=np.float64)
        _, total = _chord(_close(pts))
        n_out = max(4, int(total / 100.0 * n_per_100m))
        x_n, y_n = sci_interp.splev(np.linspace(0, 1, n_out, endpoint=False), tck)
        return _close(np.column_stack([x_n, y_n]))

    pts = np.asarray(coords, dtype=np.float64)
    if pre_tol > 0 and len(pts) > 2:
        pts = simplify_dp(pts, pre_tol)
    if len(pts) < 3:
        return pts
    t, total = _chord(pts)
    k = min(degree, len(pts) - 1)
    try:
        tck, _ = sci_interp.splprep([pts[:, 0], pts[:, 1]], u=t, s=smoothing, k=k)
    except Exception:
        return np.asarray(coords, dtype=np.float64)
    n_out = max(4, int(total / 100.0 * n_per_100m))
    x_n, y_n = sci_interp.splev(np.linspace(0, 1, n_out), tck)
    r = np.column_stack([x_n, y_n])
    r[0] = pts[0]
    r[-1] = pts[-1]
    return r


def alg_bezier(coords, is_ring, params):
    try:
        from scipy.interpolate import CubicSpline
    except ImportError:
        return alg_chaikin(coords, is_ring,
                           {'pre_simplify_m': params.get('pre_simplify_m', 3.0),
                            'iterations': 3, 'post_simplify_m': 0.5})

    pre_tol = float(params.get('pre_simplify_m', 3.0))
    n_per_100m = float(params.get('n_points_per_100m', 5.0))

    if is_ring:
        pts = _open_ring(coords)
        if pre_tol > 0 and len(pts) >= 3:
            pts = _simplify_ring(pts, pre_tol)
        if len(pts) < 3:
            return np.asarray(coords, dtype=np.float64)
        pts_c = _close(pts)
        t, total = _chord(pts_c)
        try:
            cs_x = CubicSpline(t, pts_c[:, 0], bc_type='periodic')
            cs_y = CubicSpline(t, pts_c[:, 1], bc_type='periodic')
        except Exception:
            return np.asarray(coords, dtype=np.float64)
        n_out = max(4, int(total / 100.0 * n_per_100m))
        t_new = np.linspace(0, 1, n_out, endpoint=False)
        return _close(np.column_stack([cs_x(t_new), cs_y(t_new)]))

    pts = np.asarray(coords, dtype=np.float64)
    if pre_tol > 0 and len(pts) > 2:
        pts = simplify_dp(pts, pre_tol)
    if len(pts) < 3:
        return pts
    t, total = _chord(pts)
    try:
        cs_x = CubicSpline(t, pts[:, 0])
        cs_y = CubicSpline(t, pts[:, 1])
    except Exception:
        return np.asarray(coords, dtype=np.float64)
    n_out = max(4, int(total / 100.0 * n_per_100m))
    t_new = np.linspace(0, 1, n_out)
    r = np.column_stack([cs_x(t_new), cs_y(t_new)])
    r[0] = pts[0]
    r[-1] = pts[-1]
    return r


def alg_gaussian(coords, is_ring, params):
    """Gaussian sliding average along the arc length (not in the original).

    Like GRASS v.generalize "sliding_averaging": the arc is resampled at a
    fixed spacing and x/y are convolved with a Gaussian of ``sigma_m``.  This
    removes pixel staircases of any step length (Douglas-Peucker + Chaikin
    keeps steps whose corners deviate more than the pre-simplify tolerance).
    Open arcs use an odd reflection at both ends, so their end points (nodes)
    stay fixed.  Closed rings are rescaled about their centroid to keep their
    area (plain Gaussian smoothing shrinks closed curves).
    """
    sigma = float(params.get('sigma_m', 10.0))
    post_tol = float(params.get('post_simplify_m', 1.0))
    pts = _open_ring(coords) if is_ring else np.asarray(coords, dtype=np.float64)
    if sigma <= 0 or len(pts) < (3 if is_ring else 2):
        return np.asarray(coords, dtype=np.float64)
    seq = _close(pts) if is_ring else pts
    d = np.hypot(*np.diff(seq, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(d)])
    total = float(cum[-1])
    if total <= 0:
        return np.asarray(coords, dtype=np.float64)
    step = min(sigma / 4.0, total / (8 if is_ring else 3))
    n = max(int(np.ceil(total / step)), 8 if is_ring else 3)
    t = np.linspace(0.0, total, n, endpoint=not is_ring)
    x = np.interp(t, cum, seq[:, 0])
    y = np.interp(t, cum, seq[:, 1])
    s = sigma / (total / n)
    # the kernel may not reach past one reflection (open) / one turn (ring)
    s = min(s, (n - 1) / 3.0)
    half = max(1, int(np.ceil(3 * s)))
    kern = np.exp(-0.5 * (np.arange(-half, half + 1) / s) ** 2)
    kern /= kern.sum()
    if is_ring:
        idx = np.arange(-half, n + half) % n
        xp = x[idx]
        yp = y[idx]
    else:
        # odd reflection about the end points keeps the nodes fixed
        xp = np.concatenate([2 * x[0] - x[half:0:-1], x, 2 * x[-1] - x[-2:-half - 2:-1]])
        yp = np.concatenate([2 * y[0] - y[half:0:-1], y, 2 * y[-1] - y[-2:-half - 2:-1]])
    xs_s = np.convolve(xp, kern, mode='valid')
    ys_s = np.convolve(yp, kern, mode='valid')
    out = np.column_stack([xs_s, ys_s])
    if is_ring:
        if post_tol > 0:
            s2 = _simplify_ring(out, post_tol)
            if len(s2) >= 3:
                out = s2
        # area correction AFTER post-simplify: simplifying a small, round ring
        # cuts it inside (a one-pixel patch lost ~9 % when corrected before)
        a0 = signed_area_open(pts)
        a1 = signed_area_open(out)
        if a1 != 0 and (a0 > 0) == (a1 > 0):
            cx, cy = out.mean(axis=0)
            f = np.sqrt(a0 / a1)
            out = (out - (cx, cy)) * f + (cx, cy)
        return _close(out)
    out[0] = pts[0]
    out[-1] = pts[-1]
    if post_tol > 0 and len(out) > 2:
        out = simplify_dp(out, post_tol)
    return out


def signed_area_open(ring):
    x = ring[:, 0] - ring[0, 0]
    y = ring[:, 1] - ring[0, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)) * 0.5


def smooth_segment(coords, is_ring, algorithm, params):
    """Dispatch by algorithm name (same aliases as the original _smooth_segment)."""
    key = algorithm.lower().replace('-', '').replace(' ', '').replace('_', '')
    if key == 'douglaspeucker':
        return alg_dp(coords, is_ring, params)
    if key == 'chaikin':
        return alg_chaikin(coords, is_ring, params)
    if key in ('bspline', 'spline'):
        return alg_bspline(coords, is_ring, params)
    if key in ('bezier', 'beziercurve', 'cubicspline'):
        return alg_bezier(coords, is_ring, params)
    if key in ('gaussian', 'slidingaverage'):
        return alg_gaussian(coords, is_ring, params)
    raise ValueError('Unknown algorithm {!r}. Choose: {}'.format(algorithm, ', '.join(ALGORITHMS)))


def _validate(out, is_ring):
    out = np.asarray(out, dtype=np.float64)
    min_pts = 4 if is_ring else 2
    if out.ndim != 2 or len(out) < min_pts or not np.all(np.isfinite(out)):
        return None
    return out


def smooth_arc(coords, is_ring, algorithm, params):
    """Smooth one arc; returns float coords or None if the result is unusable.

    Mirrors the original _smooth_edge_worker: any exception or a result with
    too few points means "keep the original arc".
    """
    try:
        out = smooth_segment(coords, is_ring, algorithm, params)
    except Exception:
        return None
    return _validate(out, is_ring)


# ── Batch versions (same results as the per-arc functions) ────────────────────

def _offsets(lens):
    return np.cumsum(lens) - lens


def _chaikin_open_many(lines, iterations):
    if not lines or iterations <= 0:
        return lines
    lens = np.array([len(p) for p in lines], dtype=np.int64)
    pts = np.vstack(lines)
    for _ in range(iterations):
        offs = _offsets(lens)
        nseg = lens - 1
        seg_arc = np.repeat(np.arange(len(lens)), nseg)
        seg_j = np.arange(int(nseg.sum())) - np.repeat(_offsets(nseg), nseg)
        a_idx = offs[seg_arc] + seg_j
        p0 = pts[a_idx]
        p1 = pts[a_idx + 1]
        new_lens = 2 * lens
        new_offs = _offsets(new_lens)
        new = np.empty((int(new_lens.sum()), 2), dtype=np.float64)
        new[new_offs] = pts[offs]
        new[new_offs + new_lens - 1] = pts[offs + lens - 1]
        base = new_offs[seg_arc] + 1 + 2 * seg_j
        new[base] = 0.75 * p0 + 0.25 * p1
        new[base + 1] = 0.25 * p0 + 0.75 * p1
        pts, lens = new, new_lens
    offs = _offsets(lens)
    return [pts[o:o + n] for o, n in zip(offs, lens)]


def _chaikin_ring_many(rings, iterations):
    """Rings are open (no closing vertex)."""
    if not rings or iterations <= 0:
        return rings
    lens = np.array([len(r) for r in rings], dtype=np.int64)
    pts = np.vstack(rings)
    for _ in range(iterations):
        offs = _offsets(lens)
        idx = np.arange(len(pts))
        nxt = idx + 1
        nxt[offs + lens - 1] = offs
        arr = pts
        nb = pts[nxt]
        new = np.empty((2 * len(pts), 2), dtype=np.float64)
        new[0::2] = 0.75 * arr + 0.25 * nb
        new[1::2] = 0.25 * arr + 0.75 * nb
        pts, lens = new, 2 * lens
    offs = _offsets(lens)
    return [pts[o:o + n] for o, n in zip(offs, lens)]


def _simplify_open_many(lines, tol, cond):
    idx = [k for k, p in enumerate(lines) if cond(p)]
    if tol > 0 and idx:
        for k, r in zip(idx, simplify_dp_many([lines[k] for k in idx], [tol] * len(idx))):
            lines[k] = r


def _simplify_ring_many(rings, tols, idx):
    """In-place ring simplification (open rings) for the given indices."""
    idx = [k for k in idx if tols[k] > 0]
    if not idx:
        return
    res = simplify_dp_many([_close(rings[k]) for k in idx], [tols[k] for k in idx])
    for k, r in zip(idx, res):
        if len(r) - 1 >= 3:          # keep the ring unsimplified if it would collapse
            rings[k] = r[:-1]


def _chaikin_batch(coords_list, closed_list, params):
    pre_tol = float(params.get('pre_simplify_m', 2.0))
    iterations = int(params.get('iterations', 4))
    post_tol = float(params.get('post_simplify_m', 1.0))
    out = [None] * len(coords_list)

    oi = [i for i, c in enumerate(closed_list) if not c]
    lines = [np.asarray(coords_list[i], dtype=np.float64) for i in oi]
    _simplify_open_many(lines, pre_tol, lambda p: len(p) > 2)
    lines = _chaikin_open_many(lines, iterations)
    _simplify_open_many(lines, post_tol, lambda p: len(p) > 2)
    for k, i in enumerate(oi):
        out[i] = lines[k]

    ci = [i for i, c in enumerate(closed_list) if c]
    rings = [_open_ring(coords_list[i]) for i in ci]
    _simplify_ring_many(rings, [pre_tol] * len(rings),
                        [k for k, r in enumerate(rings) if len(r) >= 3])
    take = [k for k, r in enumerate(rings) if len(r) >= 3]
    for k, r in zip(take, _chaikin_ring_many([rings[k] for k in take], iterations)):
        rings[k] = r
    _simplify_ring_many(rings, [post_tol] * len(rings), [k for k in take if len(rings[k]) >= 3])
    take = set(take)
    for k, i in enumerate(ci):
        if k in take and len(rings[k]) >= 3:
            out[i] = _close(rings[k])
        else:
            out[i] = np.asarray(coords_list[i], dtype=np.float64)
    return out


def _dp_batch(coords_list, closed_list, params):
    tol = float(params.get('tolerance_m', 5.0))
    out = [None] * len(coords_list)
    oi = [i for i, c in enumerate(closed_list) if not c]
    lines = [np.asarray(coords_list[i], dtype=np.float64) for i in oi]
    tols = [_dp_alg_tol(p, False, tol) for p in lines]
    idx = [k for k, p in enumerate(lines) if tols[k] > 0 and len(p) >= 3]
    for k, r in zip(idx, simplify_dp_many([lines[k] for k in idx], [tols[k] for k in idx])):
        lines[k] = r
    for k, i in enumerate(oi):
        out[i] = lines[k]

    ci = [i for i, c in enumerate(closed_list) if c]
    rings = [_open_ring(coords_list[i]) for i in ci]
    ok = [len(r) >= 3 for r in rings]
    tols = [_dp_alg_tol(r, True, tol) if ok[k] else 0.0 for k, r in enumerate(rings)]
    orig = list(rings)
    _simplify_ring_many(rings, tols, [k for k in range(len(rings)) if ok[k]])
    for k, i in enumerate(ci):
        if not ok[k]:
            out[i] = np.asarray(coords_list[i], dtype=np.float64)
        else:
            out[i] = _close(rings[k] if len(rings[k]) >= 3 else orig[k])
    return out


def smooth_batch(coords_list, closed_list, algorithm, params):
    """Smooth many arcs; same results as ``[smooth_arc(...) for ...]``."""
    key = algorithm.lower().replace('-', '').replace(' ', '').replace('_', '')
    if key == 'chaikin':
        res = _chaikin_batch(coords_list, closed_list, params)
    elif key == 'douglaspeucker':
        res = _dp_batch(coords_list, closed_list, params)
    else:
        return [smooth_arc(c, r, algorithm, params) for c, r in zip(coords_list, closed_list)]
    return [None if s is None else _validate(s, r) for s, r in zip(res, closed_list)]
