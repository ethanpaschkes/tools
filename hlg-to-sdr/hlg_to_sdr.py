"""Decode the iPhone HLG (BT.2020) clip to SDR BT.709 PNG frames.

    python3 hlg_to_sdr.py IN.MOV OUT_DIR [GAIN] [START_S N_FRAMES]

Many ffmpeg builds have no zscale filter, so the transfer is done by hand. Steps: inverse HLG OETF → scene
light → OOTF (gamma 1.2) → BT.2020→709 matrix → exposure gain with a soft shoulder → BT.709
OETF. GAIN 2.3 was calibrated against Apple's own SDR export of the same iPhone clips; raise it if the result is dark.
"""
import pathlib
import subprocess
import sys

import numpy as np

if len(sys.argv) < 3:
    sys.exit(__doc__)
src, out = sys.argv[1], pathlib.Path(sys.argv[2])
gain = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
cut = ['-ss', sys.argv[4]] if len(sys.argv) > 4 else []
limit = ['-frames:v', sys.argv[5]] if len(sys.argv) > 5 else []
out.mkdir(parents=True, exist_ok=True)
W, H = 1080, 1920                                         # portrait output; change for landscape clips
a, b, c = 0.17883277, 0.28466892, 0.55991073
M = np.array([[1.6605, -0.5876, -0.0728], [-0.1246, 1.1329, -0.0083], [-0.0182, -0.1006, 1.1187]])
p = subprocess.Popen(['ffmpeg', '-v', 'error', *cut, '-i', src, *limit, '-vf',
                      'scale=in_color_matrix=bt2020:in_range=tv:out_range=pc,format=rgb48le',
                      '-f', 'rawvideo', '-'], stdout=subprocess.PIPE)
i = 0
while True:
    raw = p.stdout.read(W * H * 6)
    if len(raw) < W * H * 6:
        break
    e = np.frombuffer(raw, np.uint16).reshape(H, W, 3).astype(np.float32) / 65535
    lin = np.where(e <= 0.5, e * e / 3, (np.exp((e - c) / a) + b) / 12)
    y = 0.2627 * lin[..., 0] + 0.6780 * lin[..., 1] + 0.0593 * lin[..., 2]
    lin = lin * (np.maximum(y, 1e-6) ** 0.2)[..., None]
    lin = np.clip(lin @ M.T, 0, None) * gain
    lin = np.where(lin < 0.8, lin, 0.8 + 0.2 * np.tanh((lin - 0.8) / 0.2))
    v = np.where(lin < 0.018, 4.5 * lin, 1.099 * np.power(lin, 0.45) - 0.099)
    i += 1
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{W}x{H}',
                    '-i', '-', str(out / f'f{i:04d}.png')], input=(np.clip(v, 0, 1) * 65535).astype(np.uint16).tobytes(),
                   check=True)
print(f'{i} frames → {out}')
