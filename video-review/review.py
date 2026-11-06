#!/usr/bin/env python3
"""Video review: watch clips on a phone, rate them, leave comments tied to a moment. No packages needed.

    python3 review.py serve --root ~/video-review --port 8796 --token SECRET
    python3 review.py add   --root ~/video-review my-batch clip1.mp4 clip2.mp4 --title "My batch"
    python3 review.py read  --root ~/video-review my-batch [--json]

A batch is a folder in <root>/batches/. Every video in it shows up by itself. batch.json (optional) adds a title
and per-clip notes; feedback.json holds the ratings and comments.
"""
import argparse
import hmac
import json
import mimetypes
import os
import pathlib
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
VIDEO = {'.mp4', '.m4v', '.mov', '.webm'}
VERDICTS = {'', 'final', 'fix', 'skip'}
MAX_BODY = 64 * 1024
LOCK = threading.Lock()


def load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def batch_dir(root, name):
    if not re.fullmatch(r'[\w.-]+', name) or name.startswith('.'):
        return None
    d = root / 'batches' / name
    return d if d.is_dir() else None


def clip_names(d):
    return sorted(p.name for p in d.iterdir() if p.suffix.lower() in VIDEO and not p.name.startswith('.'))


def batch_view(root, name):
    d = batch_dir(root, name)
    if d is None:
        return None
    meta = load(d / 'batch.json', {})
    fb = load(d / 'feedback.json', {})
    notes = meta.get('clips', {})
    clips = []
    for c in clip_names(d):
        row = {'file': c, 'title': notes.get(c, {}).get('title', pathlib.Path(c).stem),
               'note': notes.get(c, {}).get('note', ''), 'rating': 0, 'verdict': '', 'comments': []}
        row.update(fb.get(c, {}))
        clips.append(row)
    return {'name': name, 'title': meta.get('title', name), 'note': meta.get('note', ''), 'clips': clips}


def batches(root):
    base = root / 'batches'
    base.mkdir(parents=True, exist_ok=True)
    rows = []
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        v = batch_view(root, d.name) if d.is_dir() else None
        if v:
            rows.append({'name': v['name'], 'title': v['title'], 'count': len(v['clips']),
                         'rated': sum(1 for c in v['clips'] if c['rating'] or c['verdict'])})
    return rows


def apply_op(root, name, clip, op):
    d = batch_dir(root, name)
    if d is None or clip not in clip_names(d):
        return None
    with LOCK:
        fb = load(d / 'feedback.json', {})
        row = fb.setdefault(clip, {'rating': 0, 'verdict': '', 'comments': []})
        kind = op.get('op')
        if kind == 'rate' and op.get('rating') in range(0, 6):
            row['rating'] = op['rating']
        elif kind == 'verdict' and op.get('verdict') in VERDICTS:
            row['verdict'] = op['verdict']
        elif kind == 'comment' and str(op.get('text', '')).strip():
            t = op.get('t')
            cid = str(op.get('id') or uuid.uuid4().hex)[:40]
            if not any(c['id'] == cid for c in row['comments']):   # a retried save must not double up
                row['comments'].append({'id': cid, 't': round(float(t), 2) if t is not None else None,
                                        'text': str(op['text']).strip()[:2000], 'at': time.strftime('%Y-%m-%d %H:%M')})
        elif kind == 'delete':
            row['comments'] = [c for c in row['comments'] if c['id'] != op.get('id')]
        else:
            return None
        row['comments'].sort(key=lambda c: (c['t'] is None, c['t'] or 0))
        save(d / 'feedback.json', fb)
    return row


