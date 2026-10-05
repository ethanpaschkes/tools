# screenshot-frame

Draws an iPhone-style body (rails, buttons, shadow) around iOS Simulator screenshots on a transparent background, ready for a pitch deck or README.

```bash
python3 frame-screenshot.py shots/                  # every PNG in a folder, portrait
python3 frame-screenshot.py shots/ --landscape      # landscape captures (simctl writes them sideways)
python3 frame-screenshot.py one.png -o framed.png
```

Needs Pillow.
