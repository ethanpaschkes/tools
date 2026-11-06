# video-review

Watch video clips on your phone, rate them, and leave comments tied to the exact moment. Then read all of that back from
the command line. One Python file and one HTML page, with nothing to install.

## Run it

```bash
python3 review.py serve --root ~/video-review --port 8796 --token "a-long-secret-of-16-or-more-characters"
```

Open `http://127.0.0.1:8796/<token>/`. Every page and video sits behind the token, and any other address gets a 404.
The server listens on this computer only. To reach it from a phone, put a tunnel in front of it, such as Tailscale Serve or Funnel.

## Add a batch

```bash
python3 review.py add --root ~/video-review my-batch clip1.mp4 clip2.mp4 --title "My batch"
```

A batch is just a folder in `<root>/batches/`, so copying videos in by hand works too. The optional `batch.json` adds titles and notes:

```json
{"title": "My batch", "note": "Shown under the title", "clips": {"clip1.mp4": {"title": "First idea", "note": "Why it's here"}}}
```

Copy the videos in instead of linking them: macOS blocks background services from reading `~/Downloads`, even through a link.
Keep videos H.264 MP4 with `-movflags +faststart` so they start quickly on a phone.

## Read the feedback

```bash
python3 review.py read --root ~/video-review my-batch          # best rated first
python3 review.py read --root ~/video-review my-batch --json   # for scripts
```

It's stored in each batch's `feedback.json`. Saves that fail on a bad connection stay in the browser and retry by themselves.
