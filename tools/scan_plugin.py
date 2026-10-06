"""Run the plugins.qgis.org blocking checks locally before uploading a ZIP.

    "C:\\Program Files\\QGIS 4.2.2\\bin\\python-qgis.bat" tools\\scan_plugin.py dist\\geovanguard_qgis-1.0.1.zip

1. Bandit (security, CRITICAL on plugins.qgis.org) — ``--bandit`` is the bandit
   executable (default: ``bandit`` on PATH), e.g. from its own venv:
       python -m venv %TEMP%\\bandit_env && %TEMP%\\bandit_env\\Scripts\\pip install bandit
       ... --bandit %TEMP%\\bandit_env\\Scripts\\bandit.exe
   A bandit that does not run is an error, never "no issues".
2. QGIS Qt6 check — QGIS's own ``pyqt5_to_pyqt6.py --dry_run`` (needs PyQt6, i.e.
   the QGIS 4 Python, and the small ``tokenize-rt`` package; ``--qt6-script``,
   ``--tokenize-rt`` give their locations).  It is downloaded from the QGIS
   repository when no path is given.

Exit code 1 when either reports anything.
"""
import argparse
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

QT6_URL = ('https://raw.githubusercontent.com/qgis/QGIS/master/scripts/pyqt5_to_pyqt6/'
           'pyqt5_to_pyqt6.py')


def bandit(command, folder):
    """``command``: the bandit executable (e.g. <venv>/Scripts/bandit.exe)."""
    # the QGIS Python environment (PYTHON* variables, its folders on PATH) would make
    # another interpreter start QGIS' Python instead: give bandit a clean one
    qgis_root = os.path.normcase(os.path.dirname(os.path.dirname(sys.executable)))
    env = dict((k, v) for k, v in os.environ.items()
               if not k.upper().startswith(('PYTHON', '__PYVENV')))
    env['PATH'] = os.pathsep.join(p for p in env.get('PATH', '').split(os.pathsep)
                                  if not os.path.normcase(p).startswith(qgis_root))
    out = subprocess.run([command, '-r', '.', '-f', 'custom',
                          '--msg-template', '{relpath}:{line} [{test_id} {severity}] {msg}'],
                         capture_output=True, text=True, env=env, cwd=folder)
    # bandit exits 0 (clean) or 1 (issues) and logs "running on Python …" when it
    # really ran; anything else is an error — never "no issues"
    ran = 'running on Python' in out.stderr
    if out.returncode not in (0, 1) or 'Traceback' in out.stderr or not ran:
        raise SystemExit('bandit did not run ({}):{}{}'.format(command, os.linesep,
                                                                out.stderr[-800:]))
    return [ln for ln in out.stdout.splitlines() if re.search(r'\[B\d+ ', ln)]


def qt6_check(folder, script, tokenize_rt):
    if not script:
        script = os.path.join(tempfile.mkdtemp(), 'pyqt5_to_pyqt6.py')
        urllib.request.urlretrieve(QT6_URL, script)       # QGIS project script  # nosec B310
    if tokenize_rt:
        sys.path.insert(0, tokenize_rt)
    log = os.path.join(tempfile.mkdtemp(), 'qt6.log')
    argv = sys.argv
    sys.argv = ['pyqt5_to_pyqt6.py', '--dry_run', '--logfile', log, folder]
    try:
        runpy.run_path(script, run_name='__main__')
    except SystemExit:
        pass                                              # its exit code: 1 when it would edit
    finally:
        sys.argv = argv
    with open(log, encoding='utf-8') as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith('===')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('zip')
    ap.add_argument('--bandit', default='bandit', help='bandit executable')
    ap.add_argument('--qt6-script')
    ap.add_argument('--tokenize-rt', help='folder that contains tokenize_rt.py')
    a = ap.parse_args()
    folder = tempfile.mkdtemp(prefix='scan_')
    try:
        zipfile.ZipFile(a.zip).extractall(folder)
        b = bandit(a.bandit, folder)
        q = qt6_check(folder, a.qt6_script, a.tokenize_rt)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print('Bandit: {} issue(s)'.format(len(b)))
    for ln in b:
        print('   ' + ln)
    print('Qt6 check: {} finding(s)'.format(len(q)))
    for ln in q:
        print('   ' + ln.replace(folder, ''))
    print('OK - no blocking findings' if not (b or q) else 'NOT READY')
    return 1 if (b or q) else 0


if __name__ == '__main__':
    sys.exit(main())
