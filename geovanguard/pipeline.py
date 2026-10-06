"""Topology-preserving smoothing pipeline (stages S1–S7).

Port of the original 07_BuildTopology → 08_SmoothPolygon flow:

  S1 load        polygons → work DB (global poly_id, quantised, oriented rings)
  S2a insert     junction insertion per tile         (_insert_junctions)
  S2b densify    apply insertions to each polygon
  S2c label      LPoly/RPoly arc pieces per tile     (_build_tile_topology)
  S3 merge       join pieces cut by tile edges       (collect / dedup step)
  S4 smooth      smooth every arc exactly once       (_smooth_tile_worker)
  S5 reconstruct rebuild each polygon from its complete arc set
                 (bucket-by-poly_id reconstruct, _reconstruct_one_proc)
  S6 repair      revert arcs of invalid polygons and rebuild their neighbours
  S7 output      handled by the caller via iter_results()/iter_arcs()

Each stage commits progress to the work DB, so re-running with the same work
folder resumes; changing only smoothing parameters re-uses S1–S3.
"""
import json
import math
import time

import numpy as np

from . import parallel, stages, workers
from .backends import get_backend
from .core import geom, topology
from .progress import PIPELINE_STEPS, StepTracker, fmt_duration
from .core.store import (id_list, ARC_FALLBACK, ARC_FALLBACK2, ARC_ORIGINAL, ARC_PENDING,
                         ARC_REVERTED, ARC_SMOOTHED,
                         RES_INVALID, RES_OK, RES_ORIGINAL, RES_WALK_FAILED, WorkStore)

_USES_SMOOTH_BLOB = stages.USES_SMOOTH_BLOB
_REPAIR_WINDOWS = stages.REPAIR_WINDOWS
_shift = stages.shift
# bump when the algorithm changes so work folders of older versions recompute
ENGINE_VERSION = 5

TARGET_VERTICES_PER_TILE = 1000000
RECON_BATCH = 2000
SMOOTH_BATCH = 2000
LOAD_BATCH = 5000
DENSIFY_BATCH = 500



class Canceled(Exception):
    pass








