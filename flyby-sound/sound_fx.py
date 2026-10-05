"""Physically motivated aircraft flyby sound for the marketing edits.

    python3 sound_fx.py --path path.json --type f16 --out x.wav
                        [--real clip.MOV --real-start s] [--ground-h m] [--afterburner] [--duck-db 9]

Output: 48 kHz float, mono or stereo.

Model, per sample of the output:
  1. The aircraft is a set of sources (jet mixing noise, fine-scale noise, rumble, fan tones, crackle,
     wind) synthesised in EMISSION time tau, each with its own directivity around the aircraft's nose.
  2. Two propagation paths: direct, and a ground reflection (the image source mirrored in the ground
     plane z = -ground_h). For each path the emission time of every received sample is solved from
     t = tau + r(tau)/c and the emitted signal is read at tau(t). Arrival delay and Doppler both fall out
     of that one resampling; pressure also scales by the Doppler factor dtau/dt (convective amplification
     of a moving monopole).
  3. Spreading 1/r (capped at a near-field radius), air absorption per octave band (ISO 9613-1 at 20 C,
     70 % RH, evaluated at the Doppler-shifted frequency), a frequency-dependent ground reflection
     coefficient. Direct + reflected interfere: the comb / flange of a low pass.
  4. Constant-power stereo pan from the direction each path arrives from.

Coordinates: listener at the origin, x right, y forward (where the listener faces), z up, metres.
Path JSON: a list of [x, y, z] per frame at FPS (30000/1001), or {"pos": [...], "fwd": [...]?,
"throttle": [...] | number?, "fps": number?}. Frame k is at time k / fps in the video.
"""
import argparse
import json
import pathlib
import subprocess
import sys

import numpy as np
from scipy.fft import next_fast_len
from scipy.interpolate import CubicSpline
from scipy.io import wavfile

SR = 48000
FPS = 30000 / 1001
C = 343.0
BANDS_HZ = np.array([63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000], float)
# ISO 9613-1 pure-tone absorption, 20 C, 70 % RH, dB/km, at BANDS_HZ (16 kHz extrapolated)
ALPHA_DB_KM = np.array([0.09, 0.34, 1.13, 2.80, 4.98, 9.02, 22.9, 76.6, 270.0])
GROUND_R = {   # pressure reflection coefficient per band: hard ground reflects all; grass loses the highs
    'hard': np.full(9, 0.95),
    'grass': np.array([0.95, 0.92, 0.88, 0.8, 0.7, 0.6, 0.5, 0.45, 0.4]),
    'none': np.zeros(9),
}
NEAR_M = {'f16': 6.0, 'c17': 20.0, 'glider': 3.0, 'probe': 1.0}   # near-field cap ~ the source's size


# ───────────────────────────── path ─────────────────────────────

def path_functions(pos, fwd=None, fps=FPS):
    """Turn per-frame data into continuous functions of time.

    pos: (N,3) array of frame positions, or a callable pos(t) -> (len(t),3) valid for ANY t (the emission
    times reach before the first frame, by the travel time of the sound). Frames are joined by a cubic
    spline (continuous velocity, so Doppler has no frame-rate steps) and extended outside the frames in
    a straight line at the end velocity.
    fwd: (N,3) nose direction per frame, a callable, or None = the velocity direction.
    Returns (pos_fn, fwd_fn, t_last) where t_last is the last frame's time (None for callables)."""
    if callable(pos):
        pos_fn, t_last = pos, None
    else:
        p = np.asarray(pos, float)
        tk = np.arange(len(p)) / fps
        sp = CubicSpline(tk, p, axis=0)
        v0, v1 = sp(tk[0], 1), sp(tk[-1], 1)
        t_last = tk[-1]

        def pos_fn(t):
            t = np.asarray(t, float)
            out = sp(np.clip(t, tk[0], tk[-1]))
            lo, hi = t < tk[0], t > tk[-1]
            out[lo] = p[0] + (t[lo] - tk[0])[:, None] * v0
            out[hi] = p[-1] + (t[hi] - tk[-1])[:, None] * v1
            return out
    if fwd is None:
        fwd_fn = None
    elif callable(fwd):
        fwd_fn = fwd
    else:
        f = np.asarray(fwd, float)
        tk = np.arange(len(f)) / fps
        fwd_fn = lambda t: np.stack([np.interp(t, tk, f[:, i]) for i in range(3)], 1)
    return pos_fn, fwd_fn, t_last


