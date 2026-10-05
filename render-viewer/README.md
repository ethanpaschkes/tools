# render-viewer

A live browser view of image-sequence renders (Blender, ffmpeg, anything that writes numbered frames). Shows the newest frame of each render folder as it lands, a strip of recent frames, and Play / speed / scrub / arrow-key stepping.

```bash
python3 render_viewer.py ~/renders           # then open http://localhost:8765
python3 render_viewer.py ~/renders ~/other --port 9000 --hours 12
```

- Default frame names: `f0001.png`, `f0002.png` … Change with `--pattern '^frame_\d+\.png$'`.
- Local only (listens on 127.0.0.1), read-only, and serves nothing outside the folders you name.
- Needs only Python 3.
