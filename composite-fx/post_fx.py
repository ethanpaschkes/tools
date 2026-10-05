"""2D post-compositing effects for CG aircraft over real phone footage (1080x1920, 30 fps).

    import post_fx as fx
    comp = fx.over(plate, fx.light_wrap(plate, fx.match_grade(plate, cg), 24))
    comp = fx.match_grain(plate, comp, mask=cg_alpha, seed=frame)

Conventions: images are 1080x1920 (vertical video); every function takes a PIL image or a
numpy array and returns a float32 numpy array in sRGB 0-255 (H, W, 3) or (H, W, 4). CG renders are Blender's RGBA
PNGs: STRAIGHT (un-premultiplied) alpha. Anything that resamples or blurs a CG layer does it premultiplied, so the
transparent background never bleeds black into the edges. Light is added and mixed in LINEAR light (srgb_to_linear),
because flare, haze and veiling glare add as light, not as sRGB code values. Everything random takes a seed and is
deterministic. `to_pil(arr)` turns a result back into a PIL image.

Effects
  plate_motion(frames)                       the plate's camera motion: per-frame dx, dy, roll, scale (+ the shake)
  apply_shake(cg, dx, dy, roll)              move a CG layer with that motion
  synthetic_shake(n, amp_px, freq_hz, seed)  handheld wobble + a rumble kick as a jet passes
  light_wrap(plate, cg, radius)              the plate's light bleeding over the CG's edges
  match_grade(plate, cg)                     CG black level / contrast / colour from the plate's statistics
  heat_haze(img, nozzle_xy, direction, ...)  animated refraction in the exhaust plume
  lens_flare(img, light_xy, strength)        glow, a streak and ghosts along the line through the frame centre
  specular_glint(cg, strength)               the CG's brightest highlights bloomed (canopy / sun glint)
  chromatic_aberration(img, px)              lateral CA, radial, px of R/B split at the corners
  vignette(img, amount)                      cos^4-style corner falloff in linear light
  match_grain(plate, img)                    the plate's measured noise (per channel sigma + grain size) added
"""
import math

import numpy as np
from PIL import Image
from scipy import ndimage, signal

W, H = 1080, 1920
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


# ── plumbing ──────────────────────────────────────────────────────────────────────────────────────────────────────
def as_f32(im, mode=None):
    """PIL image / path / array → float32 array 0-255. mode 'RGB' or 'RGBA' converts a PIL image first."""
    if isinstance(im, (str, bytes)) or hasattr(im, '__fspath__'):
        im = Image.open(im)
    if isinstance(im, Image.Image):
        if mode:
            im = im.convert(mode)
        return np.asarray(im, np.float32)
    a = np.asarray(im, np.float32)
    if mode == 'RGB' and a.shape[-1] == 4:
        a = a[..., :3]
    if mode == 'RGBA' and a.shape[-1] == 3:
        a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.float32)], -1)
    return a


def to_pil(a):
    """float array 0-255 → PIL image (RGB or RGBA by channel count), rounded."""
    return Image.fromarray(np.clip(np.rint(a), 0, 255).astype(np.uint8))


def srgb_to_linear(c):
    """sRGB 0-255 → linear 0-1."""
    c = np.clip(np.asarray(c, np.float32) / 255.0, 0, None)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(c):
    """linear 0-1 → sRGB 0-255 (values above 1 clip)."""
    c = np.clip(np.asarray(c, np.float32), 0, 1)
    return (np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055) * 255).astype(np.float32)


def _split(cg):
    """RGBA → (straight rgb, alpha 0-1 with a trailing axis)."""
    cg = as_f32(cg, 'RGBA')
    return cg[..., :3], cg[..., 3:] / 255.0


def _join(rgb, a):
    return np.concatenate([rgb, np.clip(a, 0, 1) * 255.0], -1).astype(np.float32)


def over(plate, cg):
    """Straight-alpha CG over the plate (the composite the effects are meant for) → RGB."""
    p = as_f32(plate, 'RGB')
    rgb, a = _split(cg)
    return p * (1 - a) + rgb * a


def _gauss(x, sigma):
    """Gaussian blur of the first two axes (nearest-edge)."""
    if sigma <= 0:
        return x
    s = (sigma, sigma) + (0,) * (x.ndim - 2)
    return ndimage.gaussian_filter(x, s, mode='nearest', truncate=3.0)


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def _warp(img, src_x, src_y, order=3):
    """Sample every channel of img at (src_x, src_y) (output-pixel → input-pixel maps). Edges: nearest."""
    out = np.empty(src_x.shape + img.shape[2:], np.float32)
    for c in range(img.shape[2]):
        out[..., c] = ndimage.map_coordinates(img[..., c], [src_y, src_x], order=order, mode='nearest')
    return out