def unit(v, fallback=(0.0, 1.0, 0.0)):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.where(n > 1e-6, v / np.maximum(n, 1e-9), np.asarray(fallback))


# ───────────────────────────── sources ─────────────────────────────

def shaped_noise(rng, m, mag_fn):
    """Gaussian noise of m samples with spectral magnitude mag_fn(f), zero phase filtering, unit RMS."""
    f = np.fft.rfftfreq(m, 1 / SR)
    x = np.fft.irfft(np.fft.rfft(rng.standard_normal(m)) * mag_fn(f), m)
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-12)


def hump(fp, rise=1.0, fall=1.0):
    """Broadband jet-noise shape: amplitude ~ f^rise below the peak fp, ~ f^-fall above it (Tam's
    large-scale similarity spectrum is close to rise 1, fall 1, i.e. +6 / -6 dB per octave)."""
    return lambda f: (f / fp) ** rise / (1 + (f / fp) ** (rise + fall))


def wander(rng, m):
    """Slow (< 2 Hz) unit-RMS random wander, for a rotor speed that is never perfectly steady."""
    return shaped_noise(rng, m, lambda f: (f < 2.0).astype(float))


def tone(inst_hz, rng, jitter=0.002, w=None):
    """Sinusoid following inst_hz (array) by phase accumulation; its frequency wanders by jitter (RMS
    fraction) along w (default: a fresh wander). Tones of one shaft share its w."""
    w = wander(rng, len(inst_hz)) if w is None else w
    ph = 2 * np.pi * np.cumsum(inst_hz * (1 + jitter * w)) / SR
    return np.sin(ph + rng.uniform(0, 2 * np.pi))


def crackle(rng, m, rate_hz=70.0):
    """Afterburner crackle: sparse, heavy-tailed, positively skewed pressure spikes (shock-like N
    waves from the supersonic mixing layer), high-passed above ~800 Hz."""
    imp = np.zeros(m)
    k = rng.random(m) < rate_hz / SR
    imp[k] = rng.pareto(2.2, k.sum()) + 0.3
    ker = np.concatenate([np.linspace(0, 1, 4), np.linspace(1, -0.45, 10), np.linspace(-0.45, 0, 30)])
    x = np.convolve(imp, ker, mode='same')
    x = np.fft.irfft(np.fft.rfft(x) * (lambda f: 1 / np.sqrt(1 + (800 / np.maximum(f, 1)) ** 4))(
        np.fft.rfftfreq(m, 1 / SR)), m)
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-12)


def gauss_db(peak_deg, width_deg, floor_db):
    """Directivity in dB vs theta (0 = nose, 180 = tail): floor_db off-lobe rising to 0 dB at peak_deg."""
    return lambda th: floor_db * (1 - np.exp(-((th - peak_deg) / width_deg) ** 2))


DIR = {
    # jet mixing noise: peaks ~145 deg from the nose (30-40 deg off the jet axis), cone of silence behind
    'aft': gauss_db(145, 45, -12),
    # fan inlet tones radiate forward (inlet lobe ~50 deg), weaker aft lobe from the bypass duct
    'fwd': lambda th: np.maximum(gauss_db(50, 40, -18)(th), gauss_db(125, 25, -18)(th) - 6),
    # fine-scale turbulence: nearly omnidirectional
    'omni': lambda th: -2.0 * (1 - np.cos(np.radians(th))) / 2,
    # airframe whoosh: dipole-ish, loudest broadside
    'side': lambda th: 10 * np.log10(0.35 + 0.65 * np.sin(np.radians(th)) ** 2),
}


