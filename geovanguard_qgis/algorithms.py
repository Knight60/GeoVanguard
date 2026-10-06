"""QGIS Processing algorithms.

A) TopologySmoothAlgorithm     – Smooth Polygons (vector input)
B) SmoothPolygonizeAlgorithm   – Smooth Polygonization (raster input)

Shown in the Processing Toolbox as GeoVanguard › Smoothing Topology Preserver,
ids geovanguard:smooth_polygons and geovanguard:smooth_polygonization.

Both run the same LPoly/RPoly pipeline (geovanguard/pipeline.py) as the
standalone package, with the QGIS geometry backend.  Setting a work folder
keeps the intermediate database so a large job can be resumed, or re-run with
different smoothing settings without rebuilding the topology.
"""
import math
import os
import shutil
import tempfile

from qgis.core import (
    QgsFeature,
    QgsFeatureRequest,
    QgsFields,
    QgsGeometry,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBand,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingUtils,
)
from qgis.PyQt.QtCore import QCoreApplication, QUrl

from . import compat, outputs, pause_ui, units
from .geovanguard.core import smoothing, topology
from .geovanguard.core.store import ARC_FALLBACK, ARC_FALLBACK2, ARC_SMOOTHED
from .geovanguard.params import DEFAULT_ALGORITHM, DEFAULTS, smooth_signature
from .geovanguard.pause import PauseControl
from .geovanguard.pipeline import Canceled, SmoothPipeline
from .geovanguard.progress import PIPELINE_STEPS, StepTracker
from .qgis_backend import QgisGeosBackend


UNITS_METERS = 0
UNITS_CRS = 1

# smoothing defaults (Sentinel-2 tuned) live in params.py, shared with the
# standalone API
DEFAULT_PRE_SIMPLIFY = DEFAULTS['pre_simplify']
DEFAULT_SIGMA = DEFAULTS['sigma']

# Above these sizes a temporary (memory) output is slow to draw / identify
# (no spatial index, everything in RAM) and is lost when QGIS closes.
LARGE_OUTPUT_FEATURES = 50000
LARGE_RASTER_PIXELS = 10000000
# Help button → this toolset's section of the README in the public repository
HELP_URL = 'https://github.com/Knight60/geovanguard#smoothing-topology-preserver'
CREDIT = '\n\n<i>GeoVanguard — developed by Pisut Nakmuenwai</i>'

TOO_LARGE = (
    '{what}: this job is too large for a temporary layer. A temporary layer keeps everything '
    'in memory without a spatial index, is very slow to draw and query, and is lost when QGIS '
    'closes. Choose a file for "{output}" (e.g. a GeoPackage, .gpkg); the work folder is then '
    'created next to it with the same name.')


