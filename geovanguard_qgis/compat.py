"""QGIS API names shared by the QGIS 3.40 LTR and 4.x builds of the plugin.

QGIS 3.30–3.36 moved many enums into ``Qgis.*`` scoped enums and QGIS 4 (Qt6)
removed the old unscoped names.  The minimum QGIS version is 3.40, which has
all the scoped names below, so no fallback to the old names is needed (the
QGIS 4 / Qt6 compatibility check of plugins.qgis.org flags them).
"""
from qgis.core import Qgis, QgsFeatureSink, QgsField, QgsWkbTypes
from qgis.PyQt.QtCore import QMetaType

SOURCE_POLYGON = Qgis.ProcessingSourceType.VectorPolygon
SOURCE_LINE = Qgis.ProcessingSourceType.VectorLine

NUMBER_INTEGER = Qgis.ProcessingNumberParameterType.Integer
NUMBER_DOUBLE = Qgis.ProcessingNumberParameterType.Double

FILE_FOLDER = Qgis.ProcessingFileParameterBehavior.Folder

FLAG_ADVANCED = Qgis.ProcessingParameterFlag.Advanced

WKB_POLYGON = Qgis.WkbType.Polygon
WKB_MULTIPOLYGON = Qgis.WkbType.MultiPolygon
WKB_LINESTRING = Qgis.WkbType.LineString

SINK_FAST_INSERT = QgsFeatureSink.Flag.FastInsert

REQUEST_NO_GEOMETRY = Qgis.FeatureRequestFlag.NoGeometry

UNIT_DEGREES = Qgis.DistanceUnit.Degrees
UNIT_METERS = Qgis.DistanceUnit.Meters
UNIT_UNKNOWN = Qgis.DistanceUnit.Unknown


def make_field(name, kind):
    """QgsField of kind 'int' (64-bit) or 'double'."""
    return QgsField(name, QMetaType.Type.LongLong if kind == 'int' else QMetaType.Type.Double)


def add_advanced(param):
    param.setFlags(param.flags() | FLAG_ADVANCED)
    return param


def is_multi(wkb_type):
    return QgsWkbTypes.isMultiType(wkb_type)


def is_curved(wkb_type):
    return QgsWkbTypes.isCurvedType(wkb_type)
