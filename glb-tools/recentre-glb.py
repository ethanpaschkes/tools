#!/usr/bin/env python3
"""Centre a GLB model on its origin: bounding-box centre at x = z = 0, lowest point at y = 0.

    python3 recentre-glb.py model.glb          # rewrites the file in place: keep a copy

Shifts the SCENE ROOTS' translations (in glTF TRS a translation composes after the root's rotation, so the whole
model moves exactly by the shift). No vertex data is touched; only the JSON chunk is rewritten. The bounds are
vertex-exact, not an approximate box, so a model that orbits or is viewed around its origin ends up truly centred.
Needs numpy.
"""
import json, struct, sys, numpy as np
path=sys.argv[1]; b=open(path,'rb').read(); ln=struct.unpack_from('<I',b,12)[0]; g=json.loads(b[20:20+ln]); rest=b[20+ln:]
binoff=20+ln+8
def acc(i):
    a=g['accessors'][i]; v=g['bufferViews'][a['bufferView']]
    comps={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}[a['type']]; dt={5126:'<f4',5125:'<u4',5123:'<u2',5121:'<u1'}[a['componentType']]
    off=binoff+v.get('byteOffset',0)+a.get('byteOffset',0)
    return np.frombuffer(b,dtype=dt,count=a['count']*comps,offset=off).reshape(a['count'],comps)
def nm(n):
    if 'matrix' in n: return np.array(n['matrix']).reshape(4,4).T
    m=np.eye(4)
    if 'scale' in n: m=m@np.diag([*n['scale'],1])
    if 'rotation' in n:
        x,y,z,w=n['rotation']; R=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]]); M=np.eye(4); M[:3,:3]=R; m=M@m
    if 'translation' in n: T=np.eye(4); T[:3,3]=n['translation']; m=T@m
    return m
def bounds():
    worlds={}
    def walk(i,parent):
        m=parent@nm(g['nodes'][i]); worlds[i]=m
        for c in g['nodes'][i].get('children',[]): walk(c,m)
    for r in g['scenes'][g.get('scene',0)]['nodes']: walk(r,np.eye(4))
    lo=np.full(3,np.inf); hi=np.full(3,-np.inf)
    for i,n in enumerate(g['nodes']):
        if 'mesh' in n:
            m=worlds[i]
            for p in g['meshes'][n['mesh']]['primitives']:
                W=acc(p['attributes']['POSITION']).astype(np.float64)@m[:3,:3].T+m[:3,3]; lo=np.minimum(lo,W.min(0)); hi=np.maximum(hi,W.max(0))
    return lo,hi
lo,hi=bounds(); shift=np.array([-(lo[0]+hi[0])/2, -lo[1], -(lo[2]+hi[2])/2])
print(f"before: x {lo[0]:.3f}..{hi[0]:.3f} y {lo[1]:.3f}..{hi[1]:.3f} z {lo[2]:.3f}..{hi[2]:.3f} → shift {np.round(shift,3)}")
for r in g['scenes'][g.get('scene',0)]['nodes']:
    n=g['nodes'][r]
    if 'matrix' in n:
        m=n['matrix']; m[12]+=shift[0]; m[13]+=shift[1]; m[14]+=shift[2]
    else:
        t=n.get('translation',[0,0,0]); n['translation']=[t[0]+shift[0], t[1]+shift[1], t[2]+shift[2]]
lo,hi=bounds(); print(f"after:  x {lo[0]:.3f}..{hi[0]:.3f} y {lo[1]:.3f}..{hi[1]:.3f} z {lo[2]:.3f}..{hi[2]:.3f}")
js=json.dumps(g,separators=(',',':')).encode(); js+=b' '*((4-len(js)%4)%4)
out=b'glTF'+struct.pack('<II',2,12+8+len(js)+len(rest))+struct.pack('<I',len(js))+b'JSON'+js+rest
open(path,'wb').write(out); print('written', len(out), 'bytes')
