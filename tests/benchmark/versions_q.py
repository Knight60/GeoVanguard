import sys, platform
from osgeo import gdal
import numpy, shapely
from qgis.core import Qgis
print('Python', platform.python_version()); print('QGIS', Qgis.version()); print('GDAL', gdal.__version__)
print('GEOS (shapely)', shapely.geos_version_string); print('shapely', shapely.__version__); print('numpy', numpy.__version__)
