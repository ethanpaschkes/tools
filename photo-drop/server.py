#!/usr/bin/env python3
"""Photo drop: a phone page that sends photos straight into a folder on this Mac.

Run it with `python3 server.py`. Settings come from the `.env` file beside this
script (see `.env.example`); the secret link token is created there on first run.
"""
import hmac
import ipaddress
import json
import os
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
DESTINATIONS_FILE = HERE / "destinations.txt"

DEFAULT_PORT = 28765
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_LONG_EDGE = 2500
MAX_NOTE_CHARS = 500
DEST_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
TAILNET = ipaddress.ip_network("100.64.0.0/10")

# The only files the server will ever hand out, all behind the token.
STATIC_FILES = {
    "": ("page.html", "text/html; charset=utf-8"),
    "icon.png": ("icon.png", "image/png"),
    "manifest.json": ("manifest.json", "application/manifest+json"),
}

name_lock = threading.Lock()


def read_env_file():
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def load_settings():
    """Environment variables win over .env; a missing token is created and saved."""
    values = read_env_file()
    values.update({k: v for k, v in os.environ.items() if k.startswith("PHOTO_DROP_")})
    if not values.get("PHOTO_DROP_TOKEN"):
        values["PHOTO_DROP_TOKEN"] = secrets.token_urlsafe(32)
        with open(ENV_FILE, "a") as f:
            f.write("PHOTO_DROP_TOKEN=" + values["PHOTO_DROP_TOKEN"] + "\n")
        os.chmod(ENV_FILE, 0o600)
    return {
        "token": values["PHOTO_DROP_TOKEN"],
        "port": int(values.get("PHOTO_DROP_PORT", DEFAULT_PORT)),
        "inbox": Path(values.get("PHOTO_DROP_INBOX", HERE / "inbox")).expanduser().resolve(),
        "tailnet": values.get("PHOTO_DROP_TAILNET") == "1",
    }


def allowed_client(ip, allow_tailnet):
    """Home network only: this Mac, private (home Wi-Fi) addresses, and Tailscale if switched on."""
    address = ipaddress.ip_address(ip)
    if address in TAILNET:
        return allow_tailnet
    return address.is_loopback or address.is_link_local or address.is_private


def destinations(inbox):
    """The list in destinations.txt, then any other folder that exists in the inbox."""
    names = []
    for line in DESTINATIONS_FILE.read_text().splitlines():
        name = line.strip()
        if DEST_RE.match(name) and name not in names:
            names.append(name)
    for path in sorted(inbox.iterdir()):
        if path.is_dir() and not path.is_symlink() and DEST_RE.match(path.name) and path.name not in names:
            names.append(path.name)
    return names


def image_kind(data):
    """File type from the first bytes of the file itself, never from what the sender claims."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
        return "heic"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def pixel_size(path):
    """(width, height) from macOS's `sips`, or None when the file is not a readable image."""
    result = subprocess.run(
        ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    found = dict(re.findall(r"(pixelWidth|pixelHeight): (\d+)", result.stdout))
    if result.returncode != 0 or len(found) != 2:
        return None
    return int(found["pixelWidth"]), int(found["pixelHeight"])


def to_capped_jpeg(source, target):
    result = subprocess.run(
        ["/usr/bin/sips", "-s", "format", "jpeg", "-s", "formatOptions", "85",
         "-Z", str(MAX_LONG_EDGE), str(source), "--out", str(target)],
        capture_output=True, timeout=60,
    )
    return result.returncode == 0 and target.exists()


def save_photo(inbox, dest, data, kind, note):
    """Store one photo as a JPEG in the destination folder; returns its name, or None if unreadable."""
    incoming = inbox / ".incoming"
    incoming.mkdir(exist_ok=True)
    stem = secrets.token_hex(8)
    raw = incoming / (stem + "." + kind)
    ready = incoming / (stem + ".ready.jpg")
    try:
        raw.write_bytes(data)
        size = pixel_size(raw)
        if size is None:
            return None
        if kind == "jpg" and max(size) <= MAX_LONG_EDGE:
            raw.replace(ready)
        elif not to_capped_jpeg(raw, ready):
            return None
        folder = inbox / dest
        folder.mkdir(exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        with name_lock:
            name, n = stamp, 1
            while (folder / (name + ".jpg")).exists():
                n += 1
                name = "%s_%d" % (stamp, n)
            ready.replace(folder / (name + ".jpg"))
        if note:
            (folder / (name + ".txt")).write_text(note + "\n")
        return name + ".jpg"
    finally:
        raw.unlink(missing_ok=True)
        ready.unlink(missing_ok=True)


def log(*parts):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *parts, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "PhotoDrop"
    sys_version = ""
    timeout = 60

    def log_message(self, format, *args):
        """Silence the built-in log: it prints the address, which holds the token."""

    def reply(self, status, body=b"", content_type="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, status, payload):
        self.reply(status, json.dumps(payload).encode(), "application/json")

    def route(self):
        """The part of the address after /k/<token>/, or None when the caller gets nothing."""
        settings = self.server.settings
        if not allowed_client(self.client_address[0], settings["tailnet"]):
            return None, {}
        url = urlsplit(self.path)
        parts = url.path.split("/")
        if len(parts) != 4 or parts[0] != "" or parts[1] != "k":
            return None, {}
        if not hmac.compare_digest(parts[2].encode(), settings["token"].encode()):
            return None, {}
        return parts[3], parse_qs(url.query)

    def do_GET(self):
        name, _ = self.route()
        if name == "dests":
            self.reply_json(200, destinations(self.server.settings["inbox"]))
        elif name in STATIC_FILES:
            filename, content_type = STATIC_FILES[name]
            self.reply(200, (HERE / filename).read_bytes(), content_type)
        else:
            self.reply(404)

    def do_POST(self):
        name, query = self.route()
        if name != "upload":
            self.reply(404)
            return
        self.close_connection = True
        inbox = self.server.settings["inbox"]
        dest = query.get("dest", [""])[0]
        if dest not in destinations(inbox):
            self.reply_json(400, {"error": "unknown destination"})
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit():
            self.reply_json(411, {"error": "length required"})
            return
        if int(length) > MAX_UPLOAD_BYTES:
            log("refused", dest, "too big:", length, "bytes")
            self.reply_json(413, {"error": "photo too big"})
            return
        data = self.rfile.read(int(length))
        kind = image_kind(data)
        note = " ".join(unquote(self.headers.get("X-Photo-Note", "")).split())[:MAX_NOTE_CHARS]
        saved = save_photo(inbox, dest, data, kind, note) if kind else None
        if saved is None:
            log("refused", dest, "not a photo:", len(data), "bytes")
            self.reply_json(415, {"error": "not a photo"})
            return
        log("saved", dest + "/" + saved, len(data), "bytes", "+ note" if note else "")
        self.reply_json(200, {"ok": True, "dest": dest, "name": saved})


def main():
    settings = load_settings()
    settings["inbox"].mkdir(parents=True, exist_ok=True)
    for name in destinations(settings["inbox"]):
        (settings["inbox"] / name).mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", settings["port"]), Handler)
    server.settings = settings
    log("Photo drop listening on port", settings["port"], "- inbox", settings["inbox"])
    server.serve_forever()


if __name__ == "__main__":
    main()