def source_components(kind, tau, speed, throttle, rng, afterburner=False, probe_hz=1000.0):
    """The aircraft's sources in emission time tau. Returns [(name, signal, directivity_key)].

    f16:  one F110 turbofan. Jet mixing noise (aft, peak ~250 Hz, ~180 Hz and louder in afterburner),
          fine-scale noise (~1.5 kHz, omni), low rumble (<80 Hz, aft), a weak turbine whine (fwd),
          crackle in afterburner. Level ~ throttle^4 (jet noise power ~ Vj^8, Vj ~ throttle).
    c17:  four F117 high-bypass turbofans. Fan blade-passing tones near 2.2 / 4.4 kHz (36 blades at
          ~3600 rpm, N1 follows throttle) with the four engines a few tenths of a percent apart so they
          beat, buzz-saw (shaft-order harmonics 1.2-4.8 kHz, random fixed amplitudes), and a softer,
          lower jet roar (~150 Hz) because the bypass jet is slow.
    glider: airframe wind only: band noise 150 Hz-4 kHz, turbulent amplitude flicker, power ~ V^5.
    probe:  a pure tone at probe_hz, omni (for measuring delay and Doppler)."""
    m = len(tau)
    thr = np.clip(throttle, 0.05, 1.2)
    out = []
    if kind == 'probe':
        return [('tone', np.sin(2 * np.pi * probe_hz * (tau - tau[0])), 'omni')]
    if kind == 'f16':
        lvl = thr ** 4
        fp = 180.0 if afterburner else 250.0
        ab = 1.8 if afterburner else 1.0
        out.append(('jet_mix', shaped_noise(rng, m, hump(fp, 1.0, 1.0)) * lvl * ab, 'aft'))
        out.append(('jet_fine', shaped_noise(rng, m, hump(1500, 1.2, 1.3)) * 0.35 * lvl, 'omni'))
        out.append(('rumble', shaped_noise(rng, m, hump(35, 1.0, 3.0)) * 0.6 * lvl * ab, 'aft'))
        n1 = 0.55 + 0.45 * thr
        out.append(('turbine', (tone(1250 * n1, rng) + 0.35 * tone(2500 * n1, rng)) * 0.06 * thr ** 2, 'fwd'))
        if afterburner:
            out.append(('crackle', crackle(rng, m) * 0.5, 'aft'))
    elif kind == 'c17':
        lvl = thr ** 4
        out.append(('jet_mix', shaped_noise(rng, m, hump(150, 1.0, 1.0)) * 0.55 * lvl, 'aft'))
        out.append(('jet_fine', shaped_noise(rng, m, hump(1200, 1.2, 1.3)) * 0.25 * lvl, 'omni'))
        n1 = 0.55 + 0.45 * thr
        fan = np.zeros(m)
        buzz = np.zeros(m)
        for det in (1.0, 1.0035, 0.9972, 1.0061):        # four engines, slightly different N1
            shaft = 60.0 * n1 * det
            w = wander(rng, m)
            fan += tone(36 * shaft, rng, 0.001, w) + 0.5 * tone(72 * shaft, rng, 0.001, w)
            orders = np.arange(20, 81, 3)
            amps = rng.lognormal(0, 0.6, len(orders)) * 20 / orders
            for order, a_ in zip(orders, amps):
                buzz += a_ * tone(order * shaft, rng, 0.001, w)
        out.append(('fan_tones', fan / 4 * 0.12 * thr ** 2, 'fwd'))
        out.append(('buzzsaw', buzz / (np.std(buzz) + 1e-9) * 0.05 * thr ** 3, 'fwd'))
    elif kind == 'glider':
        lvl = (np.maximum(speed, 0.1) / 20.0) ** 2.5
        flick = 1 + 0.35 * shaped_noise(rng, m, lambda f: ((f > 0.3) & (f < 6)).astype(float))
        bp = lambda f: 1 / np.sqrt(1 + (150 / np.maximum(f, 1)) ** 4) / np.sqrt(1 + (f / 4000) ** 4)
        out.append(('whoosh', shaped_noise(rng, m, bp) * flick * lvl, 'side'))
        out.append(('rush', shaped_noise(rng, m, hump(500, 1.0, 1.5)) * 0.5 * lvl, 'omni'))
    else:
        raise ValueError(f'unknown aircraft type {kind!r}')
    return out


# ───────────────────────────── propagation ─────────────────────────────

