"""Best parameter per tool and dataset type from tune.csv (highest mIoU on the A set).

usage: python pick_best.py <tune.csv> <best.json>
"""
import csv
import json
import sys

best = {}
for r in csv.DictReader(open(sys.argv[1])):
    if r['tool'] == 'input' or r['seconds'] == 'FAILED' or not r['mIoU']:
        continue
    kind = r['dataset'].rsplit('_', 1)[0]
    key = (kind, r['tool'])
    if key not in best or float(r['mIoU']) > best[key][1]:
        best[key] = (r['param'], float(r['mIoU']))
out = {}
for (kind, tool), (p, v) in sorted(best.items()):
    out.setdefault(kind, {})[tool] = {'param': p, 'mIoU_A': v}
    print('%-12s %-24s %-10s %.5f' % (kind, tool, p, v))
json.dump(out, open(sys.argv[2], 'w'), indent=1)
