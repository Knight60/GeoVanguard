"""QGIS 3.x / 4.x API compatibility shims.

QGIS 3.30–3.36 moved many enums into ``Qgis.*`` scoped enums and QGIS 4
(Qt6) removed the old unscoped names.  Each constant below resolves to the
new name when it exists and falls back to the old one.
"""
from qgis.core import (
    Qgis,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsField,
    QgsProcessing,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsUnitTypes,
    QgsWkbTypes,
)


def _first(*getters):
    for get in getters:
        try:
            return get()
        except AttributeError:
            continue
    raise AttributeError('No compatible QGIS API found')


SOURCE_POLYGON = _first(lambda: Qgis.ProcessingSourceType.VectorPolygon,
                        lambda: QgsProcessing.TypeVectorPolygon)
SOURCE_LINE = _first(lambda: Qgis.ProcessingSourceType.VectorLine,
                     lambda: QgsProcessing.TypeVectorLine)

NUMBER_INTEGER = _first(lambda: Qgis.ProcessingNumberParameterType.Integer,
                        lambda: QgsProcessingParameterNumber.Integer)
NUMBER_DOUBLE = _first(lambda: Qgis.ProcessingNumberParameterType.Double,
                       lambda: QgsProcessingParameterNumber.Double)

FILE_FOLDER = _first(lambda: Qgis.ProcessingFileParameterBehavior.Folder,
                     lambda: QgsProcessingParameterFile.Folder)

FLAG_ADVANCED = _first(lambda: Qgis.ProcessingParameterFlag.Advanced,
                       lambda: QgsProcessingParameterDefinition.FlagAdvanced)

WKB_POLYGON = _first(lambda: Qgis.WkbType.Polygon, lambda: QgsWkbTypes.Polygon)
WKB_MULTIPOLYGON = _first(lambda: Qgis.WkbType.MultiPolygon, lambda: QgsWkbTypes.MultiPolygon)
WKB_LINESTRING = _first(lambda: Qgis.WkbType.LineString, lambda: QgsWkbTypes.LineString)

SINK_FAST_INSERT = _first(lambda: QgsFeatureSink.Flag.FastInsert,
                          lambda: QgsFeatureSink.FastInsert)

REQUEST_NO_GEOMETRY = _first(lambda: Qgis.FeatureRequestFlag.NoGeometry,
                             lambda: QgsFeatureRequest.NoGeometry)

UNIT_DEGREES = _first(lambda: Qgis.DistanceUnit.Degrees,
                      lambda: QgsUnitTypes.DistanceDegrees)
UNIT_METERS = _first(lambda: Qgis.DistanceUnit.Meters,
                     lambda: QgsUnitTypes.DistanceMeters)
UNIT_UNKNOWN = _first(lambda: Qgis.DistanceUnit.Unknown,
                      lambda: QgsUnitTypes.DistanceUnknownUnit)


def make_field(name, kind):
    """QgsField of kind 'int' (64-bit) or 'double' for both Qt5 and Qt6 builds."""
    try:
        from qgis.PyQt.QtCore import QMetaType
        mtype = QMetaType.Type.LongLong if kind == 'int' else QMetaType.Type.Double
        return QgsField(name, mtype)
    except (AttributeError, TypeError):
        from qgis.PyQt.QtCore import QVariant
        vtype = QVariant.LongLong if kind == 'int' else QVariant.Double
        return QgsField(name, vtype)


def add_advanced(param):
    param.setFlags(param.flags() | FLAG_ADVANCED)
    return param


def is_multi(wkb_type):
    return QgsWkbTypes.isMultiType(wkb_type)


def is_curved(wkb_type):
    return QgsWkbTypes.isCurvedType(wkb_type)
