# composite-fx

A library of 2D effects for putting CG (rendered RGBA frames) over real phone footage so it stops looking pasted on:

- `match_grade`: lift and tint the CG's blacks and whites to the plate's own.
- `light_wrap`: let the background's light soften the CG's edges.
- `specular_glint`: bloom the brightest highlights.
- `heat_haze`: animated exhaust shimmer.
- `lens_flare`.
- `plate_motion` / `apply_shake` / `synthetic_shake`: measure the real camera shake and apply it to the CG.
- `vignette`, `chromatic_aberration`.
- `match_grain`: measure the footage's own sensor noise and add exactly that.

```python
import post_fx
cg = post_fx.match_grade(plate, cg)
cg = post_fx.light_wrap(plate, cg, radius=3)
out = post_fx.over(plate, cg)
out = post_fx.match_grain(plate, out, mask=cg[..., 3], seed=frame_number)
```

Frames are 1080 × 1920 by default (`W`, `H` at the top). Needs numpy, scipy and Pillow.
