"""Processing provider for the topology-preserving smoothing tools."""
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithms import SmoothPolygonizeAlgorithm, TopologySmoothAlgorithm


class GeoVanguardProvider(QgsProcessingProvider):

    def loadAlgorithms(self):
        from . import pause_ui
        pause_ui.init()                  # GUI-thread helper for the Pause button
        self.addAlgorithm(TopologySmoothAlgorithm())
        self.addAlgorithm(SmoothPolygonizeAlgorithm())

    def id(self):
        return 'geovanguard'

    def name(self):
        return 'GeoVanguard'

    def longName(self):
        return 'GeoVanguard — efficient open-source tools for large, complex RS/GIS data'

    def icon(self):
        path = os.path.join(os.path.dirname(__file__), 'GeoVanguard-Logo-Mini.png')
        return QIcon(path) if os.path.exists(path) else QgsProcessingProvider.icon(self)