def band_weights(f):
    """Partition of unity over BANDS_HZ: linear hats in log2 f, so the bands sum back to the input."""
    lf = np.log2(np.clip(f, BANDS_HZ[0], BANDS_HZ[-1]))
    lc = np.log2(BANDS_HZ)
    w = np.zeros((len(BANDS_HZ), len(f)))
    for i in range(len(lc)):
        if i > 0:
            s = (lf >= lc[i - 1]) & (lf <= lc[i])
            w[i, s] = (lf[s] - lc[i - 1]) / (lc[i] - lc[i - 1])
        if i < len(lc) - 1:
            s = (lf >= lc[i]) & (lf < lc[i + 1])
            w[i, s] = (lc[i + 1] - lf[s]) / (lc[i + 1] - lc[i])
    w[0, f <= BANDS_HZ[0]] = 1
    w[-1, f >= BANDS_HZ[-1]] = 1
    return w


def air_absorption_db_per_m(f):
    """ISO 9613-1 absorption (20 C, 70 % RH) at frequency f (Hz, array), dB per metre."""
    return np.interp(np.log2(np.maximum(f, 20)), np.log2(BANDS_HZ), ALPHA_DB_KM) / 1000


def catmull_rom(x, u):
    """Read x at fractional sample indices u (4-point cubic; flatter at high frequencies than linear)."""
    i = np.floor(u).astype(int)
    fr = u - i
    g = lambda k: x[np.clip(i + k, 0, len(x) - 1)]
    p0, p1, p2, p3 = g(-1), g(0), g(1), g(2)
    return p1 + 0.5 * fr * (p2 - p0 + fr * (2 * p0 - 5 * p1 + 4 * p2 - p3 + fr * (3 * (p1 - p2) + p3 - p0)))


def retarded_time(tau, src, ear, t):
    """Solve t = tau + |src(tau) - ear| / c for tau at each received time t (sound slower than the source
    is impossible here: a supersonic source makes the arrival time non-monotonic and is refused).
    Returns (tau(t), r(t), doppler = dtau/dt, the source position at emission)."""
    d = src - ear
    r = np.linalg.norm(d, axis=1)
    arrive = tau + r / C
    if np.any(np.diff(arrive) <= 0):
        raise ValueError('source is supersonic toward the listener: retarded time is not unique')
    te = np.interp(t, arrive, tau)
    dop = np.gradient(te, 1 / SR)
    rr = np.interp(t, arrive, r)
    pe = np.stack([np.interp(t, arrive, src[:, k]) for k in range(3)], 1)
    return te, rr, dop, pe


