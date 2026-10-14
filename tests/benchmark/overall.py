"""Overall comparison from Tables 2-3: mean ranks per group of criteria, Pareto front, and
GeoVanguard vs ArcGIS Smooth Shared Edges criterion by criterion (QGIS Python: numpy, scipy)."""
import json
import os
from scipy.stats import rankdata
import numpy as np
T=['GeoVanguard','ArcGIS SSE','ArcGIS SP','QGIS','GRASS slide','GRASS Chaiken','Mapshaper']
K=['gv_gaussian','arc_sharededges','arc_smoothpolygon','qgis_smooth','grass_sliding_averaging','grass_chaiken','mapshaper']
miou=np.array([[.0241,.0172,.0151,.0051],[.0256,.0184,.0066,.0054],[.0256,.0183,.0140,.0052],[.0201,.0124,.0029,.0028],
               [.0160,.0081,-.0023,-.0002],[.0227,.0157,-.0023,.0043],[0,0,0,0]])
gap=np.array([0,0,6056,1458,0,1,0]); ovl=np.array([0,0,1313,242,0,0,0]); inv=np.array([0,0,0,1,67,67,0])
area=np.abs(np.array([.004,.176,.196,.440,.797,.708,0]))
S=json.load(open(os.path.join(os.environ.get('GV_BENCH', 'E:/tmp/gv_bench'), 'eval', 'shape.json')))
DS=['syn_multi_B','syn_single_B','ft_multi_B','ga_single_B']
sh=np.array([[[S[d][k][m] for d in DS] for k in K] for m in range(3)])  # metric, tool, ds
def r(v, high=False): return rankdata(-np.array(v) if high else np.array(v))   # 1 = best, ties averaged
groups={}
groups['accuracy (mIoU gain, 4 sets)']=np.mean([r(miou[:,j],True) for j in range(4)],axis=0)
groups['topology (gaps, overlaps, invalid)']=np.mean([r(gap),r(ovl),r(inv)],axis=0)
groups['area change']=r(area)
groups['shape (3 measures x 4 sets)']=np.mean([r(sh[0,:,j]) for j in range(4)]+[r(sh[1,:,j],True) for j in range(4)]+[r(sh[2,:,j]) for j in range(4)],axis=0)
allc=[r(miou[:,j],True) for j in range(4)]+[r(gap),r(ovl),r(inv),r(area)]+[r(sh[0,:,j]) for j in range(4)]+[r(sh[1,:,j],True) for j in range(4)]+[r(sh[2,:,j]) for j in range(4)]
print('%-14s'%'tool'+''.join('%9s'%g.split(' ')[0] for g in groups)+'  grp-mean  all-crit')
gm=np.mean(list(groups.values()),axis=0); ac=np.mean(allc,axis=0)
for i in np.argsort(gm):
    print('%-14s'%T[i]+''.join('%9.2f'%groups[g][i] for g in groups)+'%10.2f%10.2f'%(gm[i],ac[i]))
# Pareto over the 4 group ranks
for i in range(7):
    dom=[T[j] for j in range(7) if j!=i and all(groups[g][j]<=groups[g][i] for g in groups) and any(groups[g][j]<groups[g][i] for g in groups)]
    print(T[i],'dominated by',dom or '-')
# head-to-head GeoVanguard (0) vs ArcGIS SSE (1) on every criterion
names=['mIoU syn-m','mIoU syn-s','mIoU forest','mIoU green','gaps','overlap','invalid','area']+['axis '+d[:-2] for d in DS]+['entropy '+d[:-2] for d in DS]+['turns '+d[:-2] for d in DS]
w=l=t=0
for n,c in zip(names,allc):
    s='GV' if c[0]<c[1] else 'ArcGIS' if c[1]<c[0] else 'tie'
    w+=s=='GV'; l+=s=='ArcGIS'; t+=s=='tie'
    print('  %-22s %s'%(n,s))
print('GV wins %d, ArcGIS SSE wins %d, ties %d'%(w,l,t))
