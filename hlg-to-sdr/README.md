# hlg-to-sdr

Converts iPhone HDR video (HLG, BT.2020) to normal SDR PNG frames. Many ffmpeg builds have no `zscale` filter, so plain ffmpeg turns iPhone HDR footage washed-out grey; this does the conversion by hand.

```bash
python3 hlg_to_sdr.py IMG_1234.MOV frames/          # all frames
python3 hlg_to_sdr.py IMG_1234.MOV frames/ 2.3 4.0 120   # gain 2.3, from 4.0 s, 120 frames
```

Output is 1080 × 1920 portrait. Change `W, H` for landscape clips. Needs numpy and ffmpeg.
