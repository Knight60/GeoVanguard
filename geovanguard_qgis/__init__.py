"""GeoVanguard for QGIS — topology-preserving polygon smoothing (Processing tools).

The smoothing engine is the standalone ``geovanguard`` package, bundled in
the sub-folder ``geovanguard/`` (a junction to ../geovanguard while developing,
a copy in the released ZIP) and imported relatively, so it never clashes with a
``geovanguard`` installed into QGIS' Python with pip.
"""


def classFactory(iface):  # noqa: N802 (QGIS plugin API name)
    from .plugin import GeoVanguardPlugin
    return GeoVanguardPlugin(iface)
