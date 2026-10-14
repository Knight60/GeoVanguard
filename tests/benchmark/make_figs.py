"""Manuscript figures from the benchmark results (QGIS Python: matplotlib, shapely, GDAL).

usage: python-qgis make_figs.py <figure ...>     figures: fig1 fig3 fig4a fig4b fig4 fig5 fig6  (fig4 joins fig4a and fig4b)
Writes PNG (300 dpi, 190 mm wide) to <repo>/manuscript/figures; results are read from $GV_BENCH.
"""
import csv
import glob
import json
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAN = os.path.join(REPO, 'manuscript')
FIG = os.path.join(MAN, 'figures')
DOCS = os.path.join(REPO, 'docs', 'images')
BENCH = os.environ.get('GV_BENCH', 'E:/tmp/gv_bench')
MM = 1 / 25.4
W2 = 190 * MM                                   # double column

INK, MUTED, GRID = '#1f1f1e', '#6b6a63', '#e4e3df'
TOOLS = {   # key: (label, colour, marker) — same in every figure
    'gv_gaussian': ('GeoVanguard (Gaussian)', '#1c5cab', 'o'),        # GeoVanguard family: blue
    'arc_sharededges': ('ArcGIS Smooth Shared Edges', '#9e1b24', 's'),  # ArcGIS family: red
    'arc_smoothpolygon': ('ArcGIS Smooth Polygon', '#e07a80', 'D'),
    'qgis_smooth': ('QGIS Smooth', '#b65fcc', '^'),  # QGIS: violet
    'grass_sliding_averaging': ('GRASS sliding averaging', '#237f39', 'v'),  # GRASS family: green
    'grass_chaiken': ('GRASS Chaiken', '#8fd49c', 'P'),
    'mapshaper': ('Mapshaper', '#e8740c', 'X'),  # Mapshaper: orange
}
SETS = {'syn_multi': 'Synthetic, multi-class', 'syn_single': 'Synthetic, single-class',
        'ft_multi': 'Forest type (real), multi-class', 'ga_single': 'Green area (real), single-class'}
plt.rcParams.update({'font.family': 'Arial', 'font.size': 7.5, 'axes.edgecolor': MUTED,
                     'axes.labelcolor': INK, 'xtick.color': MUTED, 'ytick.color': MUTED,
                     'axes.titlesize': 8, 'axes.titlecolor': INK})


def clean(ax):
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.grid(color=GRID, lw=0.5)
    ax.set_axisbelow(True)


def fig3():
    """Examples: a single class (green area), b multi-class (forest type)."""
    imgs = [plt.imread(os.path.join(DOCS, 'case_single_class.png')),
            plt.imread(os.path.join(DOCS, 'case_multi_class.png'))]
    hs = [i.shape[0] / i.shape[1] for i in imgs]
    fig, axes = plt.subplots(2, 1, figsize=(W2, W2 * sum(hs) * 1.03), gridspec_kw={'height_ratios': hs})
    for ax, im, tag in zip(axes, imgs, 'ab'):
        ax.imshow(im)
        ax.axis('off')
        ax.text(-0.01, 1.0, tag, transform=ax.transAxes, fontsize=10, fontweight='bold', va='top', ha='right')
    fig.subplots_adjust(left=0.03, right=1, top=1, bottom=0, hspace=0.04)
    fig.savefig(os.path.join(FIG, 'fig3_examples.png'), dpi=300)
    plt.close(fig)