class Handler(BaseHTTPRequestHandler):
    root = None
    token = ''
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def send(self, code, body=b'', kind='application/json', extra=()):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Robots-Tag', 'noindex')
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def route(self):
        """Everything lives under /<token>/; anything else is a plain 404."""
        parts = [urllib.parse.unquote(p) for p in urllib.parse.urlsplit(self.path).path.split('/') if p]
        if not parts or not hmac.compare_digest(parts[0].encode(), self.token.encode()):
            self.send(404, b'not found', 'text/plain')
            return None
        return parts[1:]

    def do_GET(self):
        parts = self.route()
        if parts is None:
            return
        if not parts:
            if not urllib.parse.urlsplit(self.path).path.endswith('/'):   # relative links need the slash; relative so a proxy prefix survives
                return self.send(302, extra=[('Location', self.token + '/')])
            return self.send(200, (HERE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if parts == ['api', 'batches']:
            return self.send(200, json.dumps(batches(self.root)).encode())
        if len(parts) == 3 and parts[:2] == ['api', 'batch']:
            v = batch_view(self.root, parts[2])
            return self.send(200, json.dumps(v).encode()) if v else self.send(404)
        if len(parts) == 3 and parts[0] == 'v':
            return self.video(parts[1], parts[2])
        self.send(404)

    do_HEAD = do_GET

    def do_POST(self):
        parts = self.route()
        if parts is None:
            return
        size = int(self.headers.get('Content-Length') or 0)
        if size > MAX_BODY or len(parts) != 4 or parts[:2] != ['api', 'batch']:
            self.close_connection = True
            return self.send(400)
        try:
            op = json.loads(self.rfile.read(size))
            row = apply_op(self.root, parts[2], parts[3], op)
        except (ValueError, TypeError):
            row = None
        self.send(200, json.dumps(row).encode()) if row is not None else self.send(400)

    def video(self, batch, name):
        d = batch_dir(self.root, batch)
        if d is None or name not in clip_names(d):
            return self.send(404)
        path = d / name
        size = path.stat().st_size
        start, end, code = 0, size - 1, 200
        m = re.fullmatch(r'bytes=(\d*)-(\d*)', self.headers.get('Range', ''))   # iPhones refuse to play without Range
        if m and (m[1] or m[2]):
            if m[1]:
                start, end = int(m[1]), min(int(m[2]) if m[2] else end, end)
            else:
                start = max(size - int(m[2]), 0)
            if start > end:
                return self.send(416, extra=[('Content-Range', f'bytes */{size}')])
            code = 206
        try:
            f = path.open('rb')   # before the headers: macOS blocks background services from ~/Downloads, even through a link
        except OSError:
            return self.send(500, b'could not read this file: copy it into the batch folder', 'text/plain')
        self.send_response(code)
        self.send_header('Content-Type', mimetypes.guess_type(name)[0] or 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        with f:
            if self.command == 'HEAD':
                return
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True


def fmt(t):
    return 'whole clip' if t is None else f'{int(t // 60)}:{t % 60:04.1f}'


def cmd_read(a):
    v = batch_view(a.root, a.batch)
    if v is None:
        raise SystemExit(f'no batch called {a.batch}')
    if a.json:
        return print(json.dumps(v, indent=1, ensure_ascii=False))
    print(v['title'])
    for c in sorted(v['clips'], key=lambda c: -c['rating']):
        print(f"\n{'★' * c['rating'] or 'unrated'} {c['verdict'] or '-'}  {c['title']} ({c['file']})")
        for m in c['comments']:
            print(f"   {fmt(m['t'])}  {m['text']}")


def cmd_add(a):
    d = a.root / 'batches' / a.batch
    d.mkdir(parents=True, exist_ok=True)
    for f in a.files:
        shutil.copy2(f, d / pathlib.Path(f).name)
    if a.title:
        meta = load(d / 'batch.json', {})
        meta['title'] = a.title
        save(d / 'batch.json', meta)
    print(f'{len(clip_names(d))} clips in {d}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('serve', 'add', 'read'):
        p = sub.add_parser(name)
        p.add_argument('--root', type=lambda s: pathlib.Path(s).expanduser(), default=pathlib.Path('~/video-review').expanduser())
        if name == 'serve':
            p.add_argument('--port', type=int, default=8796)
            p.add_argument('--token', default=os.environ.get('REVIEW_TOKEN', ''))
        else:
            p.add_argument('batch')
        if name == 'add':
            p.add_argument('files', nargs='+')
            p.add_argument('--title', default='')
        if name == 'read':
            p.add_argument('--json', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'read':
        return cmd_read(a)
    if a.cmd == 'add':
        return cmd_add(a)
    if len(a.token) < 16:
        raise SystemExit('give a --token of 16 or more characters (or set REVIEW_TOKEN)')
    Handler.root, Handler.token = a.root, a.token
    print(f'http://127.0.0.1:{a.port}/{a.token}/')
    ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
