"""Disk-backed work database (replaces the original GeoParquet files and
pickle checkpoints).

Uses only the standard-library ``sqlite3`` module.  Every pipeline stage
commits its own progress (per tile / per batch), so an interrupted run resumes
where it stopped when the same work folder is reused — the equivalent of the
original ``.checkpoints/<stem>/topology`` and ``.../smooth`` folders.

Tables
  meta    key/value (JSON) — parameters, grid, stage markers
  polys   poly_id, src_fid, attr, bbox, quantised rings blob
  polys_rt  R*Tree on polys bbox (falls back to plain bbox columns)
  tiles   per-tile progress flags
  ins     junction insertions (poly_id, ring, seg, t, x, y)
  pieces  arc pieces per tile (before joining across tile edges)
  arcs    arc_id, lpoly_id, rpoly_id, fixed, closed, coords, smooth, state
  result  reconstructed polygon per poly_id
"""
import json
import os
import sqlite3


def id_list(ids):
    """One SQL parameter holding a list of integer ids, for
    ``… IN (SELECT value FROM json_each(?))`` — the SQL text stays constant."""
    return json.dumps([int(i) for i in ids])

ARC_PENDING = 0      # not smoothed yet
ARC_SMOOTHED = 1     # smooth blob holds the smoothed geometry
ARC_ORIGINAL = 2     # kept original geometry (fixed arc or smoothing unusable)
ARC_REVERTED = 3     # smoothed, then reverted by the repair stage
ARC_FALLBACK = 4     # conflicted; smooth blob holds Chaikin with half pre-simplify
ARC_FALLBACK2 = 5    # conflicted again; smooth blob holds Chaikin corner cutting only

