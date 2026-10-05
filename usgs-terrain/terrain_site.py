"""Download real US terrain for an 8 x 8 km box: USGS NAIP aerial imagery (1 m/px) + USGS 3DEP elevation (10 m/px).

    python3 terrain_site.py SITE LAT LON [--out DIR]      # e.g.  python3 terrain_site.py grand-canyon 36.0544 -112.1401

Both sources are US federal data (public domain); no account or key is needed. Covers the United States only.
The box is centred on LAT, LON in that point's UTM zone (WGS 84). Downloads are cached in OUT/terrain_SITE/raw
(default OUT: ./terrain), then it writes OUT/terrain_SITE/:
    naip.jpg   8000 x 8000 RGB, 1 m/px, north-up (row 0 = north)
    dem.npy    800 x 800 float32, metres above NAVD88, 10 m/px, north-up, NaN = no data
    meta.json  the box and the point in a LOCAL frame (metres east / north of the box's south-west corner)
Needs numpy and Pillow. The 3DEP service writes a TILED GeoTIFF whose empty tiles are sparse (offset 0, byte count 0);
Pillow returns garbage for those, so the tiles are read by hand and missing data becomes NaN.
"""
import json
import math
import pathlib
import sys
import time
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image

NAIP_SERVICE = 'https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer'
DEM_SERVICE = 'https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer'
SIZE, NAIP_TILE, DEM_PX = 8000, 2000, 800        # 4000 px NAIP tile downloads return HTTP 502; 2000 px tiles work


def utm_forward(lat, lon, lon0, a=6378137.0, f=1 / 298.257223563, k0=0.9996, e0=500000.0, n0=0.0):
    """Transverse Mercator, Krüger series to n^6 (Karney 2011): (lat, lon) degrees → (easting, northing) m."""
    n = f / (2 - f)
    big_a = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64 + n ** 6 / 256)
    al = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16 + 41 * n ** 4 / 180 - 127 * n ** 5 / 288 + 7891 * n ** 6 / 37800,
          13 * n ** 2 / 48 - 3 * n ** 3 / 5 + 557 * n ** 4 / 1440 + 281 * n ** 5 / 630 - 1983433 * n ** 6 / 1935360,
          61 * n ** 3 / 240 - 103 * n ** 4 / 140 + 15061 * n ** 5 / 26880 + 167603 * n ** 6 / 181440,
          49561 * n ** 4 / 161280 - 179 * n ** 5 / 168 + 6601661 * n ** 6 / 7257600,
          34729 * n ** 5 / 80640 - 3418889 * n ** 6 / 1995840,
          212378941 * n ** 6 / 319334400)
    e = 2 * math.sqrt(n) / (1 + n)
    phi, lam = math.radians(lat), math.radians(lon - lon0)
    t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
    xi0 = math.atan2(t, math.cos(lam))
    eta0 = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    xi = xi0 + sum(c * math.sin(2 * j * xi0) * math.cosh(2 * j * eta0) for j, c in enumerate(al, 1))
    eta = eta0 + sum(c * math.cos(2 * j * xi0) * math.sinh(2 * j * eta0) for j, c in enumerate(al, 1))
    return e0 + k0 * big_a * eta, n0 + k0 * big_a * xi


def _get(url, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'usgs-terrain-downloader/1.0'})
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except Exception as exc:                            # network: retry politely, then give up
            print(f'  request failed ({exc}); attempt {k + 1}/{tries}')
            if k == tries - 1:
                raise
            time.sleep(5 * (k + 1))


def fetch_json(url, params):
    j = json.loads(_get(url + '?' + urllib.parse.urlencode(params)))
    if 'error' in j:
        raise RuntimeError(f'service error: {j["error"]}')
    return j


def export(service, sr, bbox, size, params, path):
    """exportImage with f=json, check the returned grid is exactly the requested one, then fetch the href."""
    j = fetch_json(service + '/exportImage', dict(bbox=','.join(f'{v:.3f}' for v in bbox), bboxSR=sr,
                                                  imageSR=sr, size=f'{size},{size}', f='json', **params))
    ex = j['extent']
    got = (ex['xmin'], ex['ymin'], ex['xmax'], ex['ymax'])
    off = max(abs(g - w) for g, w in zip(got, bbox))
    if off > 0.01 or j['width'] != size or j['height'] != size or ex['spatialReference'].get('latestWkid') != sr:
        raise RuntimeError(f'{path.name}: service returned a different grid than requested: {j}')
    body = _get(j['href'])
    tmp = path.with_suffix('.part')
    tmp.write_bytes(body)
    tmp.rename(path)
    print(f'  {path.name}: {len(body) / 1e6:.2f} MB')


