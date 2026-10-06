"""Processing dialog widget for the smoothing-method parameter.

QGIS Processing builds a static form, so every method-specific parameter is
always shown.  This wrapper replaces the method combo box and, in the standard
algorithm dialog, shows only the parameters the selected method uses.  In the
batch dialog and the modeler it behaves like a plain combo box.

It also arranges the standard dialog: outputs → work folder → Advanced
Parameters (QGIS puts the outputs last), fills the work folder in from the
output name (Forest.gpkg → Forest) unless the user typed a folder of their own,
and in the help panel puts the GeoVanguard logo above the centred tool title.
Anything it cannot find is left as QGIS built it.
"""
from qgis.core import QgsProcessingContext
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QTextBlockFormat, QTextCharFormat, QTextCursor
from qgis.PyQt import sip
from qgis.PyQt.QtWidgets import QComboBox, QGroupBox, QTextBrowser

from processing.gui.wrappers import DIALOG_STANDARD, WidgetWrapper

from .geovanguard.core.smoothing import ALGORITHMS
from .outputs import work_folder_for

# parameters each method reads (names as defined in algorithms.py)
RELEVANT = {
    'Chaikin': ('ITERATIONS', 'PRE_SIMPLIFY', 'POST_SIMPLIFY'),
    'B-Spline': ('PRE_SIMPLIFY', 'POINTS_PER_100', 'DEGREE', 'SPLINE_SMOOTHING'),
    'Bezier': ('PRE_SIMPLIFY', 'POINTS_PER_100'),
    'Douglas-Peucker': ('DP_TOLERANCE',),
    'Gaussian': ('SIGMA', 'POST_SIMPLIFY'),
}
METHOD_PARAMS = sorted({p for names in RELEVANT.values() for p in names})


def _parts(wrapper):
    """Widget and label of a Python (legacy) or C++ parameter wrapper."""
    if isinstance(wrapper, WidgetWrapper):
        return wrapper.widget, wrapper.label
    return wrapper.wrappedWidget(), wrapper.wrappedLabel()