def fig1():
    """Architecture and pipeline (vector). Stacked boxes = run per tile / batch in worker processes."""
    from matplotlib.patches import FancyBboxPatch, Rectangle
    C = {  # (fill, edge) — colours as in the README workflow image
        'input': ('#fde9d2', '#b85c12'), 'raster': ('#ece6f8', '#5b3fb0'),
        'engine': ('#e4eefb', '#1d5fb0'), 'key': ('#c9def7', '#1d5fb0'),
        'main': ('#ffffff', '#1d5fb0'), 'store': ('#f1f0ec', '#5f5e57'),
        'entry': ('#ffffff', '#1f1f1e'), 'output': ('#e1f1e1', '#2e7d32')}
    SUB = '#3d3c37'
    fig = plt.figure(figsize=(140 * MM, 132 * MM))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 140)
    ax.set_ylim(0, 132)
    ax.axis('off')

    def rbox(x, y, w, h, fc, ec, lw=1.0, ls='-', z=2):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0,rounding_size=1.6', fc=fc, ec=ec,
                                    lw=lw, ls=ls, zorder=z))

    def box(x, y, w, h, kind, title, lines=(), lw=1.0, stack=False):
        fc, ec = C[kind]
        if stack:                                   # parallel: two cards behind
            for k in (2, 1):
                rbox(x + 1.3 * k, y + 1.3 * k, w, h, fc, ec, lw=0.7, z=1.5 - 0.1 * k)
        rbox(x, y, w, h, fc, ec, lw=lw)
        n = len(lines)
        top = y + h / 2 + 1.9 * n
        ax.text(x + w / 2, top, title, ha='center', va='center', fontsize=7.2, fontweight='bold', color=INK,
                zorder=3)
        for i, s in enumerate(lines):
            ax.text(x + w / 2, top - 3.6 * (i + 1), s, ha='center', va='center', fontsize=6.1, color=SUB,
                    zorder=3)

    def arrow(pts):
        for (a, b), (c, d) in zip(pts[:-2], pts[1:-1]):
            ax.plot([a, c], [b, d], color=SUB, lw=0.9, zorder=1)
        ax.annotate('', xy=pts[-1], xytext=pts[-2], zorder=1,
                    arrowprops=dict(arrowstyle='-|>', color=SUB, lw=0.9, mutation_scale=7, shrinkA=0, shrinkB=0))

    # ── inputs ────────────────────────────────────────────────────────────────
    box(4, 118, 28, 9, 'input', 'Classified raster')
    box(38, 118, 28, 9, 'raster', 'Sieve (optional)', ['GDAL'])
    box(72, 118, 28, 9, 'raster', 'Polygonize', ['GDAL'])
    box(106, 118, 30, 9, 'input', 'Polygon coverage')
    arrow([(32, 122.5), (38, 122.5)])
    arrow([(66, 122.5), (72, 122.5)])

    # ── engine ────────────────────────────────────────────────────────────────
    rbox(3, 27, 134, 85, '#f7faff', C['engine'][1], lw=1.2, ls=(0, (4, 2)), z=0)
    ax.text(6, 108, 'geovanguard engine', fontsize=7.6, fontweight='bold', color=C['engine'][1])
    ax.text(41, 108, 'same code and results in plugin and package', fontsize=6.2, color=SUB)
    sw, sh = 38, 13
    xs = (7, 50, 93)
    yA, yB, yC = 87, 63, 41
    box(xs[2], yA, sw, sh, 'engine', '1  Load and tile', ['per batch of polygons', 'poly_id → SQLite'], stack=True)
    box(xs[1], yA, sw, sh, 'engine', '2  Insert junctions', ['per tile', 'vertices where 3 meet'], stack=True)
    box(xs[0], yA, sw, sh, 'engine', '3  Label edges', ['per tile: LPoly / RPoly', 'edges → arcs'], stack=True)
    box(xs[0], yB, sw, sh, 'main', 'Merge arcs', ['across tile borders', 'one arc per shared border'])
    box(xs[1], yB, sw, sh, 'key', '4  Smooth each arc once', ['per batch of arcs', 'Gaussian, Chaikin, …'],
        lw=1.8, stack=True)
    box(xs[2], yB, sw, sh, 'engine', '5  Rebuild polygons', ['per batch of polygons', 'walk arcs; holes alike'],
        stack=True)
    box(xs[2], yC, sw, sh, 'main', '6  Repair locally', ['gentler smoothing', 'near crossings'])
    arrow([(86, 118), (86, 114), (xs[2] + 10, 114), (xs[2] + 10, yA + sh + 2.6)])
    arrow([(121, 118), (121, yA + sh + 2.6)])
    arrow([(xs[2], yA + sh / 2), (xs[1] + sw + 2.6, yA + sh / 2)])
    arrow([(xs[1], yA + sh / 2), (xs[0] + sw + 2.6, yA + sh / 2)])
    arrow([(xs[0] + sw / 2, yA), (xs[0] + sw / 2, yB + sh)])
    arrow([(xs[0] + sw, yB + sh / 2), (xs[1], yB + sh / 2)])
    arrow([(xs[1] + sw + 2.6, yB + sh / 2), (xs[2], yB + sh / 2)])
    arrow([(xs[2] + sw / 2, yB), (xs[2] + sw / 2, yC + sh)])

    # tiles and workers: a 4 x 3 tile grid coloured by worker
    gx, gy, cw = 8, 31.5, 5.0
    wcol = ['#c9def7', '#fde9d2', '#e1f1e1']
    for r in range(3):
        for c in range(4):
            ax.add_patch(Rectangle((gx + c * cw, gy + r * cw), cw, cw, fc=wcol[(r * 4 + c) % 3], ec=SUB, lw=0.6,
                                   zorder=2))
    ax.text(gx + 4 * cw + 3, gy + 13.2, 'Tiles and workers', fontsize=7, fontweight='bold', color=INK)
    for i, s in enumerate(['map split into tiles (bounded memory);',
                           'tiles and batches shared by worker processes',
                           '(default 50–75 % of the idle cores);',
                           'the main process writes results in a fixed order',
                           '→ identical output for any worker count']):
        ax.text(gx + 4 * cw + 3, gy + 9.9 - 2.7 * i, s, fontsize=6.1, color=SUB, va='center')

    # work store
    box(xs[2], 29.5, sw, 9, 'store', 'SQLite work store', ['checkpoints → resume, pause'])

    # legend: stacked vs single
    lx, ly = 7, 53
    for k in (2, 1):
        rbox(lx + 0.8 * k, ly + 0.8 * k, 6, 4, C['engine'][0], C['engine'][1], lw=0.6, z=1.5)
    rbox(lx, ly, 6, 4, C['engine'][0], C['engine'][1], lw=0.8)
    ax.text(lx + 9, ly + 2, 'parallel worker processes', fontsize=6.1, color=SUB, va='center')
    rbox(lx + 45, ly, 6, 4, C['main'][0], C['main'][1], lw=0.8)
    ax.text(lx + 54, ly + 2, 'main process', fontsize=6.1, color=SUB, va='center')

    # ── entry points and output ───────────────────────────────────────────────
    box(7, 4, 38, 15, 'entry', 'QGIS plugin', ['Processing tools · QGIS GEOS', 'Pause / Resume button'])
    box(50, 4, 38, 15, 'entry', 'Python package API', ['smooth_polygons()', 'smooth_polygonization()'])
    box(93, 6, 38, 11, 'output', 'Smoothed polygons', ['no gaps, no overlaps'])
    arrow([(26, 19), (26, 27)])
    arrow([(69, 19), (69, 27)])
    arrow([(xs[2] + sw + 1.5, yC + sh / 2), (134.5, yC + sh / 2), (134.5, 11.5), (131, 11.5)])
    fig.savefig(os.path.join(FIG, 'fig1_architecture.png'), dpi=600)
    fig.savefig(os.path.join(FIG, 'fig1_architecture.pdf'))
    plt.close(fig)


