# flyby-sound

Synthesises what an aircraft passing a listener sounds like, from its path:

- Sound arrives late: emission time is solved per sample.
- Doppler pitch shift.
- Distance falloff.
- Air absorption.
- The echo off the ground: the comb or flange you hear when a jet passes low.
- Directivity: loud behind a jet, fan whine in front.

Types: `f16` (single turbofan), `c17` (four high-bypass turbofans), `glider` (wind only).

```bash
python3 sound_fx.py --path path.json --type f16 --out pass.wav
python3 sound_fx.py --path path.json --type c17 --out pass.wav --real phone_clip.mov --real-start 3.5
```

`path.json` is the aircraft position per video frame relative to the listener, in metres (x right, y forward, z up). `--real` mixes your real recording underneath and ducks it as the aircraft passes. Output is 48 kHz WAV, never clipped. Needs numpy, scipy and ffmpeg.