class SmoothingMethodWrapper(WidgetWrapper):

    def createWidget(self):
        self._others = None
        combo = QComboBox()
        for i, name in enumerate(ALGORITHMS):
            combo.addItem(name, i)
        combo.currentIndexChanged.connect(self._method_changed)
        return combo

    def setValue(self, value):
        try:
            idx = int(value)
        except (TypeError, ValueError):
            idx = ALGORITHMS.index(value) if value in ALGORITHMS else 0
        self.widget.setCurrentIndex(max(0, min(idx, len(ALGORITHMS) - 1)))

    def value(self):
        return self.widget.currentData()

    def postInitialize(self, wrappers):
        self._others = {}
        for w in wrappers:
            definition = w.parameterDefinition()
            if definition is not None:
                self._others[definition.name()] = w
        self._update_visibility()
        if self.dialogType == DIALOG_STANDARD:
            try:
                self._arrange()
                self._link_work_folder()
            except Exception:                   # noqa: BLE001 — layout is cosmetic
                pass
            # the help text is set by the dialog; decorate it once the dialog is built
            QTimer.singleShot(0, self._decorate_help)

    def _decorate_help(self):
        try:
            browser = self.widget.window().findChild(QTextBrowser, 'textShortHelp')
            if browser is not None:
                decorate_help(browser.document())
                browser.verticalScrollBar().setValue(0)
        except (RuntimeError, AttributeError):  # dialog already closed
            pass

    def _arrange(self):
        """Move the work folder after the outputs and Advanced Parameters to the end."""
        work = self._others.get('WORK_DIR')
        last = self._others.get('OUTPUT_ARCS') or self._others.get('OUTPUT')
        if work is None or last is None:
            return
        widget, label = _parts(work)
        anchor = _parts(last)[0]
        layout = widget.parentWidget().layout() if widget is not None else None
        if layout is None or layout.indexOf(widget) < 0 or layout.indexOf(anchor) < 0:
            return
        advanced = widget.parentWidget().findChild(QGroupBox, 'grpAdvanced')
        moving = [w for w in (label, widget, advanced) if w is not None and layout.indexOf(w) >= 0]
        for w in moving:
            layout.removeWidget(w)
        index = layout.indexOf(anchor) + 1
        for w in moving:
            layout.insertWidget(index, w)
            # removeWidget() handed ownership to Python; give it back to Qt, or the
            # widget is deleted with the last Python reference
            sip.transferto(w, None)
            index += 1

    def _link_work_folder(self):
        out = self._others.get('OUTPUT')
        work = self._others.get('WORK_DIR')
        if out is None or work is None or not hasattr(out, 'widgetValueHasChanged'):
            return
        self._auto_folder = ''
        out.widgetValueHasChanged.connect(lambda *args: self._output_changed())
        self._output_changed()

    @staticmethod
    def _value(wrapper):
        return wrapper.parameterValue() if hasattr(wrapper, 'parameterValue') else wrapper.value()

    def _output_changed(self):
        out = self._others['OUTPUT']
        work = self._others['WORK_DIR']
        current = str(self._value(work) or '')
        if current and current != self._auto_folder:
            return                              # the user chose a folder: keep it
        folder = work_folder_for(self._value(out)) or ''
        if folder == current:
            return
        self._auto_folder = folder
        if hasattr(work, 'setParameterValue'):
            work.setParameterValue(folder, QgsProcessingContext())
        else:
            work.setValue(folder)

    def _method_changed(self, *args):
        self._update_visibility()
        try:
            self.widgetValueHasChanged.emit(self)
        except (AttributeError, TypeError):
            pass

    def _update_visibility(self):
        if not self._others or self.dialogType != DIALOG_STANDARD:
            return
        keep = RELEVANT.get(ALGORITHMS[self.widget.currentIndex()], ())
        for name in METHOD_PARAMS:
            wrapper = self._others.get(name)
            if wrapper is None:
                continue
            for part in _parts(wrapper):
                if part is not None:
                    part.setVisible(name in keep)


def _enum(cls, flat, scoped):
    return getattr(cls, flat, None) or getattr(getattr(cls, scoped.split('.')[0]), scoped.split('.')[1])


def decorate_help(doc):
    """Logo (first picture of the help text, 'logo' in its name) above the title,
    both centred.  The title is the heading QGIS writes before the help text."""
    center = _enum(Qt, 'AlignHCenter', 'AlignmentFlag.AlignHCenter')
    title = doc.firstBlock()
    if not title.isValid() or title.blockFormat().headingLevel() == 0:
        return False
    cur = QTextCursor(doc)
    cur.beginEditBlock()
    try:
        logo = title.next()
        image = None
        if logo.isValid() and logo.text() == '\ufffc':
            c = QTextCursor(logo)
            c.movePosition(_enum(QTextCursor, 'NextCharacter', 'MoveOperation.NextCharacter'),
                           _enum(QTextCursor, 'KeepAnchor', 'MoveMode.KeepAnchor'))
            fmt = c.charFormat()
            if fmt.isImageFormat() and 'logo' in fmt.toImageFormat().name():
                image = fmt.toImageFormat()
                c = QTextCursor(logo)
                c.select(_enum(QTextCursor, 'BlockUnderCursor', 'SelectionType.BlockUnderCursor'))
                c.removeSelectedText()
        if image is not None:
            c = QTextCursor(doc)
            c.insertBlock()                     # new empty first block, title moves down
            first = doc.firstBlock()
            c = QTextCursor(first)
            block = QTextBlockFormat()
            block.setAlignment(center)
            c.setBlockFormat(block)
            c.setCharFormat(QTextCharFormat())
            c.insertImage(image)
            title = first.next()
        fmt = title.blockFormat()
        fmt.setAlignment(center)
        QTextCursor(title).setBlockFormat(fmt)
    finally:
        cur.endEditBlock()
    return True