def tune_rows():
    rows = list(csv.DictReader(open(os.path.join(BENCH, 'iou_out', 'tune.csv'))))
    info = json.load(open(os.path.join(BENCH, 'iou', 'datasets.json')))
    return rows, info


def fig4a():
    """mIoU gain over the unsmoothed input versus each tool's own parameter, four tuning sets."""
    rows, info = tune_rows()
    base = {r['dataset']: float(r['mIoU']) for r in rows if r['tool'] == 'input'}
    styles = {'syn_multi': ('-', 'o'), 'syn_single': ('--', 's'), 'ft_multi': ('-', '^'),
              'ga_single': (':', 'D')}
    shades = {'syn_multi': 1.0, 'syn_single': 0.75, 'ft_multi': 0.55, 'ga_single': 0.4}
    xlab = {'gv_gaussian': 'σ (input pixels)', 'arc_sharededges': 'PAEK tolerance (pixels)',
            'arc_smoothpolygon': 'PAEK tolerance (pixels)', 'qgis_smooth': 'iterations (best offset)',
            'grass_sliding_averaging': 'look-ahead (vertices, best slide)',
            'grass_chaiken': 'threshold (pixels)', 'mapshaper': 'interval (pixels)'}
    fig, axes = plt.subplots(2, 4, figsize=(W2, W2 * 0.48), sharey=True)
    axes = axes.ravel()
    for ax, (tool, (name, col, mk)) in zip(axes, TOOLS.items()):
        for kind in SETS:
            ds = kind + '_A'
            px = info[ds]['input_px']
            pts = {}
            for r in rows:
                if r['dataset'] != ds or r['tool'] != tool or r['seconds'] == 'FAILED' or not r['mIoU']:
                    continue
                p = r['param'].split(',')
                x = float(p[0]) if tool in ('qgis_smooth', 'grass_sliding_averaging') else float(p[0]) / px
                gain = float(r['mIoU']) - base[ds]
                pts[x] = max(pts.get(x, -1), gain)          # profile over the second parameter
            if not pts:
                continue
            xs = sorted(pts)
            ys = [pts[x] for x in xs]
            ls, m = styles[kind]
            ax.plot(xs, ys, ls, color=col, alpha=shades[kind], lw=1.4, marker=m, ms=3)
            k = int(np.argmax(ys))
            ax.plot(xs[k], ys[k], marker=m, ms=5, mfc='white', mec=col, mew=1.2)
        ax.axhline(0, color=MUTED, lw=0.8)
        ax.set_title(name, loc='left')
        ax.set_xlabel(xlab[tool])
        clean(ax)
    axes[0].set_ylabel('mIoU gain over input')
    axes[4].set_ylabel('mIoU gain over input')
    ax = axes[7]
    ax.axis('off')
    for i, (kind, label) in enumerate(SETS.items()):
        ls, m = styles[kind]
        ax.plot([0.02, 0.18], [0.85 - i * 0.18] * 2, ls, color=INK, alpha=shades[kind], marker=m, ms=3,
                transform=ax.transAxes)
        ax.text(0.22, 0.85 - i * 0.18, label, transform=ax.transAxes, va='center', fontsize=7, color=INK)
    ax.text(0.02, 0.06, 'open marker = best value\n0 = no change from the input', transform=ax.transAxes,
            fontsize=6.5, color=MUTED)
    lo = min(a.get_ylim()[0] for a in axes[:7])
    for a in axes[:7]:
        a.set_ylim(max(lo, -0.03), None)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'parts', 'fig4a_tuning.png'), dpi=300)
    plt.close(fig)


