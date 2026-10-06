"""QGIS plugin entry: registers the Processing provider."""
from qgis.core import QgsApplication

from .provider import GeoVanguardProvider


class GeoVanguardPlugin(object):

    def __init__(self, iface=None):
        self.iface = iface
        self.provider = None

    def initProcessing(self):
        self.provider = GeoVanguardProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
