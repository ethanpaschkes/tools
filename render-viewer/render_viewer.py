"""Live viewer for image-sequence renders (Blender, ffmpeg, anything that writes numbered frames).

    python3 render_viewer.py FOLDER [FOLDER ...] [--port 8765] [--hours 6] [--pattern '^f\\d+\\.png$']

Watches every sub-folder of the given folders that holds numbered frames (default: f0001.png, f0002.png ...).
The page (http://localhost:PORT) polls every 2 s, lists the folders newest first with their frame counts, and shows
the chosen folder's newest frame large over a checkerboard (transparent renders show as transparent), a strip of
recent frames, Play / speed / scrub and arrow-key stepping.

Read-only and local-only: it listens on 127.0.0.1, serves only matching images from inside the given folders, and
never writes anything. A frame written in the last 1.5 s is held back so a half-written file is never shown.
"""
import argparse
import json
import os
import pathlib
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ap = argparse.ArgumentParser(description='Live viewer for numbered render frames.')
ap.add_argument('folders', nargs='+', help='folders to watch (searched recursively)')
ap.add_argument('--port', type=int, default=8765)
ap.add_argument('--hours', type=float, default=6.0, help='only list folders written to in the last N hours')
ap.add_argument('--pattern', default=r'^f\d+\.png$', help='regex a frame file name must match')
args = ap.parse_args()

ROOTS = [pathlib.Path(f).expanduser().resolve() for f in args.folders]
PAGE = pathlib.Path(__file__).resolve().parent / 'render_viewer.html'
FRAME = re.compile(args.pattern)
FRESH_S = args.hours * 3600
TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}


def within_roots(p):
    return any(p == r or r in p.parents for r in ROOTS)


def frame_dirs():
    out = []
    now = time.time()
    for root in ROOTS:
        if not root.exists():
            continue
        for d, _, files in os.walk(root):
            frames = sorted(pathlib.Path(d) / f for f in files if FRAME.match(f))
            if not frames:
                continue
            last = max(f.stat().st_mtime for f in frames)
            if now - last > FRESH_S:
                continue
            out.append({'dir': d, 'count': len(frames), 'last': frames[-1].name, 'mtime': last,
                        'age_s': round(now - last, 1)})
    out.sort(key=lambda r: -r['mtime'])
    return out


def resolve_image(path):
    """A served file must be a frame inside one of the watched folders."""
    p = pathlib.Path(path).resolve()
    ok = p.is_file() and p.suffix.lower() in TYPES and FRAME.match(p.name) and within_roots(p)
    return p if ok else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == '/':
            self.send(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
        elif u.path == '/api/dirs':
            self.send(200, json.dumps(frame_dirs()).encode(), 'application/json')
        elif u.path == '/api/frames':
            d = pathlib.Path(q.get('dir', [''])[0]).resolve()
            now = time.time()                       # skip a file still being written
            names = sorted(f for f in os.listdir(d) if FRAME.match(f) and now - (d / f).stat().st_mtime > 1.5) \
                if d.is_dir() and within_roots(d) else []
            self.send(200, json.dumps(names).encode(), 'application/json')
        elif u.path == '/img':
            p = resolve_image(q.get('p', [''])[0])
            if p:
                self.send(200, p.read_bytes(), TYPES[p.suffix.lower()])
            else:
                self.send(404, b'not found', 'text/plain')
        else:
            self.send(404, b'not found', 'text/plain')


if __name__ == '__main__':
    print(f'render viewer on http://localhost:{args.port} watching {", ".join(map(str, ROOTS))}')
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
