"""Meters → layer CRS units conversion for distance parameters."""
import math

from qgis.core import (QgsCoordinateTransformContext, QgsDistanceArea, QgsPointXY,
                       QgsUnitTypes)

from .compat import UNIT_DEGREES, UNIT_METERS, UNIT_UNKNOWN


def crs_units_per_meter(crs, extent):
    """Return ``(factor, note)``: multiply meters by ``factor`` to get layer units.

    Projected CRS: exact unit conversion (meters, feet, ...).
    Geographic CRS: degrees per meter measured on the CRS ellipsoid at the
    centre of the data (geometric mean of the east-west and north-south
    scales), so it is an approximation that degrades far from the centre —
    ``note`` says so.
    """
    unit = crs.mapUnits()
    if unit == UNIT_DEGREES or crs.isGeographic():
        cx, cy = 0.0, 0.0
        if extent is not None and not extent.isNull() and not extent.isEmpty():
            cx = extent.center().x()
            cy = extent.center().y()
        cy = max(-85.0, min(85.0, cy))
        da = QgsDistanceArea()
        da.setSourceCrs(crs, QgsCoordinateTransformContext())
        da.setEllipsoid(crs.ellipsoidAcronym() or 'EPSG:7030')
        east = da.measureLine(QgsPointXY(cx - 0.5, cy), QgsPointXY(cx + 0.5, cy))
        north = da.measureLine(QgsPointXY(cx, cy - 0.5), QgsPointXY(cx, cy + 0.5))
        m_per_deg = math.sqrt(east * north) if east > 0 and north > 0 else 111320.0
        factor = 1.0 / m_per_deg
        note = ('Layer CRS is geographic: distances in meters are converted with '
                '{:.1f} m per degree (at latitude {:.2f}°). Accuracy decreases away from '
                'that latitude; reproject to a projected CRS for exact distances.'.format(
                    m_per_deg, cy))
        return factor, note
    if unit == UNIT_UNKNOWN:
        return 1.0, 'Layer CRS units are unknown: distances are used as-is (no conversion).'
    return QgsUnitTypes.fromUnitToUnitFactor(UNIT_METERS, unit), None
