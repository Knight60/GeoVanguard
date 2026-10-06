"""Exact fingerprints (SHA-256 of every output WKB) of fixed runs.

Used to prove that a refactor or another geometry backend gives bit-identical
results.  Run with QGIS' Python:

    python-qgis.bat tests\\result_hashes.py OUT.json
    python-qgis.bat tests\\result_hashes.py OUT.json --compare BASELINE.json
"""
import hashlib
import json
import os
import sys

import run_qgis_tests as T  # noqa: E402  (starts QGIS + registers the provider)

DATA = os.path.join(T.HERE, 'data')


def digest(path):
    lyr = T.layer(path)
    wkbs = sorted(bytes(f.geometry().asWkb()) for f in lyr.getFeatures())
    h = hashlib.sha256()
    for w in wkbs:
        h.update(w)
    return {'features': len(wkbs), 'sha256': h.hexdigest()}


def cases():
    from geovanguard.core.smoothing import ALGORITHMS
    tif = os.path.join(T.TMP, 'cls.tif')
    T.make_raster(tif, size=300, seed=3)
    # real-data clips are kept out of the public repository: skipped when absent
    for name in ('region5_view', 'region5_riverbank'):
        if not os.path.exists(os.path.join(DATA, name + '.gpkg')):
            continue
        yield name + ' / Gaussian', T.ALG_A, {
            'INPUT': os.path.join(DATA, name + '.gpkg')}
        yield name + ' / Chaikin', T.ALG_A, {
            'INPUT': os.path.join(DATA, name + '.gpkg'), 'ALGORITHM': ALGORITHMS.index('Chaikin')}
    for i, alg in enumerate(ALGORITHMS):
        yield 'raster / ' + alg, T.ALG_B, {'INPUT': tif, 'BAND': 1, 'ALGORITHM': i}
    yield 'raster / Gaussian tile 400', T.ALG_B, {'INPUT': tif, 'BAND': 1, 'TILE_SIZE': 400}


WORKERS = int(sys.argv[sys.argv.index('--workers') + 1]) if '--workers' in sys.argv else None


def main():
    out_json = sys.argv[1]
    base = None
    if '--compare' in sys.argv:
        with open(sys.argv[sys.argv.index('--compare') + 1]) as f:
            base = json.load(f)
    result = {}
    for k, (label, alg, params) in enumerate(cases()):
        params = dict(params, OUTPUT=os.path.join(T.TMP, 'h{}.gpkg'.format(k)))
        if WORKERS is not None:
            params['WORKERS'] = WORKERS
        res, fb, sec = T.run(alg, params, expect_smoothing=False)
        result[label] = digest(res['OUTPUT'])
        done = [m for m in fb.log if m.startswith('Done') or 'repair' in m.lower() or 'cross' in m.lower()]
        same = '' if base is None else ('  SAME' if base.get(label) == result[label] else '  DIFFERENT')
        print('{:<34} {:>6} feat  {:6.1f}s  {}{}'.format(
            label, result[label]['features'], sec, result[label]['sha256'][:16], same))
        if '-v' in sys.argv:
            for m in done:
                print('      ' + m)
    with open(out_json, 'w') as f:
        json.dump(result, f, indent=1)
    T.app.exitQgis()
    if base is not None and any(base.get(k) != v for k, v in result.items()):
        print('RESULTS DIFFER FROM BASELINE')
        sys.exit(1)


if __name__ == '__main__':
    main()