class SmoothPipeline(object):
    """Drives the stages on a WorkStore.

    ``params`` keys: algorithm, alg_params (dict), fixed_mode, tile_size (map
    units, 0 = auto), precision (grid size), tol (map units), origin (x, y),
    extent_q (for fixed_mode 'extent').
    """

    def __init__(self, work_dir, feedback, tracker=None, backend=None, workers=0, pause=None):
        """``workers``: worker processes (0 = automatic, 1 = main process only);
        ``pause``: optional PauseControl (pause / resume at the next checkpoint)."""
        self.pause = pause
        self.backend = get_backend(backend)
        self.workers = workers
        self.pool = None
        self._run_complete = False
        self.store = WorkStore(work_dir)
        self.fb = feedback
        self.tracker = tracker or StepTracker(feedback, PIPELINE_STEPS)
        self.stats = {}

    # ── helpers ──────────────────────────────────────────────────────────────
    def close(self):
        if self.pool is not None:
            # a canceled / failed run must not wait for the tasks still running
            # in the workers (a large tile can take a minute); finished work is
            # already committed, workers never write
            self.pool.close(abort=not self._run_complete)
            self.pool = None
        self.store.close()

    def _check(self):
        if self.fb.isCanceled():
            self.store.con.commit()
            raise Canceled()
        if self.pause is not None and self.pause.is_paused():
            self._wait_paused()

    def _wait_paused(self):
        """Hold here (workers frozen, CPU free) until resumed or canceled."""
        self.store.con.commit()
        if self.pool is not None:
            self.pool.suspend()
        t0 = time.time()
        self._log('⏸ Paused — CPU released; press Resume to continue (Cancel still works).')
        self.tracker.note('⏸ Paused — press Resume to continue')
        try:
            while self.pause.is_paused():
                if self.fb.isCanceled():
                    raise Canceled()
                time.sleep(0.3)
        finally:
            if self.pool is not None and not self.fb.isCanceled():
                self.pool.resume()
        paused = time.time() - t0
        self.tracker.shift(paused)
        self._log('▶ Resumed after {}.'.format(fmt_duration(paused)))

    def _progress(self, stage, frac, detail=''):
        self.tracker.update(stage, frac, detail)

    def _log(self, msg):
        self.fb.pushInfo(msg)

    @property
    def con(self):
        return self.store.con

    # ── configuration / resume ───────────────────────────────────────────────
    def configure(self, topo_sig, smooth_sig, origin, grid_size, tol, fixed_mode,
                  tile_size, extent_q=None):
        st = self.store
        topo_sig = dict(topo_sig, engine=ENGINE_VERSION)
        smooth_sig = dict(smooth_sig, engine=ENGINE_VERSION)
        old_topo = st.get('topo_sig')
        if old_topo is not None and old_topo != topo_sig:
            self._log('Work folder holds a different input/topology setup — rebuilding.')
        if old_topo != topo_sig:
            st.reset_from('load')
            st.set('topo_sig', topo_sig)
            st.set('origin', list(origin))
            st.set('grid_size', grid_size)
            st.set('tol_q', int(math.ceil(tol / grid_size)) if tol > 0 else 0)
            st.set('fixed_mode', fixed_mode)
            st.set('tile_size', tile_size)
            st.set('extent_q', list(extent_q) if extent_q else None)
        elif st.done('load'):
            self._log('Resuming from work folder: {}'.format(st.folder))
        old_smooth = st.get('smooth_sig')
        if old_smooth != smooth_sig:
            if old_smooth is not None and st.done('merge'):
                self._log('Smoothing parameters changed — re-using topology, re-smoothing arcs.')
            st.reset_from('smooth')
            st.set('smooth_sig', smooth_sig)
        st.con.commit()
        self.origin = tuple(st.get('origin'))
        self.g = float(st.get('grid_size'))
        self.tol_q = int(st.get('tol_q'))
        self.fixed_mode = st.get('fixed_mode')
        ext = st.get('extent_q')
        self.extent_q = tuple(ext) if ext else None
        self.algorithm = smooth_sig['algorithm']
        self.alg_params = smooth_sig['params']
        self.smoother = stages.ArcSmoother(self.algorithm, self.alg_params, self.backend)

    def _start_pool(self, n_hint):
        if self.workers == 1 or (self.workers <= 0 and n_hint < parallel.PARALLEL_MIN_POLYGONS):
            n, note = 1, ('1 process (small job)' if self.workers <= 0 else
                          '1 process (set by user)')
        else:
            n, note = parallel.resolve_workers(self.workers)
        self.pool = parallel.Pool(n, self.backend, self._log)
        if self.pool.n != n:
            note = '1 process (worker processes unavailable)'
        self._log('Parallel processing: ' + note)
        self.stats['workers'] = self.pool.n

    def run(self, records, n_hint):
        t0 = time.time()
        self._start_pool(n_hint)
        self._stage('load', self.load, records, n_hint)
        self.grid = topology.TileGrid.from_dict(self.store.get('grid'))
        self._stage('insert', self.insert_junctions)
        self._stage('densify', self.densify)
        self._stage('label', self.label)
        self._stage('merge', self.merge)
        self._stage('smooth', self.smooth)
        self._stage('reconstruct', self.reconstruct)
        self._stage('repair', self.repair)
        self.stats['seconds'] = round(time.time() - t0, 1)
        self._run_complete = True
        return self.summary()

    def _stage(self, name, fn, *args):
        """Run one stage with step progress and a summary line in the log."""
        if self.store.done(name):
            self.tracker.done(name, self._stage_summary(name), resumed=True)
            return
        self.tracker.start(name)
        fn(*args)
        self.tracker.done(name, self._stage_summary(name))

    def _count(self, sql, args=()):
        return self.con.execute(sql, args).fetchone()[0] or 0

    def _stage_summary(self, name):
        c = self._count
        if name == 'load':
            grid = self.store.get('grid') or {}
            skipped = self.store.get('skipped_input', 0)
            return '{:,} polygons, {:,} vertices, {} tile(s) ({}×{}){}'.format(
                c('SELECT COUNT(*) FROM polys'), self.store.get('n_vertices', 0),
                grid.get('nx', 1) * grid.get('ny', 1), grid.get('nx', 1), grid.get('ny', 1),
                ', {:,} empty/unsupported geometries skipped'.format(skipped) if skipped else '')
        if name == 'insert':
            return '{:,} tile(s), {:,} junction vertices inserted'.format(
                c('SELECT COUNT(*) FROM tiles'), c('SELECT COUNT(*) FROM ins'))
        if name == 'densify':
            return '{:,} polygon(s) received junction vertices'.format(
                c('SELECT COUNT(*) FROM polys WHERE dens=1'))
        if name == 'label':
            dup = self.store.get('duplicate_edges', 0)
            return '{:,} tile(s), {:,} arc pieces{}'.format(
                c('SELECT COUNT(*) FROM tiles'), self.store.get('n_pieces', 0),
                ', {:,} duplicated edges (overlaps!)'.format(dup) if dup else '')
        if name == 'merge':
            n = c('SELECT COUNT(*) FROM arcs')
            sh = c('SELECT COUNT(*) FROM arcs WHERE rpoly<>0')
            return '{:,} arcs ({:,} shared LPoly/RPoly, {:,} outer)'.format(n, sh, n - sh)
        if name == 'smooth':
            return '{:,} arcs smoothed, {:,} left unchanged (too short / fixed)'.format(
                c('SELECT COUNT(*) FROM arcs WHERE state=?', (ARC_SMOOTHED,)),
                c('SELECT COUNT(*) FROM arcs WHERE state=?', (ARC_ORIGINAL,)))
        if name == 'reconstruct':
            return '{:,} valid polygons, {:,} need repair'.format(
                c('SELECT COUNT(*) FROM result WHERE status=?', (RES_OK,)),
                c('SELECT COUNT(*) FROM result WHERE status IN (?,?)',
                  (RES_INVALID, RES_WALK_FAILED)))
        if name == 'repair':
            return ('{:,} arcs re-smoothed more gently, {:,} reverted, '
                    '{:,} polygon(s) kept original geometry').format(
                c('SELECT COUNT(*) FROM arcs WHERE state IN (?,?)',
                  (ARC_FALLBACK, ARC_FALLBACK2)),
                c('SELECT COUNT(*) FROM arcs WHERE state=?', (ARC_REVERTED,)),
                c('SELECT COUNT(*) FROM result WHERE status=?', (RES_ORIGINAL,)))
        return ''

    # ── S1 load ──────────────────────────────────────────────────────────────
    def load(self, records, n_hint):
        st = self.store
        if st.done('load'):
            return
        st.reset_from('load')
        origin = tuple(st.get('origin'))
        g = float(st.get('grid_size'))

        def batches():
            chunk = []
            for rec in records():
                chunk.append(rec)
                if len(chunk) >= LOAD_BATCH:
                    yield chunk, origin, g
                    chunk = []
            if chunk:
                yield chunk, origin, g

        skipped = n_vert = done = 0
        qmin = [None, None]
        qmax = [None, None]
        for rows, sk, nv, bb in self.pool.imap(workers.load, batches(), poll=self._check):
            self._check()
            skipped += sk
            n_vert += nv
            done += len(rows) + sk
            if bb is not None:
                qmin[0] = bb[0] if qmin[0] is None else min(qmin[0], bb[0])
                qmin[1] = bb[1] if qmin[1] is None else min(qmin[1], bb[1])
                qmax[0] = bb[2] if qmax[0] is None else max(qmax[0], bb[2])
                qmax[1] = bb[3] if qmax[1] is None else max(qmax[1], bb[3])
            if rows:
                st.insert_polys(rows)
            self._progress('load', done / float(max(n_hint, 1)),
                           '{:,} / {:,} polygons'.format(done, n_hint))
        n_poly = st.poly_count()
        if n_poly == 0:
            st.con.commit()
            raise ValueError('No polygon could be read from the input.')

        tile_size = float(st.get('tile_size') or 0)
        width = qmax[0] - qmin[0] + 1
        height = qmax[1] - qmin[1] + 1
        if tile_size > 0:
            tile_q = int(math.ceil(tile_size / g))
        else:
            n_tiles = max(1, int(math.ceil(n_vert / float(TARGET_VERTICES_PER_TILE))))
            tile_q = int(math.ceil(math.sqrt(width * float(height) / n_tiles)))
            tile_q = max(tile_q, 1)
            if n_tiles == 1:
                tile_q = max(width, height) + 1
        grid = topology.TileGrid.for_extent(qmin[0], qmin[1], qmax[0], qmax[1], tile_q)
        st.set('grid', grid.to_dict())
        st.set('n_vertices', n_vert)
        st.con.executemany('INSERT OR IGNORE INTO tiles(tile) VALUES (?)',
                           ((t,) for t in range(grid.n_tiles)))
        st.set('skipped_input', skipped)
        st.mark_done('load')

    # ── tile context ─────────────────────────────────────────────────────────
    def _context(self, tile, pad=0):
        """``[(poly_id, geom_blob)]`` of the polygons touching the (padded) tile."""
        xmin, ymin, xmax, ymax = self.grid.tile_bbox(tile)
        ids = self.store.query_bbox(xmin - pad, ymin - pad, xmax + pad, ymax + pad)
        if not ids:
            return []
        return list(self.store.fetch_geoms(ids))

    _PENDING_SQL = {
        'ins_done': 'SELECT tile FROM tiles WHERE ins_done=0 ORDER BY tile',
        'lab_done': 'SELECT tile FROM tiles WHERE lab_done=0 ORDER BY tile',
    }

    def _pending_tiles(self, column):
        return [r[0] for r in self.con.execute(self._PENDING_SQL[column])]

    # ── S2a junction insertion ───────────────────────────────────────────────
    def insert_junctions(self):
        if self.store.done('insert'):
            return
        pending = self._pending_tiles('ins_done')
        total = self.grid.n_tiles
        done0 = total - len(pending)
        tasks = ((self._context(t, self.tol_q), t, self.grid, self.tol_q) for t in pending)
        results = self.pool.imap(workers.insert, tasks, poll=self._check)
        for k, (t, ins) in enumerate(zip(pending, results)):
            self._check()
            if ins:
                self.con.executemany(
                    'INSERT INTO ins(poly_id, ring, seg, t, x, y) VALUES (?,?,?,?,?,?)', ins)
            self.con.execute('UPDATE tiles SET ins_done=1 WHERE tile=?', (t,))
            self.con.commit()
            self._progress('insert', (done0 + k + 1) / float(total),
                           'tile {:,} / {:,}'.format(done0 + k + 1, total))
        self.store.mark_done('insert')

    # ── S2b densify ──────────────────────────────────────────────────────────
    def densify(self):
        st = self.store
        if st.done('densify'):
            return
        pids = [r[0] for r in self.con.execute(
            'SELECT DISTINCT i.poly_id FROM ins i JOIN polys p ON p.poly_id = i.poly_id '
            'WHERE p.dens = 0 ORDER BY i.poly_id')]
        def tasks():
            for k in range(0, len(pids), DENSIFY_BATCH):
                batch = pids[k:k + DENSIFY_BATCH]
                ins = {}
                for pid, ring, seg, t, x, y in self.con.execute(
                        'SELECT poly_id, ring, seg, t, x, y FROM ins '
                        'WHERE poly_id IN (SELECT value FROM json_each(?)) '
                        'ORDER BY poly_id, rowid', (id_list(batch),)):
                    ins.setdefault(pid, {}).setdefault(ring, []).append((seg, t, x, y))
                yield ([(pid, blob, ins.get(pid, {})) for pid, blob in st.fetch_geoms(batch)],)

        done = 0
        for res in self.pool.imap(workers.densify, tasks(), poll=self._check):
            self._check()
            for blob, bb, pid in res:
                self.con.execute('UPDATE polys SET geom=?, dens=1, minx=?, miny=?, maxx=?, maxy=? '
                                 'WHERE poly_id=?', (blob,) + tuple(bb) + (pid,))
                if st.has_rtree:
                    self.con.execute('UPDATE polys_rt SET minx=?, maxx=?, miny=?, maxy=? WHERE id=?',
                                     (bb[0], bb[2], bb[1], bb[3], pid))
            self.con.commit()
            done += len(res)
            self._progress('densify', done / float(max(len(pids), 1)),
                           '{:,} / {:,} polygons'.format(done, len(pids)))
        st.mark_done('densify')

    # ── S2c LPoly/RPoly labelling ────────────────────────────────────────────
    def label(self):
        st = self.store
        if st.done('label'):
            return
        pending = self._pending_tiles('lab_done')
        total = self.grid.n_tiles
        done0 = total - len(pending)
        dup = st.get('duplicate_edges', 0)
        tasks = ((self._context(t), t, self.grid, self.fixed_mode, self.extent_q) for t in pending)
        results = self.pool.imap(workers.label, tasks, poll=self._check)
        for k, (t, (rows, n_dup)) in enumerate(zip(pending, results)):
            self._check()
            dup += n_dup
            if rows:
                self.con.executemany(
                    'INSERT INTO pieces(tile, lpoly, rpoly, fixed, ring, start, end_, nseg, closed, '
                    'cut_s, cut_e, coords) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', rows)
            self.con.execute('UPDATE tiles SET lab_done=1 WHERE tile=?', (t,))
            st.set('duplicate_edges', dup)
            self.con.commit()
            self._progress('label', (done0 + k + 1) / float(total),
                           'tile {:,} / {:,}'.format(done0 + k + 1, total))
        st.set('n_pieces', self._count('SELECT COUNT(*) FROM pieces'))
        st.mark_done('label')

    # ── S3 merge pieces into arcs ────────────────────────────────────────────
    def merge(self):
        st = self.store
        if st.done('merge'):
            return
        self.con.execute('DELETE FROM arcs')
        self.con.execute(
            'INSERT INTO arcs(lpoly, rpoly, fixed, closed, coords, smooth, state) '
            'SELECT lpoly, rpoly, fixed, closed, coords, NULL, '
            'CASE WHEN fixed=1 THEN ? ELSE ? END FROM pieces '
            'WHERE cut_s=0 AND cut_e=0 ORDER BY piece_id', (ARC_ORIGINAL, ARC_PENDING))
        metas = self.con.execute(
            'SELECT piece_id, lpoly, rpoly, fixed, ring, start, end_, nseg, cut_s, cut_e '
            'FROM pieces WHERE cut_s=1 OR cut_e=1').fetchall()
        chains = topology.merge_pieces(metas)
        self._progress('merge', 0.3, '{:,} chains'.format(len(chains)))
        for k, (chain, closed) in enumerate(chains):
            if k % 2000 == 0:
                self._check()
            rows = dict((r[0], r[1:]) for r in self.con.execute(
                'SELECT piece_id, lpoly, rpoly, fixed, coords FROM pieces '
                'WHERE piece_id IN (SELECT value FROM json_each(?))', (id_list(chain),)))
            parts = [geom.decode_coords(rows[p][3]) for p in chain]
            coords = topology.join_coords(parts)
            if closed:
                coords = topology.canonical_closed(coords)
            lp, rp, fx = rows[chain[0]][:3]
            self.con.execute(
                'INSERT INTO arcs(lpoly, rpoly, fixed, closed, coords, smooth, state) '
                'VALUES (?,?,?,?,?,NULL,?)',
                (lp, rp, fx, int(closed), geom.encode_coords(coords),
                 ARC_ORIGINAL if fx else ARC_PENDING))
        self.con.execute('DELETE FROM pieces')
        st.create_arc_indexes()
        st.mark_done('merge')
        self._progress('merge', 1.0)

    # ── S4 smooth ────────────────────────────────────────────────────────────
    def smooth(self):
        st = self.store
        if st.done('smooth'):
            return
        total = self.con.execute('SELECT COUNT(*) FROM arcs').fetchone()[0]
        params = tuple(sorted(self.alg_params.items()))

        def tasks():
            last_id = 0
            while True:
                rows = self.con.execute(
                    'SELECT arc_id, closed, coords FROM arcs WHERE state=? AND arc_id > ? '
                    'ORDER BY arc_id LIMIT ?', (ARC_PENDING, last_id, SMOOTH_BATCH)).fetchall()
                if not rows:
                    return
                last_id = rows[-1][0]
                yield rows, self.g, self.algorithm, params

        n_done = 0
        for upd in self.pool.imap(workers.smooth, tasks(), poll=self._check):
            self._check()
            self.con.executemany('UPDATE arcs SET smooth=?, state=? WHERE arc_id=?', upd)
            self.con.commit()
            n_done += len(upd)
            self._progress('smooth', n_done / float(max(total, 1)),
                           '{:,} / {:,} arcs'.format(n_done, total))
        st.mark_done('smooth')

    # ── S5 reconstruct ───────────────────────────────────────────────────────
    _ARCS_SQL = {
        'range': ('SELECT arc_id, lpoly, rpoly, coords, smooth, state FROM arcs '
                  'WHERE lpoly >= ? AND lpoly < ? ORDER BY lpoly, arc_id',
                  'SELECT arc_id, lpoly, rpoly, coords, smooth, state FROM arcs '
                  'WHERE rpoly >= ? AND rpoly < ? ORDER BY rpoly, arc_id'),
        'ids': ('SELECT arc_id, lpoly, rpoly, coords, smooth, state FROM arcs '
                'WHERE lpoly IN (SELECT value FROM json_each(?)) ORDER BY lpoly, arc_id',
                'SELECT arc_id, lpoly, rpoly, coords, smooth, state FROM arcs '
                'WHERE rpoly IN (SELECT value FROM json_each(?)) ORDER BY rpoly, arc_id'),
    }

    def _arcs_for(self, kind, args):
        """Arcs whose lpoly or rpoly is in a poly_id range (``kind`` 'range',
        args (lo, hi)) or in a list (``kind`` 'ids', args (id_list,)); each arc
        once (an arc whose two sides both match is returned by both queries)."""
        rows = {}
        for sql in self._ARCS_SQL[kind]:
            for r in self.con.execute(sql, args):
                rows[r[0]] = r[1:]
        return list(rows.values())

    def reconstruct(self):
        st = self.store
        if st.done('reconstruct'):
            return
        max_pid = self.con.execute('SELECT MAX(poly_id) FROM polys').fetchone()[0] or 0
        upto = int(st.get('recon_upto', 0))

        def tasks():
            for a in range(upto + 1, max_pid + 1, RECON_BATCH):
                b = a + RECON_BATCH
                rows = self._arcs_for('range', (a, b))
                pids = [r[0] for r in self.con.execute(
                    'SELECT poly_id FROM polys WHERE poly_id >= ? AND poly_id < ?', (a, b))]
                yield rows, pids, self.g, a, b, self.origin

        for a, res in zip(range(upto + 1, max_pid + 1, RECON_BATCH),
                          self.pool.imap(workers.rebuild, tasks(), poll=self._check)):
            self._check()
            b = a + RECON_BATCH
            self.con.executemany('INSERT OR REPLACE INTO result(poly_id, status, wkb) VALUES (?,?,?)',
                                 res)
            st.set('recon_upto', b - 1)
            self.con.commit()
            self._progress('reconstruct', (b - 1) / float(max(max_pid, 1)),
                           'polygons {:,} / {:,}'.format(min(b - 1, max_pid), max_pid))
        st.mark_done('reconstruct')

    # ── S6 repair ────────────────────────────────────────────────────────────
    def _rebuild_ids(self, pids):
        pids = sorted(set(pids))
        wanted = set(pids)
        for k in range(0, len(pids), 400):
            part = pids[k:k + 400]
            rows = self._arcs_for('ids', (id_list(part),))
            by_pid = stages.group_arcs(rows, self.g, wanted=wanted)
            res = stages.rebuild(part, by_pid, self.origin, self.backend)
            self.con.executemany('INSERT OR REPLACE INTO result(poly_id, status, wkb) VALUES (?,?,?)',
                                 res)

    def _repair_candidates(self, pid):
        """Smoothed arcs of an invalid polygon that should be stepped back.

        Returns ``[(arc_row, conflict_points), ...]``: only arcs whose smoothed
        geometry crosses or touches another arc of the same polygon (anywhere
        except at a shared node), with the points where that happens, so the
        repair can fix just those places.  If no such pair is found (e.g. the
        walk failed), every smoothed arc is returned without points, meaning
        "step the whole arc down".
        """
        rows = self.con.execute(
            'SELECT arc_id, lpoly, rpoly, state, closed, coords, smooth FROM arcs '
            'WHERE lpoly=? OR rpoly=?', (pid, pid)).fetchall()
        smoothed = [r for r in rows if r[3] in _USES_SMOOTH_BLOB]
        if not smoothed:
            return []
        g = self.g
        lines = []
        ends = []
        for r in rows:
            if r[3] in _USES_SMOOTH_BLOB and r[6] is not None:
                xy = geom.decode_coords(r[6], np.float64)
            else:
                xy = geom.decode_coords(r[5]).astype(np.float64) * g
            lines.append(xy)
            ends.append({(float(xy[0, 0]), float(xy[0, 1])), (float(xy[-1, 0]), float(xy[-1, 1]))})
        tol = 10 * g
        hit = {}
        # each pair once, the larger arc prepared (see GeometryBackend.intersections)
        for k, j, inter in self.backend.intersections(lines):
            common = ends[k] & ends[j]
            pts = [(x, y) for x, y in inter
                   if not any(abs(x - cx) <= tol and abs(y - cy) <= tol for cx, cy in common)]
            if pts:
                hit.setdefault(k, []).extend(pts)
                hit.setdefault(j, []).extend(pts)
        picked = [(rows[k][:6], pts) for k, pts in hit.items() if rows[k][3] in _USES_SMOOTH_BLOB]
        if not picked:
            # no crossing pair found (e.g. the walk failed): whole arcs step down
            picked = [(r[:6], []) for r in smoothed]
        return picked

    def _repair_arc(self, arc_id, c, closed, state, pts):
        """Fix one conflicting arc; returns (state, coords or None).

        With conflict points, only windows around them (accumulated over the
        repair rounds and growing each time the same arc conflicts again) are
        smoothed gently; the rest of the arc keeps the full smoothing, so one
        conflict on a 30 km outer ring does not de-smooth all of it.  Without
        points, or once the windows are exhausted, the whole arc steps down
        the ladder of ArcSmoother._step_down().
        """
        row = self.con.execute('SELECT level, points FROM repair WHERE arc_id=?',
                               (arc_id,)).fetchone()
        level, old_pts = (row[0], json.loads(row[1])) if row else (0, [])
        all_pts = old_pts + [list(p) for p in pts]
        level += 1
        self.con.execute('INSERT OR REPLACE INTO repair(arc_id, level, points) VALUES (?,?,?)',
                         (arc_id, level, json.dumps(all_pts)))
        if (pts and state == ARC_SMOOTHED and len(c) >= 40
                and level <= len(_REPAIR_WINDOWS)):
            s = self.smoother._smooth_windowed(c, closed, all_pts, _REPAIR_WINDOWS[level - 1])
            if s is not None:
                return ARC_SMOOTHED, s
        return self.smoother._step_down(c, closed, state)

    def repair(self):
        st = self.store
        if st.done('repair'):
            return
        bad_status = (RES_INVALID, RES_WALK_FAILED)
        rnd = 0
        # Terminates: every round moves at least one arc one step down the
        # ladder of ArcSmoother._step_down(), whose last step is the original geometry.
        while True:
            self._check()
            bad = [r[0] for r in self.con.execute(
                'SELECT poly_id FROM result WHERE status IN (?,?)', bad_status)]
            if not bad:
                break
            touched = {}
            points = {}
            for pid in bad:
                for row, pts in self._repair_candidates(pid):
                    touched[row[0]] = row
                    points.setdefault(row[0], []).extend(pts)
            touched = list(touched.values())
            if not touched:
                break
            updates = []
            n_local = n_soft = 0
            for arc_id, _, _, state, closed, cblob in touched:
                c = geom.decode_coords(cblob).astype(np.float64) * self.g
                new_state, s = self._repair_arc(arc_id, c, bool(closed), state,
                                                points.get(arc_id, []))
                if s is None:
                    updates.append((None, ARC_REVERTED, arc_id))
                    continue
                updates.append((geom.encode_coords(s, np.float64), new_state, arc_id))
                if new_state == ARC_SMOOTHED:
                    n_local += 1
                else:
                    n_soft += 1
            self._log('S6 repair round {}: {:,} invalid polygon(s); {:,} arc(s) fixed locally, '
                      '{:,} re-smoothed more gently, {:,} reverted to original'.format(
                          rnd + 1, len(bad), n_local, n_soft, len(touched) - n_local - n_soft))
            self.con.executemany('UPDATE arcs SET smooth=?, state=? WHERE arc_id=?', updates)
            affected = set(bad)
            for _, lp, rp, _, _, _ in touched:
                affected.add(lp)
                if rp:
                    affected.add(rp)
            self._rebuild_ids(affected)
            self.con.commit()
            rnd += 1
            self._progress('repair', 1.0 - 1.0 / (rnd + 1), 'round {}'.format(rnd))

        # Last resort: original geometry (its arcs are all reverted by now, so
        # neighbours were rebuilt with the same, unsmoothed shared boundary).
        bad = [r[0] for r in self.con.execute(
            'SELECT poly_id FROM result WHERE status IN (?,?)', bad_status)]
        if bad:
            ox, oy = self.origin
            for pid, blob in st.fetch_geoms(bad):
                rings = geom.decode_rings(blob)
                pairs = [(r, r.astype(np.float64) * self.g) for r in rings]
                polys = geom.assemble_polygons(pairs) or []
                shifted = [(_shift(s, ox, oy), [_shift(h, ox, oy) for h in holes])
                           for s, holes in polys]
                wkb = None
                if shifted:
                    wkb = (geom.polygon_wkb(*shifted[0]) if len(shifted) == 1
                           else geom.multipolygon_wkb(shifted))
                self.con.execute('UPDATE result SET status=?, wkb=? WHERE poly_id=?',
                                 (RES_ORIGINAL, wkb, pid))
            self._log('  {:,} polygon(s) kept their original geometry'.format(len(bad)))
        st.mark_done('repair')
        self._progress('repair', 1.0)

    # ── S7 output iterators ──────────────────────────────────────────────────
    def iter_results(self):
        """Yield (poly_id, src_fid, attr, status, wkb) ordered by poly_id."""
        cur = self.con.execute(
            'SELECT r.poly_id, p.src_fid, p.attr, r.status, r.wkb FROM result r '
            'JOIN polys p ON p.poly_id = r.poly_id ORDER BY r.poly_id')
        for row in cur:
            yield row

    def iter_arcs(self):
        """Yield (arc_id, lpoly, rpoly, state, wkb) with absolute coordinates."""
        ox, oy = self.origin
        g = self.g
        cur = self.con.execute('SELECT arc_id, lpoly, rpoly, state, coords, smooth FROM arcs '
                               'ORDER BY arc_id')
        for arc_id, lp, rp, state, cblob, sblob in cur:
            if state in _USES_SMOOTH_BLOB and sblob is not None:
                xy = geom.decode_coords(sblob, np.float64)
            else:
                xy = geom.decode_coords(cblob).astype(np.float64) * g
            yield arc_id, lp, rp, state, geom.linestring_wkb(_shift(xy, ox, oy))

    def summary(self):
        c = self.con
        s = dict(self.stats)
        s['polygons'] = c.execute('SELECT COUNT(*) FROM polys').fetchone()[0]
        s['arcs'] = c.execute('SELECT COUNT(*) FROM arcs').fetchone()[0]
        s['shared_arcs'] = c.execute('SELECT COUNT(*) FROM arcs WHERE rpoly<>0').fetchone()[0]
        for name, code in (('arcs_smoothed', ARC_SMOOTHED), ('arcs_unchanged', ARC_ORIGINAL),
                           ('arcs_reverted', ARC_REVERTED), ('arcs_fallback', ARC_FALLBACK),
                           ('arcs_fallback2', ARC_FALLBACK2)):
            s[name] = c.execute('SELECT COUNT(*) FROM arcs WHERE state=?', (code,)).fetchone()[0]
        s['polygons_original'] = c.execute('SELECT COUNT(*) FROM result WHERE status=?',
                                           (RES_ORIGINAL,)).fetchone()[0]
        s['duplicate_edges'] = self.store.get('duplicate_edges', 0)
        s['skipped_input'] = self.store.get('skipped_input', 0)
        s['backend'] = '{} (GEOS {})'.format(self.backend.name, self.backend.geos_version())
        return s

