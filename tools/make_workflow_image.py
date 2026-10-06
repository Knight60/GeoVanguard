"""Side-by-side workflow of the two tools for the README (docs/images/workflow.png).

    "C:\\Program Files\\QGIS 4.2.2\\bin\\python-qgis.bat" tools\\make_workflow_image.py

Same drawing style and colours as the help-panel pictures (make_help_images.py):
orange = input, purple = raster steps of Smooth Polygonization only, blue = the
Smooth Polygons engine (identical in both tools), green = output.  The shared
engine steps sit on the same rows in both columns.  Drawn as SVG and rendered
with Qt at 2x (PNG shows in every Markdown viewer).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_SVG = os.path.join(ROOT, 'docs', 'images', 'workflow.svg')
OUT_PNG = os.path.join(ROOT, 'docs', 'images', 'workflow.png')

COL_W = 330
GAP = 44
PAD = 22
HEAD = 34          # column title band
BOX_H = 54
LINE_H = 15
ROW = 82           # row pitch
FONT = 'Segoe UI'

INPUT = ('#FFF4DE', '#D08A1E')
RASTER = ('#F1E8FA', '#7E57C2')
ENGINE = ('#E3EEFB', '#3B73B9')
OUTPUT = ('#E6F4EA', '#3C8D50')

ENGINE_STEPS = [
    ('Find the shared borders', 'left / right polygon of every edge'),
    ('Join edges into arcs', 'one arc per border between two neighbours'),
    ('Smooth every arc once', 'Gaussian, Chaikin, … (parallel workers)'),
    ('Rebuild every polygon', 'from its own arcs: no gaps, no overlaps'),
    ('Check every polygon', 'problem spots are smoothed more gently'),
]

# (row, title, subtitle, colours) per column; rows 0..2 are before the engine
LEFT = [(2, 'Polygon layer', 'polygons that do not overlap', INPUT)]
RIGHT = [(0, 'Classified raster', 'whole-number classes (e.g. Sentinel-2)', INPUT),
         (1, 'Remove small patches', 'GDAL Sieve (optional)', RASTER),
         (2, 'Pixels to polygons', 'GDAL Polygonize', RASTER)]
FIRST_ENGINE = 3
OUT_ROW = FIRST_ENGINE + len(ENGINE_STEPS)
LEFT_OUT = ('Smoothed polygons', 'same attributes as the input', OUTPUT)
RIGHT_OUT = ('Smoothed polygons', 'field class_value; nodata stays empty', OUTPUT)


def esc(t):
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


ENGINE_GAP = 30    # extra space above the engine frame


def row_y(r):
    return PAD + HEAD + 14 + r * ROW + (ENGINE_GAP if r >= FIRST_ENGINE else 0)


def box(x, r, title, sub, colours):
    fill, stroke = colours
    y = row_y(r)
    w = COL_W - 56
    bx = x + (COL_W - w) / 2
    return ('<rect x="{:.1f}" y="{}" width="{}" height="{}" rx="8" fill="{}" stroke="{}" '
            'stroke-width="1.6"/>'
            '<text x="{:.1f}" y="{}" font-size="14" font-weight="bold" fill="#1F1F1F" '
            'text-anchor="middle">{}</text>'
            '<text x="{:.1f}" y="{}" font-size="11.5" fill="#444" text-anchor="middle">{}</text>'
            ).format(bx, y, w, BOX_H, fill, stroke, x + COL_W / 2, y + 22, esc(title),
                     x + COL_W / 2, y + 40, esc(sub))


def arrow(x, r_from, r_to):
    cx = x + COL_W / 2
    y1 = row_y(r_from) + BOX_H + 2
    y2 = row_y(r_to) - 3
    return ('<line x1="{0}" y1="{1}" x2="{0}" y2="{2}" stroke="#666" stroke-width="1.6"/>'
            '<polygon points="{0},{3} {4},{5} {6},{5}" fill="#666"/>').format(
                cx, y1, y2 - 6, y2, cx - 5, y2 - 8, cx + 5)


def frame(x, y, w, h, colours, title, fill='#FFFFFF', bold=True, size=15, left=False):
    _, stroke = colours
    tx, anchor = (x + 12, 'start') if left else (x + w / 2, 'middle')
    return ('<rect x="{}" y="{}" width="{}" height="{}" rx="10" fill="{}" stroke="{}" '
            'stroke-width="2"/><text x="{}" y="{}" font-size="{}" font-weight="{}" fill="{}" '
            'text-anchor="{}">{}</text>').format(
                x, y, w, h, fill, stroke, tx, y + 20, size, 'bold' if bold else 'normal',
                stroke, anchor, esc(title))


def svg():
    W = 2 * COL_W + GAP + 2 * PAD
    H = row_y(OUT_ROW) + BOX_H + PAD + 10
    parts = []
    xl, xr = PAD, PAD + COL_W + GAP
    top = PAD
    bottom = row_y(OUT_ROW) + BOX_H + 12
    # outer frames: Smooth Polygons (blue), Smooth Polygonization (purple)
    lt = row_y(2) - HEAD - 8
    parts.append(frame(xl, lt, COL_W, bottom - lt, ENGINE, 'Smooth Polygons', '#F4F8FD'))
    parts.append(frame(xr, top, COL_W, bottom - top, RASTER, 'Smooth Polygonization'))
    # the engine inside Smooth Polygonization = Smooth Polygons
    et = row_y(FIRST_ENGINE) - 30
    eb = row_y(OUT_ROW - 1) + BOX_H + 12
    parts.append(frame(xr + 10, et, COL_W - 20, eb - et, ENGINE, 'Smooth Polygons',
                       '#F4F8FD', bold=False, size=12.5, left=True))
    for x, before, out in ((xl, LEFT, LEFT_OUT), (xr, RIGHT, RIGHT_OUT)):
        rows = [r for r, _, _, _ in before]
        for r, t, s, c in before:
            parts.append(box(x, r, t, s, c))
        for k, (t, s) in enumerate(ENGINE_STEPS):
            parts.append(box(x, FIRST_ENGINE + k, t, s, ENGINE))
        parts.append(box(x, OUT_ROW, out[0], out[1], out[2]))
        seq = rows + list(range(FIRST_ENGINE, OUT_ROW + 1))
        for a, b in zip(seq, seq[1:]):
            parts.append(arrow(x, a, b))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{1}" '
            'viewBox="0 0 {0} {1}" font-family="{2}"><rect width="100%" height="100%" '
            'fill="#FFFFFF"/>{3}</svg>').format(W, H, FONT, ''.join(parts))


def main():
    from qgis.PyQt.QtCore import QRectF
    from qgis.PyQt.QtGui import QColor, QImage, QPainter
    from qgis.PyQt.QtSvg import QSvgRenderer
    from qgis.PyQt.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841 (fonts)
    os.makedirs(os.path.dirname(OUT_SVG), exist_ok=True)
    with open(OUT_SVG, 'w', encoding='utf-8') as f:
        f.write(svg())
    r = QSvgRenderer(OUT_SVG)
    size = r.defaultSize()
    img = QImage(size.width() * 2, size.height() * 2, QImage.Format.Format_ARGB32)
    img.fill(QColor('white'))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p, QRectF(0, 0, img.width(), img.height()))
    p.end()
    assert img.save(OUT_PNG), OUT_PNG
    print('wrote', OUT_PNG, img.width(), 'x', img.height())


if __name__ == '__main__':
    main()
