"""Check a plugin ZIP against the plugins.qgis.org upload rules (stdlib only).

    python tools/check_plugin_zip.py dist/geovanguard_qgis-1.0.0.zip

Mirrors the validator of the QGIS plugin repository: one top-level package
folder, metadata.txt with the mandatory fields (incl. a repository URL),
classFactory in __init__.py, a LICENSE file, no compiled/hidden/VCS files,
size limit.  Exit code 1 if anything mandatory fails.
"""
import configparser
import os
import re
import sys
import zipfile

MAX_BYTES = 25 * 1024 * 1024
REQUIRED = ('name', 'qgisMinimumVersion', 'description', 'about', 'version', 'author',
            'email', 'repository')
RECOMMENDED = ('tracker', 'homepage', 'icon', 'tags', 'changelog', 'qgisMaximumVersion')
FORBIDDEN = re.compile(r'(__pycache__|\.pyc$|\.pyo$|(^|/)\.git(/|$)|__MACOSX|\.DS_Store|'
                       r'Thumbs\.db|\.exe$|\.dll$|\.so$|\.pyd$)')


def main(path):
    errors, warnings = [], []
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        errors.append('ZIP is {:.1f} MB (> 25 MB)'.format(size / 1048576.0))
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if not n.endswith('/')]
    tops = {n.split('/')[0] for n in z.namelist()}
    if len(tops) != 1:
        errors.append('ZIP must contain exactly one top-level folder, found: {}'.format(sorted(tops)))
    top = sorted(tops)[0]
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', top):
        errors.append('package folder {!r} is not a valid Python identifier'.format(top))
    bad = [n for n in z.namelist() if FORBIDDEN.search(n)]
    if bad:
        errors.append('forbidden files: {}'.format(bad[:10]))

    meta_name = top + '/metadata.txt'
    if meta_name not in names:
        errors.append('metadata.txt missing')
        meta = {}
    else:
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        cp.read_string(z.read(meta_name).decode('utf-8'))
        meta = dict(cp['general']) if cp.has_section('general') else {}
        if not meta:
            errors.append('metadata.txt has no [general] section')
    for key in REQUIRED:
        if not meta.get(key, '').strip():
            errors.append('metadata: mandatory "{}" missing'.format(key))
    for key in RECOMMENDED:
        if not meta.get(key, '').strip():
            warnings.append('metadata: recommended "{}" missing'.format(key))
    if meta.get('version') and not re.match(r'^\d+(\.\d+){1,3}$', meta['version']):
        errors.append('version {!r} is not numeric (e.g. 1.0.0)'.format(meta['version']))
    for key in ('repository', 'tracker', 'homepage'):
        if meta.get(key) and not meta[key].startswith('https://'):
            errors.append('{} must be an https URL'.format(key))
    if meta.get('icon') and top + '/' + meta['icon'] not in names:
        errors.append('icon file {!r} not in the ZIP'.format(meta['icon']))
    if str(meta.get('experimental', '')).lower() in ('true', 'yes', '1'):
        warnings.append('experimental=True: hidden unless users enable experimental plugins')

    init = top + '/__init__.py'
    if init not in names:
        errors.append('__init__.py missing')
    elif 'def classFactory' not in z.read(init).decode('utf-8'):
        errors.append('__init__.py has no classFactory(iface)')
    if not any(n.split('/')[-1].upper().startswith(('LICENSE', 'COPYING')) for n in names
               if n.count('/') == 1):
        errors.append('LICENSE file missing at the package root')

    for n in names:
        if n.endswith('.py'):
            try:
                compile(z.read(n).decode('utf-8'), n, 'exec')
            except SyntaxError as e:
                errors.append('syntax error in {}: {}'.format(n, e))

    print('{}  ({:.0f} kB, {} files, package {!r}, version {})'.format(
        os.path.basename(path), size / 1024.0, len(names), top, meta.get('version')))
    for w in warnings:
        print('  warning: ' + w)
    for e in errors:
        print('  ERROR:   ' + e)
    print('OK - ready for plugins.qgis.org' if not errors else 'NOT READY')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
