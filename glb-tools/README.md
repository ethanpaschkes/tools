# glb-tools

Small fixes for GLB (binary glTF) 3D models with no Blender needed.

```bash
python3 recentre-glb.py model.glb                  # centre on the origin, lowest point at y=0 (in place)
python3 recolor-glb.py in.glb out.glb --list       # print every material
python3 recolor-glb.py in.glb out.glb body=0.16,0.34,0.78,0.1,0.42 trim=0.97,0.98,1.0
```

`recolor-glb` takes `MATERIAL=R,G,B[,METALLIC[,ROUGHNESS]]` (0-1). Geometry and textures are copied byte for byte. `recentre-glb` rewrites the file in place: keep a copy. `recentre-glb` needs numpy.