RES_OK = 1           # reconstructed from arcs, valid
RES_ORIGINAL = 2     # fallback: original geometry
RES_INVALID = 0      # reconstructed but invalid (repair pending)
RES_WALK_FAILED = -1  # arcs did not close into rings (repair pending)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS polys (
    poly_id INTEGER PRIMARY KEY, src_fid INTEGER, attr,
    minx INTEGER, miny INTEGER, maxx INTEGER, maxy INTEGER,
    geom BLOB, dens INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tiles (
    tile INTEGER PRIMARY KEY, ins_done INTEGER DEFAULT 0, lab_done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ins (
    poly_id INTEGER, ring INTEGER, seg INTEGER, t REAL, x INTEGER, y INTEGER);
CREATE INDEX IF NOT EXISTS ins_poly ON ins(poly_id);
CREATE TABLE IF NOT EXISTS pieces (
    piece_id INTEGER PRIMARY KEY, tile INTEGER, lpoly INTEGER, rpoly INTEGER,
    fixed INTEGER, ring INTEGER, start INTEGER, end_ INTEGER, nseg INTEGER,
    closed INTEGER, cut_s INTEGER, cut_e INTEGER, coords BLOB);
CREATE TABLE IF NOT EXISTS arcs (
    arc_id INTEGER PRIMARY KEY, lpoly INTEGER, rpoly INTEGER, fixed INTEGER,
    closed INTEGER, coords BLOB, smooth BLOB, state INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS result (
    poly_id INTEGER PRIMARY KEY, status INTEGER, wkb BLOB);
CREATE TABLE IF NOT EXISTS repair (
    arc_id INTEGER PRIMARY KEY, level INTEGER, points TEXT);
"""

_ARC_INDEXES = """
CREATE INDEX IF NOT EXISTS arcs_l ON arcs(lpoly);
CREATE INDEX IF NOT EXISTS arcs_r ON arcs(rpoly);
CREATE INDEX IF NOT EXISTS arcs_state ON arcs(state);
"""

DB_NAME = 'topology_work.sqlite'


class WorkStore(object):

    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, DB_NAME)
        self.con = sqlite3.connect(self.path)
        self.con.execute('PRAGMA journal_mode=WAL')
        self.con.execute('PRAGMA synchronous=NORMAL')
        self.con.execute('PRAGMA temp_store=FILE')
        self.con.execute('PRAGMA cache_size=-200000')     # ~200 MB page cache
        self.con.executescript(_SCHEMA)
        self.has_rtree = self._init_rtree()
        self.con.commit()

    def _init_rtree(self):
        try:
            self.con.execute('CREATE VIRTUAL TABLE IF NOT EXISTS polys_rt '
                             'USING rtree(id, minx, maxx, miny, maxy)')
            return True
        except sqlite3.OperationalError:
            self.con.execute('CREATE INDEX IF NOT EXISTS polys_bbox ON polys(minx, maxx)')
            return False

    def close(self):
        try:
            self.con.commit()
            self.con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.Error:
            pass
        self.con.close()

    # ── meta ────────────────────────────────────────────────────────────────
    def get(self, key, default=None):
        row = self.con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.con.execute('INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)',
                         (key, json.dumps(value)))

    def done(self, stage):
        return bool(self.get('done:' + stage, False))

    def mark_done(self, stage):
        self.set('done:' + stage, True)
        self.con.commit()

    def reset_from(self, stage):
        """Drop results of ``stage`` and every later stage."""
        order = ['load', 'insert', 'densify', 'label', 'merge', 'smooth',
                 'reconstruct', 'repair']
        later = order[order.index(stage):]
        for st in later:
            self.con.execute('DELETE FROM meta WHERE key=?', ('done:' + st,))
        if 'load' in later:
            self.con.execute('DELETE FROM polys')
            if self.has_rtree:
                self.con.execute('DELETE FROM polys_rt')
            self.con.execute('DELETE FROM tiles')
        if 'insert' in later:
            self.con.execute('DELETE FROM ins')
            self.con.execute('UPDATE tiles SET ins_done=0')
        if 'label' in later:
            self.con.execute('DELETE FROM pieces')
            self.con.execute('UPDATE tiles SET lab_done=0')
        if 'merge' in later:
            self.con.execute('DELETE FROM arcs')
        elif 'smooth' in later:
            self.con.execute('UPDATE arcs SET smooth=NULL, state=0 WHERE fixed=0')
        if 'smooth' in later:
            self.con.execute('DELETE FROM repair')
        if 'reconstruct' in later:
            self.con.execute('DELETE FROM result')
            self.con.execute("DELETE FROM meta WHERE key='recon_upto'")
        self.con.commit()

    # ── polygons ───────────────────────────────────────────────────────────
    def insert_polys(self, rows):
        """rows: (src_fid, attr, minx, miny, maxx, maxy, blob); returns nothing."""
        cur = self.con.cursor()
        for r in rows:
            cur.execute('INSERT INTO polys(src_fid, attr, minx, miny, maxx, maxy, geom) '
                        'VALUES (?,?,?,?,?,?,?)', r)
            if self.has_rtree:
                cur.execute('INSERT INTO polys_rt(id, minx, maxx, miny, maxy) VALUES (?,?,?,?,?)',
                            (cur.lastrowid, r[2], r[4], r[3], r[5]))

    def query_bbox(self, xmin, ymin, xmax, ymax):
        """poly_ids whose bbox intersects the box (conservative)."""
        if self.has_rtree:
            sql = ('SELECT id FROM polys_rt WHERE maxx >= ? AND minx <= ? '
                   'AND maxy >= ? AND miny <= ?')
        else:
            sql = ('SELECT poly_id FROM polys WHERE maxx >= ? AND minx <= ? '
                   'AND maxy >= ? AND miny <= ?')
        return [r[0] for r in self.con.execute(sql, (xmin, xmax, ymin, ymax))]

    def fetch_geoms(self, ids):
        """[(poly_id, geom_blob)] for ``ids``, ordered by poly_id."""
        return self.con.execute(
            'SELECT poly_id, geom FROM polys WHERE poly_id IN (SELECT value FROM json_each(?)) '
            'ORDER BY poly_id', (id_list(ids),)).fetchall()

    def poly_count(self):
        return self.con.execute('SELECT COUNT(*) FROM polys').fetchone()[0]

    def create_arc_indexes(self):
        self.con.executescript(_ARC_INDEXES)
        self.con.commit()
