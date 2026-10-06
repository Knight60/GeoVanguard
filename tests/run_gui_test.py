"""GUI test: the method combo shows only the parameters of the selected method.

    "C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" tests\run_gui_test.py
"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qgis.core import QgsApplication
app = QgsApplication([], True); app.initQgis()
sys.path.append(os.path.join(QgsApplication.prefixPath(), 'python', 'plugins'))
from processing.core.Processing import Processing; Processing.initialize()
from geovanguard_qgis.provider import GeoVanguardProvider
P = GeoVanguardProvider(); QgsApplication.processingRegistry().addProvider(P)
try:
    from processing.gui.AlgorithmDialog import AlgorithmDialog
except ImportError:                       # QGIS 4
    from processing.gui.algorithm_widget import AlgorithmWidget as AlgorithmDialog
from geovanguard_qgis.gui_wrappers import RELEVANT, METHOD_PARAMS, _parts
from geovanguard.core.smoothing import ALGORITHMS
from qgis.PyQt.QtCore import Qt
ok = True
for alg_id in ('geovanguard:smooth_polygons', 'geovanguard:smooth_polygonization'):
    alg = QgsApplication.processingRegistry().createAlgorithmById(alg_id)
    dlg = AlgorithmDialog(alg); dlg.show()
    panel = dlg.mainWidget(); wrappers = panel.wrappers
    method = wrappers['ALGORITHM']
    print(alg_id, '| wrapper:', type(method).__name__, '| default:', ALGORITHMS[method.widget.currentIndex()])
    for i, name in enumerate(ALGORITHMS):
        method.widget.setCurrentIndex(i); app.processEvents()
        vis = sorted(p for p in METHOD_PARAMS if _parts(wrappers[p])[0].isVisible())
        good = vis == sorted(RELEVANT[name])
        ok &= good
        print('   %-16s shown: %s %s' % (name, vis, '' if good else '<-- WRONG'))
    assert alg.parameterDefinition('ALGORITHM') is not None
    method.widget.setCurrentIndex(1); print('   value() for B-Spline ->', method.value())
    # the workflow picture in the help panel is found and loaded
    import re
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QTextDocument
    from qgis.PyQt.QtWidgets import QTextBrowser
    kind = getattr(QTextDocument, 'ImageResource', None) or QTextDocument.ResourceType.ImageResource
    imgs = [(tb, src) for tb in dlg.findChildren(QTextBrowser)
            for src in re.findall(r'<img src="([^"]+)"', tb.toHtml())]
    loaded = len(imgs) >= 2 and all(not tb.document().resource(kind, QUrl(src)).isNull()
                                for tb, src in imgs)
    ok &= loaded
    print('   help workflow image:', [os.path.basename(s) for _, s in imgs], 'loaded' if loaded else '<-- MISSING')
    # logo on top, then the centred title
    app.processEvents()
    doc = dlg.findChild(QTextBrowser, 'textShortHelp').document()
    b0, b1 = doc.firstBlock(), doc.firstBlock().next()
    center = int(getattr(Qt, 'AlignHCenter', None) or Qt.AlignmentFlag.AlignHCenter)
    top_ok = (b0.text() == '\ufffc' and b1.text() == alg.displayName()
              and int(b0.blockFormat().alignment()) & center and int(b1.blockFormat().alignment()) & center)
    ok &= bool(top_ok)
    print('   help top: logo then centred title', bool(top_ok), '' if top_ok else repr((b0.text(), b1.text())))
    # Pause / Resume button beside Cancel
    from qgis.PyQt.QtWidgets import QPushButton
    from geovanguard_qgis import pause_ui
    from geovanguard_qgis.geovanguard.pause import PauseControl
    control = PauseControl()
    pause_ui.init().install.emit(control, alg_id)
    app.processEvents()
    btn = dlg.findChild(QPushButton, pause_ui.BUTTON_NAME)
    cancel = dlg.findChild(QPushButton, 'buttonCancel')
    beside = False
    if btn is not None:
        place = pause_ui._find_layout(dlg.layout(), cancel)
        beside = place is not None and place[0].indexOf(btn) == place[1] - 1
        btn.click()
        paused_ok = control.is_paused() and btn.text() == 'Resume'
        btn.click()
        resumed_ok = not control.is_paused() and btn.text() == 'Pause'
    else:
        paused_ok = resumed_ok = False
    pause_ui.instance().remove.emit(control)
    app.processEvents()
    app.sendPostedEvents(None, 0)
    gone = dlg.findChild(QPushButton, pause_ui.BUTTON_NAME) is None or not btn.isVisible()
    good = btn is not None and beside and paused_ok and resumed_ok and gone
    ok &= good
    print('   pause button: beside Cancel', beside, '| pause', paused_ok, '| resume', resumed_ok,
          '| removed', gone, '' if good else '<-- WRONG')
    # order: outputs, work folder, Advanced Parameters; work folder follows the output
    from qgis.core import QgsProcessingContext
    from qgis.PyQt.QtWidgets import QGroupBox
    wd_widget, wd_label = _parts(wrappers['WORK_DIR'])
    out_widget = _parts(wrappers['OUTPUT'])[0]
    arcs_widget = _parts(wrappers['OUTPUT_ARCS'])[0]
    lay = wd_widget.parentWidget().layout()
    adv = wd_widget.parentWidget().findChild(QGroupBox, 'grpAdvanced')
    pos = [lay.indexOf(w) for w in (out_widget, arcs_widget, wd_label, wd_widget, adv)]
    order_ok = all(p >= 0 for p in pos) and pos == sorted(pos)
    target = os.path.join(os.environ.get('TEMP', '/tmp'), 'gv_gui', 'Forest.gpkg')
    wrappers['OUTPUT'].setParameterValue(target, QgsProcessingContext())
    app.processEvents()
    filled = str(wrappers['WORK_DIR'].parameterValue() or '')
    fill_ok = os.path.normcase(filled) == os.path.normcase(target[:-5])
    wrappers['WORK_DIR'].setParameterValue('D:/my/own', QgsProcessingContext())
    wrappers['OUTPUT'].setParameterValue(target.replace('Forest', 'Other'), QgsProcessingContext())
    app.processEvents()
    keep_ok = str(wrappers['WORK_DIR'].parameterValue()) == 'D:/my/own'
    good = order_ok and fill_ok and keep_ok
    ok &= good
    print('   layout order', pos, order_ok, '| work folder auto', repr(filled), fill_ok,
          '| user folder kept', keep_ok, '' if good else '<-- WRONG')
    dlg.close()
print('GUI OK' if ok else 'GUI FAILED')
sys.exit(0 if ok else 1)