def render(pos, kind='f16', duration=None, fwd=None, throttle=1.0, fps=FPS, ground_h=1.5,
           ground='grass', afterburner=False, stereo=True, seed=1, near_m=None, convective=1.0,
           probe_hz=1000.0, return_debug=False):
    """Synthesize what a listener at the origin hears, sample-aligned to the video (t = 0 at frame 0).

    pos, fwd: see path_functions. throttle: number, per-frame list or callable (0-1, 1 = full mil power).
    duration: seconds of output (default: the frames' span). ground_h: listener ear height above the
    ground (m); ground: 'grass' | 'hard' | 'none'. near_m: 1/r cap radius (default per type).
    convective: exponent on the Doppler factor applied to amplitude (1 = moving monopole, 0 = off).
    Returns float32 (n,2) if stereo else (n,), in arbitrary physical-ish units (normalise later);
    with return_debug, also a dict of per-sample r, doppler and emission positions for the direct path."""
    pos_fn, fwd_fn, t_last = path_functions(pos, fwd, fps)
    if duration is None:
        if t_last is None:
            raise ValueError('duration is required when pos is a callable')
        duration = t_last
    n = int(round(duration * SR))
    t = np.arange(n) / SR
    near = NEAR_M[kind] if near_m is None else near_m
    ear = np.zeros(3)
    ear_img = np.array([0.0, 0.0, -2 * ground_h])     # listener mirrored in the ground = image-source geometry

    # emission grid: early enough that the first received sample's sound was emitted inside it
    p0 = pos_fn(np.array([0.0]))[0]
    t0 = -(np.linalg.norm(p0 - ear_img) / C) * 1.6 - 0.5
    for _ in range(8):
        r_start = np.linalg.norm(pos_fn(np.array([t0]))[0] - ear_img)
        if t0 + r_start / C < -0.05:
            break
        t0 -= 2 * r_start / C + 1.0
    tau = t0 + np.arange(next_fast_len(int((duration - t0) * SR) + 4, real=True)) / SR   # FFT-friendly length
    src = pos_fn(tau)
    vel = np.gradient(src, 1 / SR, axis=0)
    speed = np.linalg.norm(vel, axis=1)
    nose = unit(fwd_fn(tau)) if fwd_fn else unit(vel)
    if callable(throttle):
        thr = np.asarray(throttle(tau), float)
    elif np.ndim(throttle):
        thr_k = np.asarray(throttle, float)
        thr = np.interp(tau, np.arange(len(thr_k)) / fps, thr_k)
    else:
        thr = np.full(len(tau), float(throttle))
    rng = np.random.default_rng(seed)
    comps = source_components(kind, tau, speed, thr, rng, afterburner, probe_hz)

    f = np.fft.rfftfreq(len(tau), 1 / SR)
    W = band_weights(f)
    R = GROUND_R[ground]
    out = np.zeros((n, 2))
    dbg = {}
    for name, ear_p, refl in (('direct', ear, None), ('ground', ear_img, R)):
        if refl is not None and not np.any(refl):
            continue
        to_ear = ear_p - src
        theta = np.degrees(np.arccos(np.clip(np.sum(unit(to_ear) * nose, 1), -1, 1)))
        emitted = sum(sig * 10 ** (DIR[key](theta) / 20) for _, sig, key in comps)
        te, rr, dop, pe = retarded_time(tau, src, ear_p, t)
        u = (te - tau[0]) * SR
        spec = np.fft.rfft(emitted)
        y = np.zeros(n)
        for b, fc in enumerate(BANDS_HZ):
            g = 10 ** (-air_absorption_db_per_m(fc * dop) * rr / 20)
            if refl is not None:
                g = g * refl[b]
            y += catmull_rom(np.fft.irfft(spec * W[b], len(tau)), u) * g
        y *= near / np.maximum(rr, near) * dop ** convective
        if name == 'ground':
            pe = pe * np.array([1, 1, -1]) + np.array([0, 0, -2 * ground_h])   # arrives from the image
        pan = np.clip(pe[:, 0] / np.maximum(np.linalg.norm(pe, axis=1), 1e-6), -1, 1)
        a = (pan + 1) * np.pi / 4
        out[:, 0] += y * np.cos(a)
        out[:, 1] += y * np.sin(a)
        if name == 'direct':
            dbg = {'t': t, 'r': rr, 'doppler': dop, 'emit_t': te, 'theta_deg': np.interp(te, tau, theta)}
    out = out.astype(np.float32)
    if not stereo:
        out = (out.sum(1) / np.sqrt(2)).astype(np.float32)
    return (out, dbg) if return_debug else out


# ───────────────────────────── mixing ─────────────────────────────

def soft_limit(x, ceiling=10 ** (-1 / 20), knee=0.8):
    """Identity below knee*ceiling, a tanh shoulder above it with slope 1 at the knee that approaches but
    never reaches ceiling. No hard clipping, nothing touched below the knee."""
    k = knee * ceiling
    y = np.array(x, np.float64)
    a = np.abs(y)
    o = a > k
    y[o] = np.sign(y[o]) * (k + (ceiling - k) * np.tanh((a[o] - k) / (ceiling - k)))
    return y.astype(np.float32)


def normalize_peak(x, peak_dbfs=-1.0):
    """Scale so the largest |sample| is peak_dbfs."""
    m = float(np.max(np.abs(x)))
    return (x * (10 ** (peak_dbfs / 20) / m)).astype(np.float32) if m > 0 else x