class _TopologyAlgorithm(QgsProcessingAlgorithm):
    ALGORITHM = 'ALGORITHM'
    UNITS = 'UNITS'
    ITERATIONS = 'ITERATIONS'
    SIGMA = 'SIGMA'
    PRE_SIMPLIFY = 'PRE_SIMPLIFY'
    POST_SIMPLIFY = 'POST_SIMPLIFY'
    DP_TOLERANCE = 'DP_TOLERANCE'
    POINTS_PER_100 = 'POINTS_PER_100'
    DEGREE = 'DEGREE'
    SPLINE_SMOOTHING = 'SPLINE_SMOOTHING'
    TILE_SIZE = 'TILE_SIZE'
    PRECISION = 'PRECISION'
    WORKERS = 'WORKERS'
    WORK_DIR = 'WORK_DIR'
    OUTPUT = 'OUTPUT'
    OUTPUT_ARCS = 'OUTPUT_ARCS'

    def tr(self, text):
        return QCoreApplication.translate('GeoVanguard', text)

    def group(self):
        return self.tr('Smoothing Topology Preserver')

    def groupId(self):
        return 'smoothing_topology_preserver'

    def helpUrl(self):
        return HELP_URL

    @staticmethod
    def _help_image(name):
        """Picture help/<name>.png (natural size; @2x picked on high-DPI), or ''."""
        path = os.path.join(os.path.dirname(__file__), 'help', name + '.png')
        if not os.path.exists(path):
            return ''
        return '<p align="center"><img src="{}"></p>'.format(
            QUrl.fromLocalFile(path).toString())

    _workflow = _help_image

    # ── parameters shared by both tools ──────────────────────────────────────
    def _num(self, name, label, kind, default, minimum=None, advanced=False, maximum=None):
        p = QgsProcessingParameterNumber(name, label, type=kind, defaultValue=default)
        if minimum is not None:
            p.setMinimum(minimum)
        if maximum is not None:
            p.setMaximum(maximum)
        if advanced:
            compat.add_advanced(p)
        self.addParameter(p)

    def _add_smoothing_parameters(self):
        method = QgsProcessingParameterEnum(
            self.ALGORITHM, self.tr('Smoothing algorithm'),
            options=list(smoothing.ALGORITHMS),
            defaultValue=smoothing.ALGORITHMS.index(DEFAULT_ALGORITHM))
        try:
            # show only the parameters of the selected method in the dialog
            from .gui_wrappers import SmoothingMethodWrapper
            method.setMetadata({'widget_wrapper': {'class': SmoothingMethodWrapper}})
        except ImportError:          # no Processing GUI (e.g. qgis_process)
            pass
        self.addParameter(method)
        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr('Distance units (for all distances below)'),
            options=[self.tr('Meters (converted to layer CRS units)'),
                     self.tr('Layer CRS units')], defaultValue=UNITS_METERS))
        self._num(self.ITERATIONS, self.tr('Chaikin iterations'),
                  compat.NUMBER_INTEGER, DEFAULTS['iterations'], 1, maximum=10)
        self._num(self.PRE_SIMPLIFY, self.tr('Pre-simplify tolerance (≈ 1 pixel; 0 = off)'),
                  compat.NUMBER_DOUBLE, DEFAULT_PRE_SIMPLIFY, 0.0)
        self._num(self.POST_SIMPLIFY, self.tr('Post-simplify tolerance (0 = off)'),
                  compat.NUMBER_DOUBLE, DEFAULTS['post_simplify'], 0.0)
        self._num(self.SIGMA, self.tr('Gaussian sigma (≈ 1–1.5 pixel)'),
                  compat.NUMBER_DOUBLE, DEFAULT_SIGMA, 0.0)
        self._num(self.DP_TOLERANCE, self.tr('Douglas-Peucker tolerance'),
                  compat.NUMBER_DOUBLE, DEFAULTS['dp_tolerance'], 0.0)
        self._num(self.POINTS_PER_100, self.tr('B-Spline/Bezier output points per 100 distance units'),
                  compat.NUMBER_DOUBLE, DEFAULTS['points_per_100'], 0.01)
        self._num(self.DEGREE, self.tr('B-Spline degree'),
                  compat.NUMBER_INTEGER, DEFAULTS['degree'], 1, maximum=5)
        self._num(self.SPLINE_SMOOTHING, self.tr('B-Spline smoothing factor (s)'),
                  compat.NUMBER_DOUBLE, DEFAULTS['spline_smoothing'], 0.0)

    def _add_engine_parameters(self):
        self._num(self.TILE_SIZE, self.tr('Tile size (0 = automatic)'),
                  compat.NUMBER_DOUBLE, 0.0, 0.0, advanced=True)
        self._num(self.PRECISION, self.tr('Coordinate precision / snapping grid (0 = automatic)'),
                  compat.NUMBER_DOUBLE, 0.0, 0.0, advanced=True)
        self._num(self.WORKERS,
                  self.tr('Parallel worker processes (0 = automatic: 50–75 % of the idle CPU cores)'),
                  compat.NUMBER_INTEGER, 0, 0, advanced=True, maximum=os.cpu_count() or 1)
        self.addParameter(QgsProcessingParameterFile(
            self.WORK_DIR,
            self.tr('Work folder (automatic: same name as the output; reuse it to resume)'),
            behavior=compat.FILE_FOLDER, optional=True))

    def _add_outputs(self):
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr('Smoothed polygons'), type=compat.SOURCE_POLYGON))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_ARCS, self.tr('Topology arcs (LPoly/RPoly)'), type=compat.SOURCE_LINE,
            optional=True, createByDefault=False))

    def _unit_factor(self, parameters, context, crs, extent, feedback):
        """Layer CRS units per user distance unit (1.0 when the user works in CRS units)."""
        if self.parameterAsEnum(parameters, self.UNITS, context) != UNITS_METERS:
            return 1.0
        factor, note = units.crs_units_per_meter(crs, extent)
        if note:
            feedback.pushWarning(note)
        else:
            feedback.pushInfo(self.tr('Distances in meters → layer units × {:g}').format(factor))
        return factor

    def _dist(self, parameters, name, context, factor):
        return self.parameterAsDouble(parameters, name, context) * factor

    def _smooth_sig(self, parameters, context, factor):
        alg = smoothing.ALGORITHMS[self.parameterAsEnum(parameters, self.ALGORITHM, context)]
        num = lambda name: self.parameterAsDouble(parameters, name, context)  # noqa: E731
        values = {
            'iterations': self.parameterAsInt(parameters, self.ITERATIONS, context),
            'pre_simplify': num(self.PRE_SIMPLIFY),
            'post_simplify': num(self.POST_SIMPLIFY),
            'dp_tolerance': num(self.DP_TOLERANCE),
            'sigma': num(self.SIGMA),
            'points_per_100': num(self.POINTS_PER_100),
            'degree': self.parameterAsInt(parameters, self.DEGREE, context),
            'spline_smoothing': num(self.SPLINE_SMOOTHING),
        }
        return smooth_signature(alg, values, factor)

    def _too_large(self, parameters, what):
        """Message if a large job would write a temporary layer, else None."""
        for name in (self.OUTPUT, self.OUTPUT_ARCS):
            value = parameters.get(name)
            if name == self.OUTPUT_ARCS and value is None:
                continue
            if outputs.is_temporary(value):
                return self.tr(TOO_LARGE).format(
                    what=what, output=self.parameterDefinition(name).description())
        return None

    def _work_dir(self, parameters, context, feedback):
        """``(folder, delete_afterwards)``: the user's folder, else one with the same name
        as the output file (created if missing, kept), else a temporary one."""
        path = self.parameterAsFile(parameters, self.WORK_DIR, context)
        auto = False
        if not path:
            path = outputs.work_folder_for(parameters.get(self.OUTPUT))
            auto = bool(path)
        if path:
            if not os.path.isdir(path):
                os.makedirs(path)
            feedback.pushInfo(self.tr('Work folder{}: {}').format(
                self.tr(' (same name as the output; kept to resume or re-smooth faster — '
                        'delete it when no longer needed)') if auto else '', path))
            return path, False
        return tempfile.mkdtemp(prefix='topo_smooth_', dir=QgsProcessingUtils.tempFolder()), True

    def _pause_start(self):
        """PauseControl for this run + the Pause/Resume button in the dialog (if any)."""
        control = PauseControl()
        ui = pause_ui.instance()
        if ui is not None:
            ui.install.emit(control, self.id())
        return control

    @staticmethod
    def _pause_end(control):
        ui = pause_ui.instance()
        if ui is not None:
            ui.remove.emit(control)

    # ── output helpers ───────────────────────────────────────────────────────
    def _tracker(self, parameters, feedback, before=()):
        steps = list(before) + list(PIPELINE_STEPS) + ['output']
        if parameters.get(self.OUTPUT_ARCS) is not None:
            steps.append('arcs')
        tracker = StepTracker(feedback, steps)
        feedback.pushInfo(self.tr('{} steps: {}').format(len(steps), ' → '.join(steps)))
        return tracker

    def _write_arcs(self, pipe, parameters, context, crs, feedback):
        if parameters.get(self.OUTPUT_ARCS) is None:
            return None
        tracker = pipe.tracker
        tracker.start('arcs')
        n_total = max(pipe.summary()['arcs'], 1)
        n = 0
        fields = QgsFields()
        for name in ('arc_id', 'lpoly_id', 'rpoly_id', 'smoothed'):
            fields.append(compat.make_field(name, 'int'))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT_ARCS, context, fields,
                                          compat.WKB_LINESTRING, crs)
        if sink is None:
            return None
        for arc_id, lp, rp, state, wkb in pipe.iter_arcs():
            if feedback.isCanceled():
                break
            n += 1
            if n % 2000 == 0:
                tracker.update('arcs', n / float(n_total), '{:,} / {:,} arcs'.format(n, n_total))
            f = QgsFeature(fields)
            g = QgsGeometry()
            g.fromWkb(wkb)
            f.setGeometry(g)
            f.setAttributes([arc_id, lp, rp, 1 if state in (ARC_SMOOTHED, ARC_FALLBACK, ARC_FALLBACK2) else 0])
            sink.addFeature(f, compat.SINK_FAST_INSERT)
        tracker.done('arcs', '{:,} arcs with lpoly_id / rpoly_id'.format(n))
        return dest

    @staticmethod
    def _emit(sink, fields, attrs, wkb, multi):
        g = QgsGeometry()
        g.fromWkb(wkb)
        if multi:
            if not g.isMultipart():
                g.convertToMultiType()
            geoms = [g]
        elif g.isMultipart():
            geoms = g.asGeometryCollection()
        else:
            geoms = [g]
        for part in geoms:
            f = QgsFeature(fields)
            f.setGeometry(part)
            f.setAttributes(attrs)
            if not sink.addFeature(f, compat.SINK_FAST_INSERT):
                raise QgsProcessingException('Could not write an output feature.')

    def _report(self, summary, feedback):
        summary = dict(summary, arcs_gentle=summary['arcs_fallback'] + summary['arcs_fallback2'])
        feedback.pushInfo(self.tr(
            'Done: {polygons:,} polygons, {arcs:,} arcs ({shared_arcs:,} shared), '
            '{arcs_smoothed:,} smoothed, {arcs_gentle:,} re-smoothed more gently, '
            '{arcs_reverted:,} reverted by repair, '
            '{polygons_original:,} polygon(s) kept original geometry, {seconds} s').format(**summary))
        if summary.get('duplicate_edges'):
            feedback.reportError(self.tr(
                '{:,} duplicated edges found: the input has overlapping polygons, so it is '
                'not a clean coverage; results along those edges may be wrong.').format(
                summary['duplicate_edges']))

    def _finish(self, pipe, work, is_temp):
        if pipe is not None:
            pipe.close()
        if is_temp:
            shutil.rmtree(work, ignore_errors=True)