def load_dem(path, west, north):
    """Read the service's tiled float32 GeoTIFF by hand: sparse tiles and exact 0.0 fill → NaN."""
    t = Image.open(path).tag_v2
    raw = path.read_bytes()
    assert raw[:2] == b'II', 'expected a little-endian TIFF'
    assert (t[256], t[257], t[258], t[259], t[277], t.get(339)) == (DEM_PX, DEM_PX, (32,), 1, 1, (3,)), dict(t)
    tie, scale = t[33922], t[33550]
    assert abs(tie[3] - west) < 0.01 and abs(tie[4] - north) < 0.01, tie
    assert abs(scale[0] - SIZE / DEM_PX) < 1e-6 and abs(scale[1] - SIZE / DEM_PX) < 1e-6, scale
    if 322 in t:
        tw, th, offs, cnts = t[322], t[323], t[324], t[325]
    else:
        tw, th, offs, cnts = DEM_PX, t[278], t[273], t[279]
    dem = np.full((DEM_PX, DEM_PX), np.nan, np.float32)
    nx = -(-DEM_PX // tw)
    for k, (o, c) in enumerate(zip(offs, cnts)):
        if c == 0:
            continue
        assert c == tw * th * 4, (k, c)
        r0, c0 = (k // nx) * th, (k % nx) * tw
        tile = np.frombuffer(raw, '<f4', tw * th, o).reshape(th, tw)
        h, w = min(th, DEM_PX - r0), min(tw, DEM_PX - c0)
        dem[r0:r0 + h, c0:c0 + w] = tile[:h, :w]
    dem[(dem == 0.0) | (np.abs(dem) > 9000)] = np.nan
    return dem


def main():
    a = sys.argv[1:]
    out_root = pathlib.Path(a[a.index('--out') + 1]) if '--out' in a else pathlib.Path('terrain')
    a = [v for i, v in enumerate(a) if v != '--out' and (i == 0 or a[i - 1] != '--out')]
    if len(a) != 3:
        sys.exit('usage: terrain_site.py SITE LAT LON [--out DIR]')
    site, lat, lon = a[0], float(a[1]), float(a[2])
    zone = int((lon + 180) // 6) + 1
    sr, lon0 = 32600 + zone, zone * 6 - 183
    px, py = utm_forward(lat, lon, lon0)
    west, south = round(px) - SIZE // 2, round(py) - SIZE // 2
    east, north = west + SIZE, south + SIZE
    out = out_root / f'terrain_{site}'
    raw = out / 'raw'
    raw.mkdir(parents=True, exist_ok=True)
    for r in range(SIZE // NAIP_TILE):
        for c in range(SIZE // NAIP_TILE):
            path = raw / f'naip_r{r}c{c}.jpg'
            if not path.exists():
                x0, y1 = west + c * NAIP_TILE, north - r * NAIP_TILE
                export(NAIP_SERVICE, sr, (x0, y1 - NAIP_TILE, x0 + NAIP_TILE, y1), NAIP_TILE,
                       dict(bandIds='0,1,2', format='jpg', compressionQuality=90,
                            interpolation='RSP_BilinearInterpolation'), path)
    dem_path = raw / 'dem_10m.tif'
    if not dem_path.exists():
        export(DEM_SERVICE, sr, (west, south, east, north), DEM_PX,
               dict(format='tiff', pixelType='F32', compression='None', interpolation='RSP_BilinearInterpolation'),
               dem_path)
    img = np.zeros((SIZE, SIZE, 3), np.uint8)
    for r in range(SIZE // NAIP_TILE):
        for c in range(SIZE // NAIP_TILE):
            t = Image.open(raw / f'naip_r{r}c{c}.jpg')
            assert t.size == (NAIP_TILE, NAIP_TILE), t.size
            img[r * NAIP_TILE:(r + 1) * NAIP_TILE, c * NAIP_TILE:(c + 1) * NAIP_TILE] = np.asarray(t.convert('RGB'))
    Image.fromarray(img).save(out / 'naip.jpg', quality=92)
    dem = load_dem(dem_path, west, north)
    np.save(out / 'dem.npy', dem)
    meta = {'site': site, 'utm_zone': zone, 'hemisphere': 'N', 'epsg': sr, 'datum': 'WGS 84',
            'west': west, 'east': east, 'south': south, 'north': north,
            'naip_m_per_px': 1.0, 'dem_m_per_px': SIZE / DEM_PX,
            'local_frame': 'metres east (x) / north (y) of the SW corner (west, south); naip pixel (col,row) centre '
                           '= (col+0.5, 8000-row-0.5); dem cell (i,j) centre = (10j+5, 8000-10i-5)',
            'point_latlon': [lat, lon], 'point_xy_local': [px - west, py - south],
            'phone_xy_local': [px - west, py - south],
            'dem_nan_pct': round(100 * float(np.isnan(dem).mean()), 2),
            'dem_range_m': [round(float(np.nanmin(dem)), 1), round(float(np.nanmax(dem)), 1)]}
    (out / 'meta.json').write_text(json.dumps(meta, indent=1))
    print(json.dumps({k: meta[k] for k in ('epsg', 'point_xy_local', 'dem_nan_pct', 'dem_range_m')}))


if __name__ == '__main__':
    main()
