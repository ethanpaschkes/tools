# tools

Small, general-purpose helper tools, each in its own folder with a README. They were built while working on other
projects and kept here so they can be found and reused instead of rebuilt.

| tool | what it does |
|---|---|
| [render-viewer](render-viewer) | live browser view of image-sequence renders as they land, with Play/scrub |
| [job-pause](job-pause) | freeze / unfreeze long jobs (renders, encodes) without losing progress |
| [mem-watch](mem-watch) | watch a Mac's memory per application during a long job; never kills anything |
| [repo-stats](repo-stats) | line-count snapshot of a git repo, with the change since last time |
| [usgs-terrain](usgs-terrain) | download US aerial imagery + elevation for any 8 × 8 km box (public domain) |
| [composite-fx](composite-fx) | effects for compositing CG over real footage: grade, light wrap, shake, grain… |
| [flyby-sound](flyby-sound) | physically modelled aircraft flyby sound: Doppler, delay, ground echo |
| [hlg-to-sdr](hlg-to-sdr) | iPhone HDR (HLG) video → normal SDR frames |
| [video-helpers](video-helpers) | macOS: grab a frame, phone bezel around a recording, side-by-side, no ffmpeg |
| [screenshot-frame](screenshot-frame) | iPhone body around iOS Simulator screenshots |
| [glb-tools](glb-tools) | recentre / recolour GLB 3D models without Blender |

Get one tool without cloning everything:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/ethanpaschkes/tools.git
cd tools && git sparse-checkout set render-viewer
```

None of these tools need an account, key or token. MIT licensed.
