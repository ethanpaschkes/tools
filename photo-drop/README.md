# photo-drop

Send photos from an iPhone straight into a folder on your Mac, without AirDrop and without the photo landing in the
camera roll. A small server on the Mac serves one phone page with a big button per destination folder. Tap a button,
the iPhone camera opens, take the photo, and it is on the Mac a second later.

Python standard library and macOS built-ins only (`sips` for pictures, `swift` for the QR code). Nothing to install.

## Set it up

```bash
./install.sh
```

That starts the server and makes it start by itself at every login (a launchd "user agent", macOS's way of running a
small program in the background; it is restarted if it ever stops). The first start creates `.env` with a long random
token, the secret part of your link.

```bash
./link.sh
```

That opens a QR code on the Mac's screen. On the iPhone:

1. Point the Camera app at the QR code and tap the link. The page opens in Safari.
2. Tap Share, then **Add to Home Screen**, then Add. You get a "Photo drop" tile that opens like an app.
3. Open the tile and tap a destination. Take the photo and tap **Use Photo**. The page shows "Sent" with a thumbnail.

The small **Library** button beside each destination sends photos you already have instead (several at once is fine).
The note box adds one line of text to the next photo.

## Where photos go

`inbox/<destination>/2026-11-10_160512.jpg` beside `server.py`, newest name last. A note is saved as a `.txt` with the
same name. Everything is stored as JPEG with the long edge at most 2500 pixels: the phone page shrinks the photo before
sending, and the server converts anything else (HEIC, PNG, WebP) itself.

To have the inbox somewhere handier, make a link to it:

```bash
ln -s "$PWD/inbox" ~/Downloads/claude-inbox
```

Keep the real folder outside Downloads, Desktop and Documents: macOS blocks background programs from writing there.
Or set `PHOTO_DROP_INBOX` in `.env` to any other folder.

## Destinations

The buttons are the names in `destinations.txt` (one per line), followed by any other folder that exists in the inbox.
So either add a line to that file, or just make a folder:

```bash
mkdir inbox/receipts
```

Names are lowercase letters, digits, `-` and `_`. The page picks up new ones the next time it is opened.

## Stop, start, remove

```bash
launchctl bootout gui/$(id -u)/local.photo-drop
```

Stops it until the next login.

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.photo-drop.plist
```

Starts it again.

```bash
./install.sh remove
```

Stops it and removes the auto-start for good. The log is `~/Library/Logs/photo-drop.log`.

## Safety

- **Token.** Every address starts with `/k/<token>/`. Without the right token the server answers 404 to everything.
  The token lives in `.env` (kept out of Git) and is never written to the log. To cancel an old link, delete the
  `PHOTO_DROP_TOKEN` line from `.env`, restart, and run `./link.sh` again.
- **Home network only.** The server only answers private network addresses (your Wi-Fi) and listens on IPv4 only.
  Do not put a public tunnel in front of it. For use away from home, `PHOTO_DROP_TAILNET=1` in `.env` also accepts
  devices on your own Tailscale network; `./link.sh tailnet` then makes the link for that.
- **Photos only.** The file type is read from the file itself. Anything that is not a readable JPEG, PNG, HEIC or WebP,
  or is over 40 MB, is refused. Destinations must already exist, so nothing can be written outside the inbox.
- **Nothing else is served.** Only the page, its icon and the destination list. No folder listings, no way to read
  photos back.
- The link is plain `http`, so anyone already on the same Wi-Fi who can watch its traffic could copy the token. Fine
  at home; think twice on shared Wi-Fi.

If macOS asks whether to allow incoming connections for Python, click **Allow**.

## Test

```bash
./test.sh
```

Runs a throwaway copy of the server (own port, own token, temporary inbox) and checks it with curl: good and bad
tokens, each picture format, the size cap, non-pictures, and attempts to escape the inbox.

## Limits

- The Mac must be awake and on the same Wi-Fi as the phone.
- Photos only, no video.
- The address uses the Mac's `.local` name, so it keeps working when the Mac's IP address changes. If you rename the
  Mac, run `./link.sh` and add the tile again.