EVAL = os.path.join(BENCH, 'eval')
IOU_D = os.path.join(BENCH, 'iou')
ORDER = ['gv_gaussian', 'arc_sharededges', 'arc_smoothpolygon', 'qgis_smooth', 'grass_sliding_averaging',
         'grass_chaiken', 'mapshaper']


def ev(ds, tool, kind):
    p = os.path.join(EVAL, '%s_%s.%s.json' % (ds, tool, kind))
    return json.load(open(p)) if os.path.exists(p) else None


def broken(met):
    """Topology errors: gaps, overlaps (> 1 ppm of the area) or invalid polygons."""
    if not met:
        return False
    return (met['invalid'] > 0 or met['overlap_ppm'] > 1 or (met['gap_ppm'] or 0) > 1)


def fig4b():
    """mIoU gain over the input on the test sets B, per tool; hollow = topology errors."""
    fig, axes = plt.subplots(1, 4, figsize=(W2, W2 * 0.3), sharey=True)
    for ax, (kind, label) in zip(axes, SETS.items()):
        ds = kind + '_B'
        base = ev(ds, 'input', 'iou')['mIoU']
        for i, tool in enumerate(ORDER):
            r, m = ev(ds, tool, 'iou'), ev(ds, tool, 'met')
            if not r:
                continue
            name, col, mk = TOOLS[tool]
            g = r['mIoU'] - base
            bad = broken(m)
            ax.plot([0, g], [i, i], color=col, lw=1.2, alpha=0.5)
            ax.plot(g, i, marker=mk, ms=6, mfc='white' if bad else col, mec=col, mew=1.4, ls='none')
        ax.axvline(0, color=MUTED, lw=0.8)
        ax.set_title(label.replace(' (real)', ''), loc='left', fontsize=7)
        ax.set_xlabel('mIoU gain over input')
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(4))
        clean(ax)
        ax.grid(axis='y', visible=False)
    axes[0].set_yticks(range(len(ORDER)))
    axes[0].set_yticklabels([TOOLS[t][0] for t in ORDER])
    axes[0].invert_yaxis()
    fig.text(0.99, 0.01, 'hollow marker = gaps, overlaps or invalid polygons', ha='right', fontsize=6.5,
             color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(os.path.join(FIG, 'parts', 'fig4b_test_sets.png'), dpi=300)
    plt.close(fig)


def fig4():
    """Fig. 5 = 5a (tuning) above 5b (test sets) in one image."""
    a, b = plt.imread(os.path.join(FIG, 'parts', 'fig4a_tuning.png')), plt.imread(os.path.join(FIG, 'parts', 'fig4b_test_sets.png'))
    ha, hb = a.shape[0] / a.shape[1], b.shape[0] / b.shape[1]
    fig, axes = plt.subplots(2, 1, figsize=(W2, W2 * (ha + hb) * 1.02), gridspec_kw={'height_ratios': [ha, hb]})
    for ax, im, tag in zip(axes, (a, b), 'ab'):
        ax.imshow(im)
        ax.axis('off')
        ax.text(0.0, 1.0, tag, transform=ax.transAxes, fontsize=10, fontweight='bold', va='top')
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0, hspace=0.02)
    fig.savefig(os.path.join(FIG, 'fig4_accuracy.png'), dpi=300)
    plt.close(fig)