# ── 1. camera shake ───────────────────────────────────────────────────────────────────────────────────────────────
def _highpass_luma(rgb, sigma=10.0):
    """Luminance minus its large-scale blur (sky gradients, exposure drift), lightly denoised."""
    y = as_f32(rgb, 'RGB') @ LUMA
    return _gauss(y, 1.0) - _gauss(y, sigma)


def _phase_corr(a, b, win):
    """Sub-pixel shift (dx, dy) of tile b relative to tile a (b's content = a's moved by +d), and the peak height."""
    fa, fb = np.fft.rfft2(a * win), np.fft.rfft2(b * win)
    r = fb * np.conj(fa)
    r /= np.abs(r) + 1e-9
    c = np.fft.irfft2(r, s=a.shape)
    iy, ix = np.unravel_index(np.argmax(c), c.shape)
    h, w = c.shape

    def sub(cm, c0, cp):                                 # parabola through the peak and its neighbours
        d = cm - 2 * c0 + cp
        return 0.0 if abs(d) < 1e-12 else 0.5 * (cm - cp) / d

    ddy = sub(c[(iy - 1) % h, ix], c[iy, ix], c[(iy + 1) % h, ix])
    ddx = sub(c[iy, (ix - 1) % w], c[iy, ix], c[iy, (ix + 1) % w])
    dy = (iy + ddy + h / 2) % h - h / 2
    dx = (ix + ddx + w / 2) % w - w / 2
    return dx, dy, float(c[iy, ix])


def _fit_similarity(pts, d, w, iters=6):
    """Robust least squares for d ≈ t + [[a, -b], [b, a]] · pts (a = scale-1, b = roll in radians)
    → ((tx, ty, a, b), per-tile residual px)."""
    x, y = pts[:, 0], pts[:, 1]
    A = np.zeros((2 * len(pts), 4))
    A[0::2, 0], A[0::2, 2], A[0::2, 3] = 1, x, -y
    A[1::2, 1], A[1::2, 2], A[1::2, 3] = 1, y, x
    rhs = d.reshape(-1)
    ww = np.repeat(w, 2).astype(float)
    p = np.zeros(4)
    for _ in range(iters):
        sw = np.sqrt(ww)
        p = np.linalg.lstsq(A * sw[:, None], rhs * sw, rcond=None)[0]
        res = np.hypot(*(A @ p - rhs).reshape(-1, 2).T)
        k = max(1.5 * np.median(res), 0.15)              # Huber: tiles off the fit (the jet, smoke) lose weight
        ww = np.repeat(w * np.minimum(1, k / np.maximum(res, 1e-9)), 2)
    return p, res