def load_audio(path, start=0.0, duration=None, channels=2):
    """Any file ffmpeg reads (.MOV, .wav, .m4a) -> float32 (n, channels) at SR, from start s."""
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{start:.6f}', '-i', str(path), '-vn']
    if duration is not None:
        cmd += ['-t', f'{duration:.6f}']
    raw = subprocess.run(cmd + ['-f', 'f32le', '-ac', str(channels), '-ar', str(SR), '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, channels).copy()


def envelope_follow(x, attack_s=0.03, release_s=0.4):
    """Peak envelope with separate attack and release (one-pole, block of 64 samples)."""
    a = np.abs(x if x.ndim == 1 else x.max(1))
    blk = 64
    pk = np.array([a[i:i + blk].max() for i in range(0, len(a), blk)])
    ka, kr = np.exp(-blk / (attack_s * SR)), np.exp(-blk / (release_s * SR))
    e = np.empty_like(pk)
    acc = 0.0
    for i, v in enumerate(pk):
        acc = (ka if v > acc else kr) * (acc - v) + v
        e[i] = acc
    return np.repeat(e, blk)[:len(a)]


def breakpoints(n, points, default=0.0):
    """Gain in dB per sample from [(t_s, gain_db), ...], linear between points, held outside."""
    if not points:
        return np.full(n, default)
    ts, gs = zip(*sorted(points))
    return np.interp(np.arange(n) / SR, ts, gs)


def mix(real_wav, synth, duck_db=9.0, fade_points=None, real_gain_db=0.0, real_fade_points=None,
        peak_dbfs=-1.0):
    """Real phone audio (crowd, wind) under the synthetic aircraft.

    real_wav: array (n,2)/(n,) at SR, or a path (read from 0 s; use load_audio for an offset).
    synth: render() output. It is peak-normalised to peak_dbfs first, then shaped by fade_points
    [(t_s, gain_db), ...]. The real track is set by real_gain_db / real_fade_points and ducked by up to
    duck_db, following the synth's envelope (fast attack, slow release) so the crowd dips under the jet
    the way a phone's AGC makes it. The sum goes through soft_limit at peak_dbfs: never clips."""
    s = normalize_peak(np.asarray(synth, np.float32), peak_dbfs).astype(np.float64)
    if s.ndim == 1:
        s = np.stack([s, s], 1) / np.sqrt(2)
    n = len(s)
    s *= 10 ** (breakpoints(n, fade_points) / 20)[:, None]
    r = load_audio(real_wav) if isinstance(real_wav, (str, pathlib.Path)) else np.asarray(real_wav, np.float64)
    if r.ndim == 1:
        r = np.stack([r, r], 1)
    rr = np.zeros((n, 2))
    rr[:min(n, len(r))] = r[:n]
    rr *= 10 ** ((real_gain_db + breakpoints(n, real_fade_points)) / 20)[:, None]
    e = envelope_follow(s)
    rr *= 10 ** (-duck_db * np.clip(e / (e.max() + 1e-12), 0, 1) / 20)[:, None]
    return soft_limit(s + rr, 10 ** (peak_dbfs / 20))


def write_wav(path, x):
    """float32 WAV at SR."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), SR, np.asarray(x, np.float32))


def load_path_json(path):
    """Path JSON -> (pos, fwd, throttle, fps). A bare list is positions only."""
    d = json.loads(pathlib.Path(path).read_text())
    if isinstance(d, list):
        return d, None, 1.0, FPS
    return d['pos'], d.get('fwd'), d.get('throttle', 1.0), d.get('fps', FPS)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--path', required=True)
    ap.add_argument('--type', choices=('f16', 'c17', 'glider'), default='f16')
    ap.add_argument('--out', required=True)
    ap.add_argument('--real', help='phone recording (.MOV/.wav) to lay under the synth')
    ap.add_argument('--real-start', type=float, default=0.0, help='s into --real that lines up with frame 0')
    ap.add_argument('--real-gain-db', type=float, default=0.0)
    ap.add_argument('--duck-db', type=float, default=9.0)
    ap.add_argument('--ground-h', type=float, default=1.5)
    ap.add_argument('--ground', choices=tuple(GROUND_R), default='grass')
    ap.add_argument('--afterburner', action='store_true')
    ap.add_argument('--mono', action='store_true')
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args(argv)
    pos, fwd, thr, fps = load_path_json(a.path)
    y = render(pos, a.type, fwd=fwd, throttle=thr, fps=fps, ground_h=a.ground_h, ground=a.ground,
               afterburner=a.afterburner, stereo=not a.mono, seed=a.seed)
    if a.real:
        real = load_audio(a.real, a.real_start, len(y) / SR)
        y = mix(real, y, a.duck_db, real_gain_db=a.real_gain_db)
    else:
        y = soft_limit(normalize_peak(y, -1.0) * 1.0)
    if not np.all(np.isfinite(y)):
        sys.exit('non-finite samples in the output')
    write_wav(a.out, y)
    print(f'{a.out}: {len(y) / SR:.2f} s, {"mono" if y.ndim == 1 else "stereo"}, peak {np.max(np.abs(y)):.3f}')


if __name__ == '__main__':
    main()
