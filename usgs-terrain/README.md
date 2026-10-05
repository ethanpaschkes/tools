# usgs-terrain

Downloads real terrain for an 8 × 8 km box anywhere in the US: USGS NAIP aerial photography (1 m/px) and USGS 3DEP elevation (10 m/px). Both are public domain and need no account or key.

```bash
python3 terrain_site.py grand-canyon 36.0544 -112.1401
python3 terrain_site.py my-airport 41.43 -74.39 --out ~/terrain
```

Writes `naip.jpg` (8000 × 8000), `dem.npy` (800 × 800 float32 metres) and `meta.json` (box corners, UTM zone, local frame). Downloads are cached and resume. Needs Python 3, numpy and Pillow.