class TopologySmoothAlgorithm(_TopologyAlgorithm):
    """A) Smooth Polygons."""

    INPUT = 'INPUT'
    KEEP_OUTER = 'KEEP_OUTER'
    SNAP_TOLERANCE = 'SNAP_TOLERANCE'

    def name(self):
        return 'smooth_polygons'

    def displayName(self):
        return self.tr('Smooth Polygons')

    def shortHelpString(self):
        return self._help_image('logo') + self.tr(
            'Smooths the jagged outlines of a polygon layer — for example polygons made from a '
            'classified satellite image — while neighbouring polygons stay perfectly joined: '
            'no gaps or overlaps appear between them, and holes are smoothed just like outer '
            'edges. All attributes are kept.\n\n') + self._workflow('workflow_polygons') + self.tr(
            '<b>Input:</b> a polygon layer whose polygons do not overlap each other, such as a '
            'land-cover or forest-type map.\n\n'
            '<b>Smoothing algorithm</b> (best first for pixel-shaped edges):\n'
            '• <b>Gaussian</b> — smooth, even curves; recommended\n'
            '• <b>Chaikin</b> — rounds the corners\n'
            '• <b>B-Spline</b> / <b>Bezier</b> — flowing curves\n'
            '• <b>Douglas-Peucker</b> — only removes vertices (no curves)\n'
            'Only the settings used by the chosen method are shown.\n\n'
            '<b>How much smoothing:</b> the defaults suit Sentinel-2 maps (10 m pixels): '
            'Gaussian sigma 12.5 m. For other pixel sizes use about 1–1.5 × the pixel size '
            '(e.g. Landsat 30 m → sigma 35 m). A larger value gives smoother, more '
            'generalised outlines.\n\n'
            '<b>Distances</b> are entered in meters and converted to the units of the layer '
            'automatically (or choose "Layer CRS units" to enter them directly).\n\n'
            '<b>Keep outer boundary unchanged:</b> tick when the outside edge of the whole '
            'layer must stay exactly as it is, e.g. a study-area or administrative border.\n\n'
            '<b>Large layers</b> (about 50 000 polygons or more) are too large for a '
            'temporary output layer: choose a file for "Smoothed polygons" (e.g. .gpkg), '
            'otherwise the tool does not start.\n\n'
            '<b>Work folder:</b> filled in from the output name and created if needed '
            '(e.g. Forest.gpkg → folder Forest next to it). Progress is saved there, so a '
            'cancelled or interrupted run continues where it stopped when started again, and '
            'trying other smoothing settings is much faster. <b>Pause</b> (next to Cancel) '
            'frees the CPU for a while; <b>Resume</b> continues. Delete the work folder when '
            'it is no longer needed.\n\n'
            'Where smoothing would make a polygon invalid, only that spot is smoothed more '
            'gently, so every output polygon is valid.') + CREDIT

    def createInstance(self):
        return TopologySmoothAlgorithm()

    def checkParameterValues(self, parameters, context):
        """Refuse (in the dialog, before running) a large input with a temporary output."""
        ok, msg = super(TopologySmoothAlgorithm, self).checkParameterValues(parameters, context)
        if not ok:
            return ok, msg
        try:
            source = self.parameterAsSource(parameters, self.INPUT, context)
        except QgsProcessingException:
            return ok, msg
        if source is not None and source.featureCount() >= LARGE_OUTPUT_FEATURES:
            err = self._too_large(parameters, self.tr('Large input ({:,} polygons)').format(
                source.featureCount()))
            if err:
                return False, err
        return True, ''

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr('Polygon coverage'), [compat.SOURCE_POLYGON]))
        self._add_smoothing_parameters()
        self.addParameter(QgsProcessingParameterBoolean(
            self.KEEP_OUTER, self.tr('Keep outer boundary of the coverage unchanged'),
            defaultValue=False))
        self._num(self.SNAP_TOLERANCE, self.tr('Junction snapping tolerance (0 = automatic)'),
                  compat.NUMBER_DOUBLE, 0.0, 0.0, advanced=True)
        self._add_engine_parameters()
        self._add_outputs()

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        crs = source.sourceCrs()
        ext = source.sourceExtent()
        n_input = source.featureCount()
        if n_input >= LARGE_OUTPUT_FEATURES:
            err = self._too_large(parameters, self.tr('Large input ({:,} polygons)').format(n_input))
            if err:
                raise QgsProcessingException(err)
        factor = self._unit_factor(parameters, context, crs, ext, feedback)
        precision = self._dist(parameters, self.PRECISION, context, factor)
        if precision <= 0:
            precision = 1e-9 if crs.isGeographic() else 1e-4
        tol = self._dist(parameters, self.SNAP_TOLERANCE, context, factor)
        if tol <= 0:
            tol = 3 * precision
        if ext.isNull() or ext.isEmpty() or not math.isfinite(ext.xMinimum()):
            origin = (0.0, 0.0)
        else:
            origin = (math.floor(ext.xMinimum()), math.floor(ext.yMinimum()))
        keep_outer = self.parameterAsBool(parameters, self.KEEP_OUTER, context)
        tile_size = self._dist(parameters, self.TILE_SIZE, context, factor)
        topo_sig = {
            'tool': 'vector',
            'input': self.parameterDefinition(self.INPUT).valueAsPythonString(
                parameters[self.INPUT], context),
            'count': source.featureCount(),
            'extent': ext.toString(),
            'crs': crs.authid() or crs.toWkt(),
            'precision': precision, 'tol': tol, 'keep_outer': keep_outer,
            'tile_size': tile_size,
        }
        smooth_sig = self._smooth_sig(parameters, context, factor)

        work, is_temp = self._work_dir(parameters, context, feedback)
        pipe = None
        pause = self._pause_start()
        try:
            pipe = SmoothPipeline(work, feedback, self._tracker(parameters, feedback),
                                  backend=QgisGeosBackend(),
                                  workers=self.parameterAsInt(parameters, self.WORKERS, context),
                                  pause=pause)
            pipe.configure(topo_sig, smooth_sig, origin, precision, tol,
                           topology.FIXED_OUTER if keep_outer else topology.FIXED_NONE,
                           tile_size)

            def records():
                req = QgsFeatureRequest()
                req.setNoAttributes()
                n_invalid = [0]
                for f in source.getFeatures(req):
                    g = f.geometry()
                    if g is None or g.isNull() or g.isEmpty():
                        continue
                    if compat.is_curved(g.wkbType()):
                        g = QgsGeometry(g.constGet().segmentize())
                    if not g.isGeosValid():
                        n_invalid[0] += 1
                        g = g.makeValid()
                    yield f.id(), None, bytes(g.asWkb())
                if n_invalid[0]:
                    feedback.pushWarning(self.tr(
                        '{:,} invalid input geometries were repaired with makeValid().').format(
                        n_invalid[0]))

            summary = pipe.run(records, source.featureCount())

            # ── output: original attributes joined back by feature id ────────
            fields = source.fields()
            multi = compat.is_multi(source.wkbType())
            sink, dest = self.parameterAsSink(
                parameters, self.OUTPUT, context, fields,
                compat.WKB_MULTIPOLYGON if multi else compat.WKB_POLYGON, crs)
            if sink is None:
                raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
            n_total = max(summary['polygons'], 1)
            batch = []
            n_done = [0]
            pipe.tracker.start('output')

            def flush():
                fids = [r[1] for r in batch]
                req = QgsFeatureRequest().setFilterFids(fids)
                req.setFlags(compat.REQUEST_NO_GEOMETRY)
                attrs = dict((f.id(), f.attributes()) for f in source.getFeatures(req))
                for _, fid, _, _, wkb in batch:
                    if wkb is not None:
                        self._emit(sink, fields, attrs.get(fid, []), wkb, multi)
                n_done[0] += len(batch)
                pipe.tracker.update('output', n_done[0] / float(n_total),
                                    '{:,} / {:,} features'.format(n_done[0], n_total))
                del batch[:]

            for row in pipe.iter_results():
                if feedback.isCanceled():
                    return {}
                batch.append(row)
                if len(batch) >= 1000:
                    flush()
            if batch:
                flush()
            pipe.tracker.done('output', '{:,} features with the source attributes'.format(
                n_done[0]))
            results = {self.OUTPUT: dest}
            arcs_dest = self._write_arcs(pipe, parameters, context, crs, feedback)
            if arcs_dest is not None:
                results[self.OUTPUT_ARCS] = arcs_dest
            self._report(summary, feedback)
            return results
        except Canceled:
            feedback.pushInfo(self.tr('Canceled. Progress is kept in the work folder.')
                              if not is_temp else self.tr('Canceled.'))
            return {}
        except ValueError as err:
            raise QgsProcessingException(str(err))
        finally:
            self._pause_end(pause)
            self._finish(pipe, work, is_temp)