def fig5():
    """Zoomed comparison on the test sets: truth (light) + smoothed borders, gaps red, overlaps violet."""
    import shapely
    from osgeo import gdal, ogr
    best = json.load(open(os.path.join(EVAL, 'best.json')))
    panels = ['gv_gaussian', 'arc_sharededges', 'arc_smoothpolygon', 'qgis_smooth', 'grass_chaiken', 'mapshaper']
    rows = [('ga_single_B', 'a  Single-class (green area, 30 m)'),
            ('ft_multi_B', 'b  Multi-class (forest type, 30 m)')]
    GAP_C, OVER_C = '#e34948', '#6250d6'

    def read(path):
        ds = ogr.Open(path)
        g = [shapely.from_wkb(bytes(f.GetGeometryRef().ExportToIsoWkb())) for f in ds.GetLayer(0)
             if f.GetGeometryRef() is not None]
        return shapely.make_valid(np.array(g, dtype=object))

    fig = plt.figure(figsize=(W2, W2 * 0.95))
    gs = fig.add_gridspec(3, 5, width_ratios=[1, 1, 0.12, 1, 1])
    axes = np.array([[fig.add_subplot(gs[k, c]) for c in (0, 1, 3, 4)] for k in range(3)])
    for r, (ds, title) in enumerate(rows):
        kind = ds[:-2]
        t = gdal.Open(os.path.join(IOU_D, ds + '_truth.tif'))
        gt = t.GetGeoTransform()
        truth = t.GetRasterBand(1).ReadAsArray()
        inp = read(os.path.join(IOU_D, ds + '.gpkg'))
        # window: 1.5 km with the most input polygons (away from the edge)
        Wm = 600.0
        cent = shapely.centroid(inp)
        cx, cy = shapely.get_x(cent), shapely.get_y(cent)
        x0s = np.arange(gt[0] + 600, gt[0] + t.RasterXSize * gt[1] - 600 - Wm, Wm / 2)
        y0s = np.arange(gt[3] + t.RasterYSize * gt[5] + 600, gt[3] - 600 - Wm, Wm / 2)
        win = max(((x, y) for x in x0s for y in y0s),
                  key=lambda w: np.count_nonzero((cx > w[0]) & (cx < w[0] + Wm) & (cy > w[1]) & (cy < w[1] + Wm)))
        box = shapely.box(win[0], win[1], win[0] + Wm, win[1] + Wm)
        c0, r0 = int((win[0] - gt[0]) / gt[1]), int((gt[3] - win[1] - Wm) / -gt[5])
        n = int(Wm / gt[1])
        sub = truth[r0:r0 + n, c0:c0 + n]
        vals = np.unique(sub)
        rgb = np.ones(sub.shape + (3,))
        cmap = plt.get_cmap('tab20')
        for k, v in enumerate(vals):
            if v == 0:
                continue
            col = np.array(matplotlib.colors.to_rgb('#4caf50')) if 'single' in ds else np.array(cmap(k % 20)[:3])
            rgb[sub == v] = 1 - 0.38 * (1 - col)                     # light truth colours
        for c, tool in enumerate(panels):
            ax = axes[c // 2, 2 * r + c % 2]
            ax.imshow(rgb, extent=(win[0], win[0] + Wm, win[1], win[1] + Wm), interpolation='nearest', zorder=0)
            p = best[kind][tool]['param'].replace(',', '_')
            path = os.path.join(EVAL, '%s_%s_p%s.gpkg' % (ds, tool, p))
            if not os.path.exists(path):
                path = path[:-5] + '.shp'
            g = read(path)
            g = g[shapely.intersects(g, box)]
            tree = shapely.STRtree(g)
            i, j = tree.query(g, predicate='intersects')
            over = [shapely.intersection(g[a], g[b]) for a, b in zip(i, j) if a < b]
            over = [o for o in over if o.area > 0.5]
            if 'multi' in ds:
                gaps = shapely.intersection(shapely.difference(box, shapely.union_all(g)), box)
                gaps = [q for q in shapely.get_parts(gaps) if q.area > 0.5]
            else:
                gaps = []
            for geoms, colr in ((gaps, GAP_C), (over, OVER_C)):
                for q in geoms:
                    for part in shapely.get_parts(q):
                        if part.geom_type == 'Polygon':
                            xy = shapely.get_coordinates(part.exterior)
                            ax.fill(xy[:, 0], xy[:, 1], fc=colr, lw=1.6, ec=colr, zorder=2)   # thick edge: thin slivers stay visible
            for geom in g:
                for ring in shapely.get_rings(shapely.get_parts(geom)):
                    xy = shapely.get_coordinates(ring)
                    ax.plot(xy[:, 0], xy[:, 1], color=INK, lw=0.45, zorder=3)
            iou, met = ev(ds, tool, 'iou'), ev(ds, tool, 'met')
            gp = '—' if met['gap_parts'] is None else str(met['gap_parts'])
            name = {'gv_gaussian': 'GeoVanguard\n(Gaussian)', 'arc_sharededges': 'ArcGIS\nShared Edges',
                    'arc_smoothpolygon': 'ArcGIS\nSmooth Polygon', 'qgis_smooth': 'QGIS\nSmooth',
                    'grass_chaiken': 'GRASS\nChaiken', 'mapshaper': 'Mapshaper\n-simplify'}[tool]
            ax.set_title('%s\nmIoU %.3f · invalid %d\ngaps %s · overlap %d ppm' % (
                name, iou['mIoU'], met['invalid'], gp, max(0, round(met['overlap_ppm']))),
                fontsize=7.5, color=INK, linespacing=1.2)
            ax.set_xlim(win[0], win[0] + Wm)
            ax.set_ylim(win[1], win[1] + Wm)
            ax.set_aspect('equal')
            ax.set_xticks([])
            ax.set_yticks([])
            for s in ax.spines.values():
                s.set_color('#bdbcb5')
        axes[0, 2 * r].text(-0.01, 1.45, title, transform=axes[0, 2 * r].transAxes, fontsize=9, fontweight='bold',
                        color=INK)
        axes[0, 2 * r].plot([win[0] + 30, win[0] + 230], [win[1] + 30, win[1] + 30], color=INK, lw=1.5, zorder=4)
        axes[0, 2 * r].text(win[0] + 130, win[1] + 50, '200 m', ha='center', fontsize=7.5, color=INK, zorder=4)
    fig.legend(handles=[plt.Rectangle((0, 0), 1, 1, fc='#e7f0e7', ec='#bdbcb5', label='truth (light)'),
                        plt.Line2D([], [], color=INK, lw=1, label='smoothed borders'),
                        plt.Rectangle((0, 0), 1, 1, color=GAP_C, label='gap (multi-class only)'),
                        plt.Rectangle((0, 0), 1, 1, color=OVER_C, label='overlap')],
               loc='lower center', ncol=4, frameon=False, fontsize=7.5)
    fig.subplots_adjust(top=0.88, bottom=0.05, left=0.035, right=0.965, wspace=0.08, hspace=0.38)
    fig.savefig(os.path.join(FIG, 'fig5_comparison.png'), dpi=300)
    plt.close(fig)


def scaling_rows():
    """{tool: {n_polygons: (seconds or None for > 1 h, peak MB)}} from the timed-run logs."""
    import re
    n = {'ft1000': 490, 'ft2000': 1205, 'ft4000': 7653, 'ft8000': 55537, 'ft16000': 224021, 'ftfull': 385679}
    out = {}
    text = ''
    for log in ('evaluate.log', 'large_data.log', 'extra.log', 'night.log'):   # extra, night: no 1 h limit
        p = os.path.join(BENCH, log)
        if os.path.exists(p):
            text += open(p, encoding='utf8', errors='replace').read() + '\n'
    m = re.search(r'RESUME uninterrupted ([\d.]+) s', text)   # full map without the 1 h limit
    if m:
        out.setdefault('gv_gaussian', {})[n['ftfull']] = (float(m.group(1)), None)
    for line in text.splitlines():
        m = re.match(r'(ft(?:\d+|full)) RESULT (\w+):\S+ (\S+)(.*)', line)
        if m:
            ds, tool, sec, rest = m.groups()
            pk = re.search(r'peak_mb=(\d+)', rest)
            if sec == 'FAILED' and out.get(tool, {}).get(n[ds], (None,))[0]:
                continue
            out.setdefault(tool, {})[n[ds]] = (None if sec == 'FAILED' else float(sec),
                                                int(pk.group(1)) if pk else None)
    return out


def fig6():
    """Large data: (a) run time and (b) peak memory versus the number of input polygons."""
    rows = scaling_rows()
    order = ['gv_gaussian', 'gv_gaussian_1w', 'arc_sharededges', 'arc_smoothpolygon', 'qgis_smooth',
             'grass_sliding_averaging', 'grass_chaiken', 'mapshaper']
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(W2, W2 * 0.56), sharex=True)   # side by side
    for tool in order:
        if tool not in rows:
            continue
        if tool == 'gv_gaussian_1w':        # same method, one process: light blue, dashed
            name, col, mk, ls = 'GeoVanguard (Gaussian), single process (1 core)', '#6aa8f0', 'o', '--'
        elif tool == 'gv_gaussian':          # automatic worker count: GeoVanguard blue
            name, col, mk, ls = 'GeoVanguard (Gaussian), automatic workers (≤ 9 of 12 cores)', '#1c5cab', 'o', '-'
        else:                                # second member of a family (lighter tone): dashed
            (name, col, mk), ls = TOOLS[tool], '--' if tool in ('arc_smoothpolygon', 'grass_chaiken') else '-'
        pts = sorted(rows[tool].items())
        ok = [(x, s, m) for x, (s, m) in pts if s is not None]
        xs = [p[0] for p in ok]
        a1.plot(xs, [p[1] for p in ok], ls, color=col, marker=mk, ms=4, lw=1.4,
                label=name.replace(' (Gaussian)', ' (Gaussian)'))
        a2.plot([p[0] for p in ok if p[2]], [p[2] / 1000 for p in ok if p[2]], ls, color=col, marker=mk, ms=4,
                lw=1.4)
        if tool in ('gv_gaussian', 'gv_gaussian_1w'):   # GeoVanguard runs above 1 h, labelled in minutes
            for x, s, _ in ok:                         # in the colour of their own line
                if s > 3600:
                    right = x > 3e5                    # full map: right of the point; 224,021: left
                    a1.annotate('%.0f min' % (round(s) / 60), (x, s), xytext=(6, -3) if right else (-8, 4),
                                textcoords='offset points', ha='left' if right else 'right', fontsize=6.5,
                                color=col)
        over = [x for x, (s, m) in pts if s is None]
        if over:                                    # stopped at the 1 h limit
            a1.plot([xs[-1], over[0]], [ok[-1][1], 3600], ls, color=col, lw=1, alpha=0.5)
            a1.plot(over[0], 3600, marker='^', ms=6, mfc='white', mec=col, mew=1.2)
    a1.axhline(3600, color=MUTED, lw=0.7, ls=':')
    a1.text(500, 3900, '1 h limit', fontsize=6, color=MUTED)
    for ax in (a1, a2):
        ax.set_xscale('log')
        ax.set_yscale('log')
        clean(ax)
    a1.set_xlabel('Input polygons')
    a2.set_xlabel('Input polygons')
    a1.set_ylabel('Run time (seconds)')
    sec = a1.secondary_yaxis('right', functions=(lambda x: x / 60.0, lambda m: m * 60.0))   # same quantity
    sec.set_ylabel('Run time (minutes)', color=INK)
    sec.tick_params(colors=MUTED)
    sec.set_yticks([0.1, 1, 10, 60, 130])                 # 130 min: single process, full map
    sec.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: '%g' % v))
    sec.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    a2.set_ylabel('Peak memory (GB)')
    a1.set_title('a  Run time (open triangle: stopped at 1 h)', loc='left')
    a2.set_title('b  Peak memory of the whole process tree', loc='left')
    a1.set_xlim(300, 1.3e6)                               # room for the minute labels
    h, l = a1.get_legend_handles_labels()
    fig.legend(h, l, loc='lower center', ncol=3, frameon=False, fontsize=6.8)
    fig.tight_layout(rect=(0, 0.13, 1, 1), w_pad=2.5)
    fig.savefig(os.path.join(FIG, 'fig6_large_data.png'), dpi=300)
    plt.close(fig)


if __name__ == '__main__':
    for name in sys.argv[1:]:
        globals()[name]()
        print('wrote', name)
