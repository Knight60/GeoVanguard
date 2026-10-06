"""Pause / Resume button next to Cancel in the Processing dialog.

The algorithm runs in a background thread and must not touch widgets, so it
emits a signal to a QObject living in the GUI thread, which finds the running
dialog of that algorithm and inserts the button beside "Cancel" (same row as
the progress bar in QGIS 3.40 and 4.x).  If the dialog or the Cancel button
cannot be found (batch, model designer, qgis_process, a future QGIS layout),
nothing is added and the job runs as usual — Cancel + Run with the same work
folder still resumes from the last checkpoint.
"""
from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtWidgets import QApplication, QPushButton

BUTTON_NAME = 'geovanguardPauseButton'
_INSTANCE = None


def _find_layout(layout, widget):
    """(layout, index) holding ``widget`` somewhere below ``layout``."""
    if layout is None:
        return None
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.widget() is widget:
            return layout, i
        if item.layout() is not None:
            found = _find_layout(item.layout(), widget)
            if found:
                return found
    return None


def _dialog_of(button):
    """The Processing dialog / widget owning ``button`` (has ``algorithm()``)."""
    w = button.parentWidget()
    while w is not None:
        if callable(getattr(w, 'algorithm', None)):
            return w
        w = w.parentWidget()
    return None


class PauseButtons(QObject):
    install = pyqtSignal(object, str)      # (PauseControl, algorithm id)
    remove = pyqtSignal(object)

    def __init__(self):
        super(PauseButtons, self).__init__()
        self._buttons = {}
        self.install.connect(self._install)
        self.remove.connect(self._remove)

    def _candidates(self, alg_id):
        out = []
        for w in QApplication.allWidgets():
            if not isinstance(w, QPushButton) or w.objectName() != 'buttonCancel':
                continue
            dlg = _dialog_of(w)
            if dlg is None or dlg.findChild(QPushButton, BUTTON_NAME) is not None:
                continue
            try:
                if dlg.algorithm() is None or dlg.algorithm().id() != alg_id:
                    continue
            except RuntimeError:            # deleted C++ object
                continue
            out.append((w.isEnabled() and w.isVisible(), dlg, w))
        out.sort(key=lambda t: not t[0])    # the running (Cancel enabled) dialog first
        return out

    def _install(self, control, alg_id):
        found = self._candidates(alg_id)
        if not found:
            return
        _, dlg, cancel = found[0]
        place = _find_layout(dlg.layout(), cancel)
        if place is None:
            return
        layout, index = place
        button = QPushButton(self.tr('Pause'), cancel.parentWidget())
        button.setObjectName(BUTTON_NAME)
        button.setToolTip(self.tr('Pause the job and free the CPU; press Resume to continue '
                                  'where it stopped. Cancel still works while paused.'))

        def toggle():
            paused = control.toggle()
            button.setText(self.tr('Resume') if paused else self.tr('Pause'))

        button.clicked.connect(toggle)
        layout.insertWidget(index, button)
        self._buttons[id(control)] = button

    def _remove(self, control):
        button = self._buttons.pop(id(control), None)
        if button is None:
            return
        try:
            button.hide()
            button.deleteLater()
        except RuntimeError:                # the dialog is already gone
            pass


def init():
    """Create the GUI-thread helper (call from the GUI thread, e.g. provider load)."""
    global _INSTANCE
    if _INSTANCE is None and QApplication.instance() is not None:
        _INSTANCE = PauseButtons()
    return _INSTANCE


def instance():
    return _INSTANCE