class SmoothPolygonizeAlgorithm(_TopologyAlgorithm):
    """B) Smooth Polygonization."""

    INPUT = 'INPUT'
    BAND = 'BAND'
    EIGHT_CONNECTEDNESS = 'EIGHT_CONNECTEDNESS'
    SIEVE = 'SIEVE'
    KEEP_EXTENT = 'KEEP_EXTENT'

    def name(self):
        return 'smooth_polygonization'

    def displayName(self):
        return self.tr('Smooth Polygonization')

    def shortHelpString(self):
        return self._help_image('logo') + self.tr(
            'Turns a classified raster — for example a land-cover classification from '
            'Sentinel-2 — into smooth polygons in one step. Neighbouring polygons stay '
            'perfectly joined: no gaps or overlaps between classes, and holes are smoothed '
            'just like outer edges. Each polygon gets its class in the field '
            '<i>class_value</i>; nodata areas stay empty.\n\n') + self._workflow(
            'workflow_polygonization') + self.tr(
            '<b>Input:</b> a raster with whole-number class values (any integer type, e.g. '
            'Byte/UInt8, Int16, UInt16, Int32). Rasters with decimal values are not accepted — '
            'convert them first with Raster › Conversion › Translate (choose an integer data '
            'type). Class values must fit in 32 bits.\n\n'
            '<b>Remove regions smaller than:</b> merges patches below this number of pixels '
            'into their neighbours before the polygons are made (0 = keep everything). '
            '<b>8-connectedness</b> joins pixels that touch only at a corner.\n\n'
            '<b>Smoothing algorithm</b> (best first for pixel-shaped edges):\n'
            '• <b>Gaussian</b> — smooth, even curves; recommended\n'
            '• <b>Chaikin</b> — rounds the corners\n'
            '• <b>B-Spline</b> / <b>Bezier</b> — flowing curves\n'
            '• <b>Douglas-Peucker</b> — only removes vertices (no curves)\n'
            'Only the settings used by the chosen method are shown.\n\n'
            '<b>How much smoothing:</b> the defaults suit Sentinel-2 (10 m pixels): Gaussian '
            'sigma 12.5 m. For other pixel sizes use about 1–1.5 × the pixel size (e.g. '
            'Landsat 30 m → sigma 35 m).\n\n'
            '<b>Distances</b> are entered in meters and converted to the units of the raster '
            'automatically (or choose "Layer CRS units").\n\n'
            '<b>Keep raster extent edges straight:</b> the outer edge of the raster stays a '
            'straight line instead of being smoothed.\n\n'
            '<b>Large rasters</b> (about 10 million pixels or 50 000 polygons or more) are '
            'too large for a temporary output layer: choose a file for "Smoothed polygons" '
            '(e.g. .gpkg), otherwise the tool does not start.\n\n'
            '<b>Work folder:</b> filled in from the output name and created if needed '
            '(e.g. Forest.gpkg → folder Forest next to it). Progress is saved there, so a '
            'cancelled or interrupted run continues where it stopped when started again, and '
            're-smoothing with other settings skips the polygon conversion. <b>Pause</b> (next '
            'to Cancel) frees the CPU for a while; <b>Resume</b> continues. Delete the work '
            'folder when it is no longer needed.\n\n'
            'Where smoothing would make a polygon invalid, only that spot is smoothed more '
            'gently, so every output polygon is valid.') + CREDIT

    def createInstance(self):
        return SmoothPolygonizeAlgorithm()

    def checkParameterValues(self, parameters, context):
        """Warn in the dialog (before running) when the band is not an integer type."""
        ok, msg = super(SmoothPolygonizeAlgorithm, self).checkParameterValues(parameters, context)
        if not ok:
            return ok, msg
        from .geovanguard import raster as rst
        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if layer is None:
            return ok, msg
        try:
            ds = rst.open_raster(layer.source().split('|')[0],
                                 self.parameterAsInt(parameters, self.BAND, context))
            err = rst.integer_error(ds, self.parameterAsInt(parameters, self.BAND, context))
        except ValueError as e:
            return False, str(e)
        if err:
            return False, self.tr(err)
        if ds.RasterXSize * ds.RasterYSize >= LARGE_RASTER_PIXELS:
            err = self._too_large(parameters, self.tr(
                'Large raster ({:,} × {:,} = {:,} pixels)').format(
                    ds.RasterXSize, ds.RasterYSize, ds.RasterXSize * ds.RasterYSize))
            if err:
                return False, err
        return True, ''

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.INPUT, self.tr('Classified raster (integer)')))
        self.addParameter(QgsProcessingParameterBand(
            self.BAND, self.tr('Band'), defaultValue=1, parentLayerParameterName=self.INPUT))
        self._num(self.SIEVE, self.tr('Remove regions smaller than (pixels, 0 = off)'),
                  compat.NUMBER_INTEGER, 0, 0)
        self.addParameter(QgsProcessingParameterBoolean(
            self.EIGHT_CONNECTEDNESS, self.tr('Use 8-connectedness'), defaultValue=False))
        self._add_smoothing_parameters()
        self.addParameter(QgsProcessingParameterBoolean(
            self.KEEP_EXTENT, self.tr('Keep raster extent edges straight'), defaultValue=True))
        self._add_engine_parameters()
        self._add_outputs()

    def processAlgorithm(self, parameters, context, feedback):
        from .geovanguard import raster as rst

        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if layer is None:
            raise QgsProcessingException(self.invalidRasterError(parameters, self.INPUT))
        path = layer.source().split('|')[0]
        band_no = self.parameterAsInt(parameters, self.BAND, context)
        sieve_px = self.parameterAsInt(parameters, self.SIEVE, context)
        eight = self.parameterAsBool(parameters, self.EIGHT_CONNECTEDNESS, context)
        keep_extent = self.parameterAsBool(parameters, self.KEEP_EXTENT, context)
        try:
            ds = rst.open_raster(path, band_no)
            info = rst.RasterInfo(ds, band_no)
        except ValueError as err:
            raise QgsProcessingException(str(err))
        err = rst.integer_error(ds, band_no)
        if err:
            raise QgsProcessingException(self.tr(err))

        if info.width * info.height >= LARGE_RASTER_PIXELS:
            err = self._too_large(parameters, self.tr(
                'Large raster ({:,} × {:,} = {:,} pixels)').format(
                    info.width, info.height, info.width * info.height))
            if err:
                raise QgsProcessingException(err)
        factor = self._unit_factor(parameters, context, layer.crs(), layer.extent(), feedback)
        tile_size = self._dist(parameters, self.TILE_SIZE, context, factor)
        precision = self._dist(parameters, self.PRECISION, context, factor)
        if precision <= 0:
            precision = info.pixel / 1000.0
        origin = (info.xmin, info.ymin)
        extent_q = (0, 0, int(round((info.xmax - info.xmin) / precision)),
                    int(round((info.ymax - info.ymin) / precision)))
        raster_sig = {'path': os.path.abspath(path), 'mtime': os.path.getmtime(path)
                      if os.path.exists(path) else None, 'band': band_no,
                      'sieve': sieve_px, 'eight': eight}
        topo_sig = {'tool': 'raster', 'raster': raster_sig, 'precision': precision,
                    'keep_extent': keep_extent, 'tile_size': tile_size}
        smooth_sig = self._smooth_sig(parameters, context, factor)

        work, is_temp = self._work_dir(parameters, context, feedback)
        pipe = None
        pause = self._pause_start()
        try:
            tracker = self._tracker(parameters, feedback,
                                    (['sieve'] if sieve_px > 0 else []) + ['polygonize'])
            pipe = SmoothPipeline(work, feedback, tracker, backend=QgisGeosBackend(),
                                  workers=self.parameterAsInt(parameters, self.WORKERS, context),
                                  pause=pause)
            store = pipe.store
            gpkg = os.path.join(work, 'polygonized.gpkg')
            if (store.get('raster_sig') != raster_sig or not store.done('polygonize')
                    or not os.path.exists(gpkg)):
                store.set('raster_sig', None)
                store.con.execute("DELETE FROM meta WHERE key='done:polygonize'")
                store.con.commit()
                src = ds
                try:
                    if sieve_px > 0:
                        tracker.start('sieve', '{} px'.format(sieve_px))
                        src = rst.sieve(ds, band_no, sieve_px, eight,
                                        os.path.join(work, 'sieved.tif'), feedback,
                                        lambda f: tracker.update('sieve', f), pause)
                        tracker.done('sieve', 'regions < {} px merged into neighbours'.format(
                            sieve_px))
                    if feedback.isCanceled():
                        raise Canceled()
                    tracker.start('polygonize', '{:,} × {:,} px'.format(info.width, info.height))
                    n = rst.polygonize(src, 1 if sieve_px > 0 else band_no, gpkg, eight,
                                       feedback, lambda f: tracker.update(
                                           'polygonize', f, '{:,} × {:,} px'.format(
                                               info.width, info.height)), pause)
                except (RuntimeError, ValueError) as err:
                    # GDAL raises (exceptions enabled) or returns an error code on cancel
                    if feedback.isCanceled():
                        raise Canceled()
                    raise ValueError(str(err))
                if feedback.isCanceled():
                    raise Canceled()
                src = None
                tracker.done('polygonize', '{:,} raw polygons'.format(n))
                store.set('raster_sig', raster_sig)
                store.mark_done('polygonize')
            else:
                for step in (['sieve'] if sieve_px > 0 else []) + ['polygonize']:
                    tracker.done(step, resumed=True)
            ds = None

            pipe.configure(topo_sig, smooth_sig, origin, precision, 0.0,
                           topology.FIXED_EXTENT if keep_extent else topology.FIXED_NONE,
                           tile_size, extent_q)

            def records():
                return rst.iter_polygons(gpkg)

            from osgeo import ogr
            _ds = ogr.Open(gpkg)
            n_hint = _ds.GetLayer(0).GetFeatureCount()
            _ds = None
            if n_hint >= LARGE_OUTPUT_FEATURES:
                # a small raster can still give very many polygons
                err = self._too_large(parameters, self.tr('Large result ({:,} polygons)').format(
                    n_hint))
                if err:
                    raise QgsProcessingException(err)
            summary = pipe.run(records, n_hint)

            fields = QgsFields()
            fields.append(compat.make_field('class_value', 'int' if info.is_integer else 'double'))
            crs = layer.crs()
            sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                              compat.WKB_POLYGON, crs)
            if sink is None:
                raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
            n_total = max(summary['polygons'], 1)
            tracker.start('output')
            k = 0
            for k, (_, _, attr, _, wkb) in enumerate(pipe.iter_results(), 1):
                if k % 1000 == 0:
                    if feedback.isCanceled():
                        return {}
                    tracker.update('output', k / float(n_total),
                                   '{:,} / {:,} features'.format(k, n_total))
                if wkb is not None:
                    self._emit(sink, fields, [attr], wkb, False)
            tracker.done('output', '{:,} polygons with class_value'.format(k))
            results = {self.OUTPUT: dest}
            arcs_dest = self._write_arcs(pipe, parameters, context, crs, feedback)
            if arcs_dest is not None:
                results[self.OUTPUT_ARCS] = arcs_dest
            self._report(summary, feedback)
            return results
        except Canceled:
            feedback.pushInfo(self.tr('Canceled. Progress is kept in the work folder.')
                              if not is_temp else self.tr('Canceled.'))
            return {}
        except ValueError as err:
            raise QgsProcessingException(str(err))
        finally:
            ds = None
            self._pause_end(pause)
            self._finish(pipe, work, is_temp)
