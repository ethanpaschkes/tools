#!/usr/bin/env python3
"""Recolour a GLB model by rewriting its materials' baseColorFactor / metallic / roughness in the JSON chunk.
The BIN chunk (geometry + textures) is left byte-identical, so it is fast, lossless, and needs no Blender.

    python3 recolor-glb.py in.glb out.glb --list                       # print every material and its values
    python3 recolor-glb.py in.glb out.glb MATERIAL=R,G,B[,METALLIC[,ROUGHNESS]] [MATERIAL=...]

    e.g.  python3 recolor-glb.py plane.glb plane-blue.glb body=0.16,0.34,0.78,0.1,0.42 trim=0.97,0.98,1.0

R, G, B are 0-1 (linear). baseColorFactor MULTIPLIES the texture: a near-white factor lets a painted texture show
through; a tinted one washes the part in that hue and keeps the texture's detail. Lowering metallic brightens a
part in viewers without a strong environment map.
"""
import json, struct, sys

def read_glb(path):
    with open(path, 'rb') as f:
        magic, ver, total = struct.unpack('<III', f.read(12))
        assert magic == 0x46546C67, 'not a GLB'
        jlen, jtype = struct.unpack('<II', f.read(8))
        jdata = f.read(jlen)
        blen, btype = struct.unpack('<II', f.read(8))
        bdata = f.read(blen)
    return json.loads(jdata), bdata

def write_glb(path, gltf, bdata):
    jdata = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    jpad = (4 - len(jdata) % 4) % 4
    jdata += b' ' * jpad
    bpad = (4 - len(bdata) % 4) % 4
    bpad_data = bdata + b'\x00' * bpad
    total = 12 + 8 + len(jdata) + 8 + len(bpad_data)
    with open(path, 'wb') as f:
        f.write(struct.pack('<III', 0x46546C67, 2, total))
        f.write(struct.pack('<II', len(jdata), 0x4E4F534A))  # 'JSON'
        f.write(jdata)
        f.write(struct.pack('<II', len(bpad_data), 0x004E4942))  # 'BIN\0'
        f.write(bpad_data)

def mat_by_name(gltf, name):
    for m in gltf.get('materials', []):
        if m.get('name') == name:
            return m
    return None

def set_pbr(gltf, name, base=None, metallic=None, roughness=None):
    m = mat_by_name(gltf, name)
    if not m:
        print(f'  (no material "{name}")'); return
    pbr = m.setdefault('pbrMetallicRoughness', {})
    if base is not None:
        pbr['baseColorFactor'] = list(base)
    if metallic is not None:
        pbr['metallicFactor'] = metallic
    if roughness is not None:
        pbr['roughnessFactor'] = roughness
    print(f'  {name}: base={pbr.get("baseColorFactor")} metal={pbr.get("metallicFactor")} rough={pbr.get("roughnessFactor")}')

def list_materials(gltf):
    for m in gltf.get('materials', []):
        pbr = m.get('pbrMetallicRoughness', {})
        print(f"  {m.get('name')!r}: base={pbr.get('baseColorFactor')} metal={pbr.get('metallicFactor')} "
              f"rough={pbr.get('roughnessFactor')} texture={'baseColorTexture' in pbr}")


if __name__ == '__main__':
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    src, dst, edits = sys.argv[1], sys.argv[2], sys.argv[3:]
    gltf, bdata = read_glb(src)
    if edits == ['--list']:
        list_materials(gltf)
        raise SystemExit(0)
    print(f'Recolouring {src} → {dst}')
    for e in edits:
        name, _, vals = e.partition('=')
        v = [float(x) for x in vals.split(',')]
        if len(v) < 3:
            raise SystemExit(f'{e!r}: need MATERIAL=R,G,B[,METALLIC[,ROUGHNESS]]')
        set_pbr(gltf, name, base=v[:3] + [1.0], metallic=v[3] if len(v) > 3 else None,
                roughness=v[4] if len(v) > 4 else None)
    write_glb(dst, gltf, bdata)
    print('done.')
