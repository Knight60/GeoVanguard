"""Draw the workflow pictures shown in the tools' help panel (Tool Info).

    python-qgis.bat tools/make_help_images.py

Writes geovanguard_qgis/help/<name>.svg, <name>.png (1x) and <name>@2x.png.
The help panel shows the PNG at its natural size (Qt's rich text scales
images without smoothing, so a down-scaled picture becomes unreadable) and
picks the @2x file by itself on high-DPI screens.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'geovanguard_qgis', 'help')

W = 340
BOX_H = 50
LINE_H = 15
GAP = 18
PAD = 10

COMMON = [
    ('Find the shared borders', 'each border between two neighbours is one line', '#E8F1FB', '#4A7EBB'),
    ('Smooth every border once', 'Gaussian (recommended), Chaikin, …', '#E8F1FB', '#4A7EBB'),
    ('Rebuild the polygons', 'neighbours use the same smoothed border →\nno gaps, no overlaps', '#E8F1FB', '#4A7EBB'),
    ('Check every polygon', 'problem spots are smoothed more gently', '#E8F1FB', '#4A7EBB'),
]

TOOLS = {
    'workflow_polygons': [
        ('Polygon layer', 'polygons that do not overlap (e.g. land cover)', '#FFF4DE', '#D08A1E'),
    ] + COMMON + [
        ('Smoothed polygons', 'same attributes as the input', '#E6F4EA', '#3C8D50'),
    ],
    'workflow_polygonization': [
        ('Classified raster', 'whole-number classes (e.g. Sentinel-2 map)', '#FFF4DE', '#D08A1E'),
        ('Remove small patches (optional)', 'regions smaller than N pixels', '#F2F2F2', '#8A8A8A'),
        ('Convert pixels to polygons', 'one polygon per patch of the same class', '#E8F1FB', '#4A7EBB'),
    ] + COMMON + [
        ('Smoothed polygons', 'field class_value; nodata stays empty', '#E6F4EA', '#3C8D50'),
    ],
}


def esc(t):
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def before_after(y):
    """Two small panels: pixel-staircase border → smooth shared border."""
    w = (W - 3 * PAD) / 2.0
    h = 70
    top, bot = y + 4, y + h - 4
    out = []
    for k, title in enumerate(('Before', 'After')):
        x0 = PAD + k * (w + PAD)
        ys = [top + i * (bot - top) / 6.0 for i in range(7)]
        if k == 0:      # staircase: vertical and horizontal pixel edges
            fx = [0.40, 0.50, 0.50, 0.62, 0.62, 0.48, 0.48]
            border = []
            for i, f in enumerate(fx):
                border.append((x0 + w * f, ys[i]))
                if i + 1 < len(fx):
                    border.append((x0 + w * f, ys[i + 1]))
        else:           # the same border, smoothed
            fx = [0.42, 0.48, 0.55, 0.59, 0.56, 0.51, 0.48]
            border = [(x0 + w * f, yy) for f, yy in zip(fx, ys)]
        pts = ' '.join('{:.1f},{:.1f}'.format(px, py) for px, py in border)
        out.append('<rect x="{:.1f}" y="{}" width="{:.1f}" height="{}" rx="6" fill="#FFFFFF" '
                   'stroke="#C8C8C8"/>'.format(x0, y, w, h))
        out.append('<polygon points="{:.1f},{:.1f} {} {:.1f},{:.1f}" fill="#B7D9A8"/>'.format(
            x0 + 4, top, pts, x0 + 4, bot))
        out.append('<polygon points="{:.1f},{:.1f} {} {:.1f},{:.1f}" fill="#F3DFA2"/>'.format(
            x0 + w - 4, top, pts, x0 + w - 4, bot))
        out.append('<polyline points="{}" fill="none" stroke="#333" stroke-width="1.6" '
                   'stroke-linejoin="round"/>'.format(pts))
        out.append('<text x="{:.1f}" y="{}" font-size="12" font-weight="bold" fill="#333" '
                   'text-anchor="middle">{}</text>'.format(x0 + w / 2, y + h + 14, title))
    return out, h + 22


def svg(steps):
    parts = []
    y = PAD
    for i, (title, sub, fill, stroke) in enumerate(steps):
        lines = sub.split('\n')
        bh = BOX_H + LINE_H * (len(lines) - 1)
        parts.append('<rect x="{}" y="{}" width="{}" height="{}" rx="8" fill="{}" stroke="{}" '
                     'stroke-width="1.5"/>'.format(PAD, y, W - 2 * PAD, bh, fill, stroke))
        parts.append('<text x="{}" y="{}" font-size="14" font-weight="bold" fill="#1F1F1F" '
                     'text-anchor="middle">{}</text>'.format(W / 2, y + 20, esc(title)))
        for j, ln in enumerate(lines):
            parts.append('<text x="{}" y="{}" font-size="12" fill="#444" text-anchor="middle">'
                         '{}</text>'.format(W / 2, y + 38 + LINE_H * j, esc(ln)))
        y += bh
        if i + 1 < len(steps):
            parts.append('<line x1="{0}" y1="{1}" x2="{0}" y2="{2}" stroke="#777" stroke-width="1.6"/>'
                         .format(W / 2, y + 2, y + GAP - 6))
            parts.append('<polygon points="{0},{1} {2},{3} {4},{3}" fill="#777"/>'.format(
                W / 2, y + GAP - 1, W / 2 - 5, y + GAP - 8, W / 2 + 5))
            y += GAP
    y += 14
    ba, h = before_after(y)
    parts += ba
    y += h + PAD
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{1}" viewBox="0 0 {0} {1}" '
            'font-family="Segoe UI">'
            '<rect width="100%" height="100%" fill="#FFFFFF"/>{2}</svg>').format(W, int(y), ''.join(parts))


def render_png(svg_path, png_path, scale=2):
    from qgis.PyQt.QtCore import QRectF
    from qgis.PyQt.QtGui import QColor, QImage, QPainter
    from qgis.PyQt.QtSvg import QSvgRenderer
    r = QSvgRenderer(svg_path)
    size = r.defaultSize()
    fmt = getattr(QImage, 'Format_ARGB32', None) or QImage.Format.Format_ARGB32
    img = QImage(size.width() * scale, size.height() * scale, fmt)
    img.fill(QColor('white'))
    p = QPainter(img)
    hint = getattr(QPainter, 'Antialiasing', None) or QPainter.RenderHint.Antialiasing
    p.setRenderHint(hint)
    r.render(p, QRectF(0, 0, img.width(), img.height()))
    p.end()
    assert img.save(png_path), png_path


LOGO = os.path.join(ROOT, 'logo', 'GeoVanguard-Logo-Full.png')
LOGO_W = 240          # display width of the logo at the top of the help panel


def logo_pngs():
    """help/logo.png (LOGO_W px wide) and help/logo@2x.png, smoothly down-scaled."""
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QImage
    if not os.path.exists(LOGO):
        print('logo source not found (kept the existing help/logo.png):', LOGO)
        return
    img = QImage(LOGO)
    assert not img.isNull(), LOGO
    aspect = getattr(Qt, 'KeepAspectRatio', None) or Qt.AspectRatioMode.KeepAspectRatio
    smooth = getattr(Qt, 'SmoothTransformation', None) or Qt.TransformationMode.SmoothTransformation
    for name, w in (('logo.png', LOGO_W), ('logo@2x.png', 2 * LOGO_W)):
        out = img.scaledToWidth(w, smooth)
        assert out.save(os.path.join(OUT, name)), name
        print('wrote', name, out.width(), 'x', out.height())


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    from qgis.PyQt.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841
    logo_pngs()
    for name, steps in TOOLS.items():
        sp = os.path.join(OUT, name + '.svg')
        with open(sp, 'w', encoding='utf-8') as f:
            f.write(svg(steps))
        render_png(sp, os.path.join(OUT, name + '.png'), scale=1)
        render_png(sp, os.path.join(OUT, name + '@2x.png'), scale=2)
        print('wrote', sp, '+ .png + @2x.png')


if __name__ == '__main__':
    # The normal platform plugin (no window is shown) has the system fonts; the
    # offscreen one renders with a fallback font that is hard to read.
    if sys.platform != 'win32' and not os.environ.get('DISPLAY'):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    main()
