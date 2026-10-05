# video-helpers (macOS, Swift, no ffmpeg needed)

```bash
swift frame.swift in.mov 3.5 frame.png                        # one frame at 3.5 s as PNG
swift phoneframe.swift in.mov out.mp4 --label "My App"         # draw a phone bezel around a screen recording
swift sidebyside.swift a.mov b.mov out.mp4 --left-label "A" --right-label "B"   # split-screen of two recordings
```

They use AVFoundation, which every Mac has. Run each script's header for all options.