def _coarse_shift(a, b, f=4):
    """Whole-frame translation of b relative to a at 1/f resolution (catches pans bigger than half a tile)."""
    h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    sa = a[:h, :w].reshape(h // f, f, w // f, f).mean((1, 3))
    sb = b[:h, :w].reshape(h // f, f, w // f, f).mean((1, 3))
    win = np.outer(np.hanning(sa.shape[0]), np.hanning(sa.shape[1])).astype(np.float32)
    dx, dy, pk = _phase_corr(sa, sb, win)
    return dx * f, dy * f, pk


def plate_motion(frames, tile=256, grid=(4, 7), smooth_frames=9, min_peak=0.05, min_tiles=5, max_res=1.5):
    """Measure the plate's camera motion, frame by frame.

    frames: a sequence of plate frames (paths, PIL images or arrays), consecutive.
    Each pair of neighbours is compared tile by tile (grid = cols x rows of tile² windows, Hann-windowed) by phase
    correlation of high-passed luminance — the shift of the peak, refined to sub-pixel by a parabola. A whole-frame
    correlation at 1/4 resolution first gives the pan, and each tile is searched around it, so pans of a few
    hundred px per frame still lock. A similarity (translation, roll, scale about the frame centre) is fitted to the
    tile shifts with Huber weights, so tiles with a moving jet or drifting smoke, or blank sky (a weak peak,
    < min_peak), barely count. A pair is ok when at least min_tiles tiles agree with the fit to within max_res px;
    otherwise (a motion-blurred whip pan, featureless sky, a cut) its step is interpolated from the neighbouring ok
    steps (constant velocity through the gap). Pair motions are chained into a path relative to frame 0.

    Returns a dict of float arrays, len(frames) each:
      dx, dy      where frame 0's centre content now sits, minus the centre (px, +x right, +y down)
      roll        degrees, + = content turned clockwise on screen
      scale       content scale relative to frame 0 (a pinch zoom shows here)
      step_dx, step_dy   this frame's motion from the previous one, in this frame's own pixels
      shake_dx, shake_dy, shake_roll   the jitter, in on-screen px / degrees: the steps summed, minus a local
                  quadratic fit over smooth_frames frames (Savitzky-Golay) — the deliberate pan that follows a jet,
                  its acceleration, and any zoom are taken out. Only meaningful where ok.
      peak        median tile correlation peak for the pair ending at that frame (confidence; 1 = identical)
      ok          1 where the pair was measured, 0 where its step was interpolated
    To glue CG to the plate: apply_shake(cg, dx[i], dy[i], roll[i], scale[i]) with the CG framed for frame 0.
    """
    n = len(frames)
    cols, rows = grid
    cx, cy = (W - 1) / 2, (H - 1) / 2
    xs = np.linspace(tile / 2, W - tile / 2, cols)
    ys = np.linspace(tile / 2, H - tile / 2, rows)
    win = np.outer(np.hanning(tile), np.hanning(tile)).astype(np.float32)
    steps = np.zeros((n, 4))                             # per pair: tx, ty, scale-1, roll (rad)
    ok = np.zeros(n, bool)
    ok[0] = True
    peak = np.ones(n)
    prev = _highpass_luma(frames[0])
    for i in range(1, n):
        cur = _highpass_luma(frames[i])
        gx, gy, _ = _coarse_shift(prev, cur)
        pts, ds, ws, peaks = [], [], [], []
        for ty in ys:
            for tx in xs:
                y0, x0 = int(ty - tile / 2), int(tx - tile / 2)
                y1 = int(np.clip(y0 + round(gy), 0, H - tile))
                x1 = int(np.clip(x0 + round(gx), 0, W - tile))
                a, b = prev[y0:y0 + tile, x0:x0 + tile], cur[y1:y1 + tile, x1:x1 + tile]
                if a.std() < 0.2 or b.std() < 0.2:
                    continue
                dx, dy, pk = _phase_corr(a, b, win)
                peaks.append(pk)
                if pk < min_peak:
                    continue
                pts.append((tx - cx, ty - cy))
                ds.append((dx + x1 - x0, dy + y1 - y0))
                ws.append(pk)
        peak[i] = float(np.median(peaks)) if peaks else 0.0
        if len(pts) >= min_tiles:
            steps[i], res = _fit_similarity(np.array(pts), np.array(ds), np.array(ws))
            ok[i] = (res < max_res).sum() >= min_tiles
        prev = cur
    good = np.flatnonzero(ok[1:]) + 1
    for j in range(4):                                   # gaps: constant velocity from the neighbouring ok steps
        steps[1:, j] = np.interp(np.arange(1, n), good, steps[good, j]) if len(good) else 0.0
    out = {k: np.zeros(n) for k in ('dx', 'dy', 'roll', 'scale')}
    out['scale'][0] = 1.0
    M = np.eye(3)
    for i in range(1, n):
        tx_, ty_, sa, sb = steps[i]
        M = np.array([[1 + sa, -sb, tx_], [sb, 1 + sa, ty_], [0, 0, 1]]) @ M   # frame 0 (centred) → frame i
        out['dx'][i], out['dy'][i] = M[0, 2], M[1, 2]
        out['roll'][i] = math.degrees(math.atan2(M[1, 0], M[0, 0]))
        out['scale'][i] = math.hypot(M[0, 0], M[1, 0])
    out['step_dx'], out['step_dy'] = steps[:, 0], steps[:, 1]
    out['step_roll'] = np.degrees(np.arctan2(steps[:, 3], 1 + steps[:, 2]))
    out['peak'], out['ok'] = peak, ok.astype(float)
    for k in ('dx', 'dy', 'roll'):
        path = np.cumsum(out['step_' + k])
        win_ = min(smooth_frames | 1, n if n % 2 else n - 1)
        out['shake_' + k] = path - signal.savgol_filter(path, win_, 2, mode='interp') if win_ > 3 else path * 0
    return out


def apply_shake(cg_rgba, dx, dy, roll, scale=1.0):
    """Move a CG layer the way plate_motion says the plate moved: rotate by roll degrees (clockwise on screen) and
    scale about the frame centre, then shift by (dx, dy) px. Resampled premultiplied (bicubic), so edges stay clean
    and sub-pixel motion is smooth. Returns RGBA float32."""
    rgb, a = _split(cg_rgba)
    pre = np.concatenate([rgb * a, a * 255.0], -1)
    cx, cy = (W - 1) / 2, (H - 1) / 2
    th = math.radians(roll)
    c, s = math.cos(th) / scale, math.sin(th) / scale
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    u, v = xx - cx - dx, yy - cy - dy                    # invert: output → input
    src_x = c * u + s * v + cx
    src_y = -s * u + c * v + cy
    out = np.empty_like(pre)
    for ch in range(4):
        out[..., ch] = ndimage.map_coordinates(pre[..., ch], [src_y, src_x], order=3, mode='constant', cval=0.0)
    al = np.clip(out[..., 3:], 0, 255)
    rgb = np.where(al > 0.5, out[..., :3] * 255.0 / np.maximum(al, 1e-6), 0)
    return np.concatenate([np.clip(rgb, 0, 255), al], -1).astype(np.float32)


def _band_noise(n, fps, f_lo, f_hi, rng):
    """Unit-RMS Gaussian noise band-limited to f_lo..f_hi Hz (FFT brick wall, smooth edges)."""
    m = 1 << int(math.ceil(math.log2(max(n, 8) * 2)))
    spec = rng.normal(size=m // 2 + 1) + 1j * rng.normal(size=m // 2 + 1)
    f = np.fft.rfftfreq(m, 1 / fps)
    gate = _smoothstep(f_lo * 0.7, f_lo, f) * (1 - _smoothstep(f_hi, f_hi * 1.3, f))
    x = np.fft.irfft(spec * gate, m)[:n]
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-12)


def synthetic_shake(n, amp_px, freq_hz, seed, kick_at=None, fps=30000 / 1001, kick_amp_px=None, kick_freq_hz=9.0,
                    kick_attack_s=0.15, kick_decay_s=0.6, roll_deg_per_px=0.03):
    """A made-up handheld shake for n frames → (dx, dy, roll) arrays (px, px, degrees).

    Base: band-limited noise (0.5·freq_hz … 1.5·freq_hz) with RMS amp_px on each axis — a hand's tremor and sway.
    kick_at (frame index, or None): the jet's closest pass. The sound pressure shakes the hand: a burst of faster
    rumble (around kick_freq_hz, kept under the 15 Hz frame-rate limit) of RMS kick_amp_px (default 3·amp_px),
    rising over kick_attack_s and dying over kick_decay_s. Put kick_at a few frames AFTER the visual pass if the
    camera was far from the line: the sound arrives distance / 343 m/s later. Roll follows the noise at
    roll_deg_per_px. Deterministic for a given seed."""
    rng = np.random.default_rng(seed)
    lo, hi = 0.5 * freq_hz, 1.5 * freq_hz
    dx = _band_noise(n, fps, lo, hi, rng) * amp_px
    dy = _band_noise(n, fps, lo, hi, rng) * amp_px
    roll = _band_noise(n, fps, lo, hi, rng) * amp_px * roll_deg_per_px
    if kick_at is not None:
        ka = 3.0 * amp_px if kick_amp_px is None else kick_amp_px
        t = (np.arange(n) - kick_at) / fps
        env = np.where(t < 0, np.exp(-(t / kick_attack_s) ** 2), np.exp(-t / kick_decay_s))
        kf = min(kick_freq_hz, 0.45 * fps)
        dx += _band_noise(n, fps, 0.6 * kf, 1.3 * kf, rng) * ka * env
        dy += _band_noise(n, fps, 0.6 * kf, 1.3 * kf, rng) * ka * env * 1.3   # the vertical buffet is the strongest
        roll += _band_noise(n, fps, 0.6 * kf, 1.3 * kf, rng) * ka * env * roll_deg_per_px
    return dx.astype(np.float32), dy.astype(np.float32), roll.astype(np.float32)


# ── 2. light wrap ─────────────────────────────────────────────────────────────────────────────────────────────────
def light_wrap(plate, cg_rgba, radius=16.0, strength=0.35, solid_alpha=(0.15, 0.6)):
    """Bleed the plate's light into the CG's edges.

    A real lens and the air scatter a bright background over the edge of anything in front of it: against an
    overcast sky the jet's silhouette gets a soft rim of sky colour, and the CG's razor edge disappears. The wrap
    colour at each pixel is the plate's light from just outside the silhouette (the plate weighted by 1-alpha,
    blurred by radius, normalised); how much of it reaches a CG pixel is how much uncovered plate lies within
    ~radius of it (the blurred 1-alpha), squared so the rim stays a rim on a thin wing instead of washing the whole
    aircraft (half of strength on the silhouette's edge, an eighth at the point where a quarter of the surround is
    plate). Mixed in linear light, into the CG's colour.
    solid_alpha: alpha ramp that counts as "object" — a shadow catcher's faint alpha (C-17 renders, ~0.05) is not
    an edge to wrap. Returns RGBA float32 (alpha unchanged)."""
    rgb, a = _split(cg_rgba)
    p = srgb_to_linear(as_f32(plate, 'RGB'))
    obj = _smoothstep(solid_alpha[0], solid_alpha[1], a)
    open_ = 1 - obj
    bg = _gauss(p * open_, radius) / np.maximum(_gauss(open_, radius), 1e-4)
    reach = _gauss(open_, radius / 2) * obj            # 0.5 on the edge, falling to 0 ~radius inside
    m = np.clip(strength * 2 * reach * reach, 0, 1)
    lin = srgb_to_linear(rgb) * (1 - m) + bg * m
    return _join(linear_to_srgb(lin), a)


# ── 3. grade ──────────────────────────────────────────────────────────────────────────────────────────────────────
def plate_stats(plate, sky_box=None, dark_pct=1.0):
    """The plate's darkest dark_pct % (mean colour of those pixels, by luminance) and its sky colour (median of
    sky_box (x0, y0, x1, y1), or of its brightest 10 % — overcast sky is the brightest thing in frame). sRGB 0-255."""
    p = as_f32(plate, 'RGB')
    y = p @ LUMA
    dark = p[y <= np.percentile(y, dark_pct)].mean(0)
    if sky_box:
        x0, y0, x1, y1 = sky_box
        sky = np.median(p[y0:y1, x0:x1].reshape(-1, 3), 0)
    else:
        sky = np.median(p[y >= np.percentile(y, 90)], 0)
    return dark, sky


def cg_black(cg_rgba, dark_pct=1.0, min_alpha=0.5):
    """Mean colour of the darkest dark_pct % of the CG's solid pixels (alpha > min_alpha), sRGB 0-255."""
    rgb, a = _split(cg_rgba)
    px = rgb[a[..., 0] > min_alpha]
    y = px @ LUMA
    return px[y <= np.percentile(y, dark_pct)].mean(0)


def match_grade(plate, cg_rgba, sky_box=None, dark_pct=1.0, tint=0.5, white_cap=0.9, min_alpha=0.5):
    """Grade the CG into the plate's light, from the plate's own statistics.

    Real footage of a distant aircraft never holds a true black: haze and lens veiling glare add light to every
    pixel, so the plate's darkest 1 % is lifted and tinted. A clean render's black is ~0 and reads as pasted on.
    In linear light, per channel c, a contrast k and an additive veil v (what haze and glare physically do):
        out_c = k_c · cg_c + v_c,   v_c = plate_black_c - k_c · cg_black_lum,
        k_c = (target_white_c - plate_black_c) / (cg_white_c - cg_black_lum)
    cg_black is the CG's darkest dark_pct % (its luminance is the anchor, so a dark PAINT — a navy fin — keeps its
    hue instead of being forced to the plate's grey), cg_white its diffuse white — the mean of the 97-99th
    percentile, so a few specular pixels don't set the contrast (solid pixels only; speculars above it pass
    through). target_white is the CG's white, tinted toward the sky's colour by tint (0 = keep the CG's balance,
    1 = take the sky's cast fully) and capped at white_cap × the sky (a lit surface under overcast is darker than
    the sky itself). A neutral CG black lands exactly on the plate's darkest 1 %; the contrast fits between them
    and the sky. Returns RGBA float32 (alpha unchanged)."""
    rgb, a = _split(cg_rgba)
    pdark, sky = plate_stats(plate, sky_box, dark_pct)
    solid = a[..., 0] > min_alpha
    lin = srgb_to_linear(rgb)
    px = lin[solid]
    yl = px @ LUMA
    cb_l = float(px[yl <= np.percentile(yl, dark_pct)].mean(0) @ LUMA)
    lo_, hi_ = np.percentile(yl, [97, 99])
    cw = px[(yl >= lo_) & (yl <= hi_)].mean(0)
    pb, sk = srgb_to_linear(pdark), srgb_to_linear(sky)
    cw_l, sk_l = float(cw @ LUMA), float(sk @ LUMA)
    tw = cw_l * ((1 - tint) * cw / max(cw_l, 1e-6) + tint * sk / max(sk_l, 1e-6))
    tw = np.minimum(tw, white_cap * sk)
    tw = np.maximum(tw, pb + 1e-3)
    k = (tw - pb) / np.maximum(cw - cb_l, 1e-4)
    out = k * lin + (pb - k * cb_l)
    return _join(linear_to_srgb(out), a)


# ── 4. heat haze ──────────────────────────────────────────────────────────────────────────────────────────────────
_NOISE = {}


def _noise_tex(seed, size=512, cell=10.0):
    """Two tileable smooth-noise fields (unit std) for the x and y displacement, cached per seed."""
    if seed not in _NOISE:
        rng = np.random.default_rng(seed)
        tex = []
        for _ in range(2):
            n = (ndimage.gaussian_filter(rng.normal(size=(size, size)), cell, mode='wrap')
                 + 0.5 * ndimage.gaussian_filter(rng.normal(size=(size, size)), cell / 2.5, mode='wrap') * 2.5)
            tex.append((n / n.std()).astype(np.float32))
        _NOISE[seed] = tex
    return _NOISE[seed]


def heat_haze(img, nozzle_xy, direction, strength=4.0, t=0.0, length=260.0, width=10.0, spread=0.18,
              speed=420.0, seed=0, protect_alpha=None):
    """Hot exhaust bends light: what is seen through the plume shimmers. Screen-space refraction.

    img: the composite (RGB or RGBA). nozzle_xy: (x, y) or a list of them (twin engines). direction: a 2D vector
    pointing AFT along the plume on screen (normalised here). strength: peak displacement in px (3-6 reads at
    1080 wide). t: seconds — the turbulence is carried aft at `speed` px/s and evolves, so it is animated and
    deterministic. The plume starts width px wide, widens by spread px per px, and fades as exp(-distance/length)
    (the gas cools and mixes); it eases in over the first 12 px so the nozzle itself is not warped.
    protect_alpha: the CG alpha (H, W) 0-255 — no displacement on the aircraft itself.
    Returns the same channel count as img."""
    im = as_f32(img)
    pts = [nozzle_xy] if np.ndim(nozzle_xy) == 1 else list(nozzle_xy)
    d = np.asarray(direction, np.float32)
    d /= np.linalg.norm(d) + 1e-9
    nrm = np.array([-d[1], d[0]], np.float32)
    nx, ny = _noise_tex(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    disp_x = np.zeros((H, W), np.float32)
    disp_y = np.zeros((H, W), np.float32)
    for j, (px, py) in enumerate(pts):
        u = (xx - px) * d[0] + (yy - py) * d[1]          # distance aft of the nozzle
        v = (xx - px) * nrm[0] + (yy - py) * nrm[1]      # across the plume
        half = width / 2 + spread * np.maximum(u, 0)
        mask = _smoothstep(0, 12, u) * np.exp(-np.maximum(u, 0) / length) * np.exp(-(v / half) ** 2)
        if not mask.any():
            continue
        # noise in plume coordinates, advected aft, a second octave drifting slower (evolution, not just a scroll)
        su = (u - speed * t) * 0.9 + 97 * j
        sv = v * 0.9 + 31 * j
        s2u = (u - 0.45 * speed * t) * 1.7 + 211
        s2v = v * 1.7 + 59 * t * 10
        n1 = ndimage.map_coordinates(nx, [sv % 512, su % 512], order=1, mode='grid-wrap')
        n2 = ndimage.map_coordinates(ny, [sv % 512, su % 512], order=1, mode='grid-wrap')
        m1 = ndimage.map_coordinates(nx, [s2v % 512, s2u % 512], order=1, mode='grid-wrap')
        m2 = ndimage.map_coordinates(ny, [s2v % 512, s2u % 512], order=1, mode='grid-wrap')
        a_, b_ = (n1 + 0.6 * m1) / 1.17, (n2 + 0.6 * m2) / 1.17
        # displacement mostly ACROSS the flow (the gradient of a stretched density field)
        disp_x += strength * mask * (0.4 * a_ * d[0] + b_ * nrm[0])
        disp_y += strength * mask * (0.4 * a_ * d[1] + b_ * nrm[1])
    if protect_alpha is not None:
        keep = 1 - _smoothstep(0.05, 0.5, as_f32(protect_alpha).reshape(H, W) / 255.0)
        disp_x *= keep
        disp_y *= keep
    return _warp(im, xx + disp_x, yy + disp_y)


# ── 5. flare and glint ────────────────────────────────────────────────────────────────────────────────────────────
def lens_flare(img, light_xy, strength=0.35, seed=0, streak_angle_deg=0.0, tint=(1.0, 0.96, 0.88)):
    """A subtle flare from a bright point (a sun glint off a canopy, a hole in the overcast).

    light_xy may be off-frame. Built in linear light and added (light adds): a soft glow around the source, a thin
    streak through it at streak_angle_deg (lens coating / sensor smear), and ghosts — reflections between lens
    elements, which sit on the line from the source through the frame centre, mostly on the opposite side, in
    coating colours, bigger and fainter the further out. strength 0.2-0.5 is subtle; 1 is loud.
    Returns the same channel count as img (alpha untouched)."""
    im = as_f32(img)
    rgb = im[..., :3]
    rng = np.random.default_rng(seed)
    lx, ly = light_xy
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    tint = np.asarray(tint, np.float32)
    add = np.zeros((H, W, 3), np.float32)
    r = np.hypot(xx - lx, yy - ly)
    add += (0.5 * np.exp(-r / 60) + 0.12 * np.exp(-r / 260))[..., None] * tint
    th = math.radians(streak_angle_deg)
    along = (xx - lx) * math.cos(th) + (yy - ly) * math.sin(th)
    across = -(xx - lx) * math.sin(th) + (yy - ly) * math.cos(th)
    add += (0.22 * np.exp(-(across / 2.2) ** 2) * np.exp(-np.abs(along) / 420))[..., None] * np.array([0.85, 0.92, 1.0])
    cx, cy = W / 2, H / 2
    coat = [(0.55, 1.0, 0.65), (0.75, 0.6, 1.0), (1.0, 0.75, 0.45), (0.6, 0.9, 1.0), (0.8, 1.0, 0.6)]
    for k, f in enumerate([-0.35, -0.7, -1.05, -1.5, 0.45]):     # position along source→centre, 1 = centre … beyond
        gx, gy = cx + f * (lx - cx), cy + f * (ly - cy)
        rad = (40 + 70 * abs(f)) * rng.uniform(0.8, 1.25)
        dist = np.hypot(xx - gx, yy - gy) / rad
        disc = _smoothstep(1.0, 0.8, dist) * (0.55 + 0.45 * _smoothstep(0.5, 0.95, dist))   # brighter rim
        add += (0.14 / (1 + abs(f)) * disc)[..., None] * np.asarray(coat[k], np.float32)
    lin = srgb_to_linear(rgb) + strength * add
    # a highlight past 1.0 rolls into white instead of clipping one channel to a hue
    over_ = np.clip(lin.max(-1, keepdims=True) - 1, 0, None)
    lin = lin + over_ * 0.5
    out = linear_to_srgb(lin)
    return np.concatenate([out, im[..., 3:]], -1) if im.shape[-1] == 4 else out


def specular_glint(cg_rgba, strength=0.6, pct=99.7, min_level=200.0, sigmas=(2.0, 6.0, 18.0), min_alpha=0.5,
                   hdr_gain=8.0):
    """Bloom the CG's brightest highlights — a canopy catching the sky, a sun glint on a wing — the way a phone
    lens blooms a specular. Pixels above the brighter of the solid pixels' pct percentile luminance and min_level
    are the source. An 8-bit render clips a specular at 255 where the real one is many times brighter, so the
    source is scaled by hdr_gain before it is spread by Gaussians of sigmas (summed, wider = fainter) and screened
    back on, in linear light. The bloom spills outside the silhouette, so alpha is raised there: over() of the result
    equals a screen of the bloom over the plate. Returns RGBA float32."""
    rgb, a = _split(cg_rgba)
    lin = srgb_to_linear(rgb)
    y = rgb @ LUMA
    solid = a[..., 0] > min_alpha
    if not solid.any():
        return _join(rgb, a)
    thr = max(float(np.percentile(y[solid], pct)), min_level)
    src = lin * (_smoothstep(thr, min(thr + 30, 255), y) * a[..., 0])[..., None]
    src = src * 0.6 + (src @ LUMA)[..., None] * 0.4      # glints read whiter than the paint they sit on
    bloom = sum(_gauss(src, s) * w for s, w in zip(sigmas, (1.0, 0.6, 0.35))) * strength * hdr_gain
    bloom = np.clip(bloom, 0, 1)
    pre = lin * a
    pre_new = pre + bloom - pre * bloom                  # screen, premultiplied
    g = bloom.max(-1, keepdims=True)
    a_new = a + g - a * g
    lin_new = pre_new / np.maximum(a_new, 1e-6)
    return _join(linear_to_srgb(lin_new), a_new)


# ── 6. lens and sensor ────────────────────────────────────────────────────────────────────────────────────────────
def chromatic_aberration(img, px=1.0):
    """Lateral chromatic aberration: the lens magnifies red slightly more than blue, so edges far from the centre
    fringe red/cyan outward. R is scaled about the centre so it moves +px at the corners, B -px, G stays.
    The plate already has its own; this gives the CG (applied to the whole composite) the same. Bicubic."""
    im = as_f32(img)
    cx, cy = (W - 1) / 2, (H - 1) / 2
    rmax = math.hypot(cx, cy)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    out = im.copy()
    for ch, sgn in ((0, 1), (2, -1)):
        s = 1 + sgn * px / rmax
        out[..., ch] = ndimage.map_coordinates(im[..., ch], [cy + (yy - cy) / s, cx + (xx - cx) / s],
                                               order=3, mode='nearest')
    return out


def vignette(img, amount=0.25):
    """Natural vignetting: the cos^4 law of illumination off-axis, in linear light. The equivalent focal length is
    chosen so the corners get (1 - amount) of the centre's light; the falloff is smooth and has no hard edge."""
    im = as_f32(img)
    cx, cy = (W - 1) / 2, (H - 1) / 2
    rc = math.hypot(cx, cy)
    # cos^4(atan(rc / f)) = 1 - amount  →  f
    c = (1 - amount) ** 0.25
    f = rc * c / math.sqrt(max(1 - c * c, 1e-9))
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    g = np.cos(np.arctan(np.hypot(xx - cx, yy - cy) / f)) ** 4
    out = im.copy()
    out[..., :3] = linear_to_srgb(srgb_to_linear(im[..., :3]) * g[..., None])
    return out


def measure_noise(plate, tile=48, flat_frac=0.08, smooth=4.0):
    """The plate's noise: (sigma per channel in sRGB code values, grain sigma in px, (x, y) of the flattest patch).

    The plate is cut into tile² patches; the residual of each (patch minus its Gaussian(smooth) blur) is texture +
    noise. Among patches of the brightest half (sky — where a viewer sees grain against CG), the flat_frac flattest
    are taken as noise-only; sigma is their median residual std, corrected for what the blur removes. The grain's
    spatial size comes from the residual's lag-1 autocorrelation: phone noise is denoised and compressed into
    blotches, not per-pixel, and white noise at the right sigma still looks wrong."""
    p = as_f32(plate, 'RGB')
    res = p - _gauss(p, smooth)
    lum = p @ LUMA
    cand = []
    for y0 in range(0, H - tile + 1, tile):
        for x0 in range(0, W - tile + 1, tile):
            r = res[y0:y0 + tile, x0:x0 + tile]
            cand.append((float((r @ LUMA).std()), float(lum[y0:y0 + tile, x0:x0 + tile].mean()), x0, y0))
    bright = np.median([c[1] for c in cand])
    cand = sorted([c for c in cand if c[1] >= bright])
    pick = cand[:max(3, int(len(cand) * flat_frac))]
    patches = [res[y0:y0 + tile, x0:x0 + tile] for _, _, x0, y0 in pick]
    sig = np.median([pt.reshape(-1, 3).std(0) for pt in patches], 0)
    rho = []
    for pt in patches:
        g = pt @ LUMA
        g = g - g.mean()
        rho.append(0.5 * ((g[:, 1:] * g[:, :-1]).mean() + (g[1:] * g[:-1]).mean()) / max(g.var(), 1e-9))
    rho = float(np.clip(np.median(rho), 0, 0.95))
    # white noise blurred by a Gaussian of sigma s has lag-1 correlation exp(-1/(4 s²))
    gs = 0.0 if rho < 0.02 else math.sqrt(-1 / (4 * math.log(rho)))
    # the residual misses the share of the noise the blur keeps (more of it the coarser the grain): measured on
    # synthetic grain of the same size, through the same filter
    syn = np.random.default_rng(1).normal(size=(256, 256)).astype(np.float32)
    syn = _gauss(syn, gs) if gs > 0.05 else syn
    sig = sig * float(syn.std() / (syn - _gauss(syn, smooth))[16:-16, 16:-16].std())
    return sig.astype(np.float32), gs, (pick[0][2], pick[0][3])


def match_grain(plate, img, mask=None, seed=0, sigma=None, grain_px=None, mono=0.6):
    """Add the plate's grain to img (the composite) where the CG is.

    sigma (per channel) and grain_px default to measure_noise(plate). mask (H, W) 0-255 — normally the CG alpha —
    limits the grain to pixels the plate's own noise doesn't already cover (adding it everywhere would double the
    plate's); None = the whole frame. mono: share of the noise common to all channels (phone noise is mostly
    luminance after the ISP). Seed per frame: the grain must change every frame."""
    im = as_f32(img)
    if sigma is None or grain_px is None:
        s_, g_, _ = measure_noise(plate)
        sigma = s_ if sigma is None else np.asarray(sigma, np.float32)
        grain_px = g_ if grain_px is None else grain_px
    rng = np.random.default_rng(seed)

    def field(shape):
        n = rng.normal(size=shape).astype(np.float32)
        if grain_px > 0.05:
            n = _gauss(n, grain_px)
        return n / (n.std() + 1e-9)

    common = field((H, W))
    noise = np.stack([math.sqrt(mono) * common + math.sqrt(1 - mono) * field((H, W)) for _ in range(3)], -1)
    noise *= np.asarray(sigma, np.float32)
    m = 1.0 if mask is None else (as_f32(mask).reshape(H, W) / 255.0)[..., None]
    out = im.copy()
    out[..., :3] = im[..., :3] + noise * m
    return out
