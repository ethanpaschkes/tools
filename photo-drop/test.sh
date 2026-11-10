#!/bin/bash
# Tests Photo drop with curl against a throwaway copy of the server:
# its own port, its own token and a temporary inbox, so the real inbox is never touched.
#   ./test.sh
set -uo pipefail
cd "$(dirname "$0")"

port=28766
token="test-token-not-the-real-one"
work=$(mktemp -d)
inbox="$work/inbox"
base="http://127.0.0.1:$port/k/$token"
passed=0
failed=0

PHOTO_DROP_PORT=$port PHOTO_DROP_TOKEN=$token PHOTO_DROP_INBOX=$inbox /usr/bin/python3 server.py > "$work/server.log" 2>&1 &
server=$!
trap 'kill $server 2>/dev/null; wait $server 2>/dev/null; rm -rf "$work"' EXIT
export PYTHONDONTWRITEBYTECODE=1

check() {  # check "what is tested" expected actual
  if [ "$2" = "$3" ]; then
    passed=$((passed + 1)); echo "  ok    $1"
  else
    failed=$((failed + 1)); echo "  FAIL  $1 (wanted $2, got $3)"
  fi
}
status() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$@"; }
send() {  # send file destination [extra curl options] -> http status
  local file=$1 dest=$2; shift 2
  status -X POST -H 'Content-Type: image/jpeg' --data-binary "@$file" "$@" "$base/upload?dest=$dest"
}
count_photos() { find "$inbox" -name '*.jpg' -not -path '*/.incoming/*' | wc -l | tr -d ' '; }
newest() { ls -t "$inbox/$1"/*.jpg | head -1; }
long_edge() { sips -g pixelWidth -g pixelHeight "$1" | awk '/pixel/ { if ($2 > m) m = $2 } END { print m }'; }

for _ in $(seq 50); do
  [ "$(status "$base/")" = 200 ] && break
  sleep 0.1
done

# Test pictures: a 3000x2000 PNG written by hand, then the other formats made from it with sips.
/usr/bin/python3 - "$work/big.png" <<'PY'
import struct, sys, zlib
w, h = 3000, 2000
def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
rows = (b"\x00" + bytes([40, 120, 220]) * w) * h
open(sys.argv[1], "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                              + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
PY
sips -s format jpeg "$work/big.png" --out "$work/big.jpg" > /dev/null
sips -s format heic "$work/big.png" --out "$work/big.heic" > /dev/null
sips -s format jpeg -Z 800 "$work/big.png" --out "$work/small.jpg" > /dev/null
echo "this is not a picture" > "$work/fake.jpg"
{ printf '\xff\xd8\xff\xe0'; echo "starts like a JPEG, then rubbish"; } > "$work/broken.jpg"
head -c $((41 * 1024 * 1024)) /dev/zero > "$work/huge.jpg"

echo "The page and the token"
check "page opens with the right token" 200 "$(status "$base/")"
check "icon is served" 200 "$(status "$base/icon.png")"
check "destination list is served" 200 "$(status "$base/dests")"
check "wrong token gets nothing" 404 "$(status "http://127.0.0.1:$port/k/wrong-token/")"
check "no token gets nothing" 404 "$(status "http://127.0.0.1:$port/")"
check "upload with a wrong token is refused" 404 "$(status -X POST --data-binary "@$work/small.jpg" "http://127.0.0.1:$port/k/wrong-token/upload?dest=general")"
check "wrong token saved nothing" 0 "$(count_photos)"

echo "Only the page and its icon are served"
check "server.py is not served" 404 "$(status "$base/server.py")"
check ".env is not served" 404 "$(status "$base/.env")"
check "no folder listing" 404 "$(status "$base/inbox/")"
check "../ in the address" 404 "$(status --path-as-is "$base/../server.py")"
check "encoded ../ in the address" 404 "$(status "$base/%2e%2e/.env")"
check "PUT is not accepted" 501 "$(status -X PUT --data-binary "@$work/small.jpg" "$base/upload?dest=general")"

echo "Photos arrive"
check "small JPEG accepted" 200 "$(send "$work/small.jpg" general)"
check "small JPEG kept exactly as sent" same "$(cmp -s "$work/small.jpg" "$(newest general)" && echo same || echo changed)"
check "big JPEG accepted" 200 "$(send "$work/big.jpg" clip)"
check "big JPEG capped at 2500 pixels" 2500 "$(long_edge "$(newest clip)")"
check "PNG accepted" 200 "$(send "$work/big.png" sound)"
check "PNG stored as JPEG" jpeg "$(sips -g format "$(newest sound)" | awk '/format/ { print $2 }')"
check "HEIC accepted" 200 "$(send "$work/big.heic" video)"
check "HEIC stored as JPEG" jpeg "$(sips -g format "$(newest video)" | awk '/format/ { print $2 }')"
check "HEIC capped at 2500 pixels" 2500 "$(long_edge "$(newest video)")"
check "photo with a note accepted" 200 "$(send "$work/small.jpg" general -H 'X-Photo-Note: left%20side%20of%20the%20clip')"
check "note saved beside the photo" "left side of the clip" "$(cat "$(newest general | sed 's/\.jpg$/.txt/')")"
check "two photos in the same second both kept" 3 "$(send "$work/small.jpg" general > /dev/null; ls "$inbox/general"/*.jpg | wc -l | tr -d ' ')"

echo "Things that must be refused"
before=$(count_photos)
check "text file dressed as a JPEG" 415 "$(send "$work/fake.jpg" general)"
check "broken JPEG" 415 "$(send "$work/broken.jpg" general)"
check "41 MB upload" 413 "$(send "$work/huge.jpg" general)"
check "upload without a length" 411 "$(send "$work/small.jpg" general -H 'Transfer-Encoding: chunked')"
check "unknown destination" 400 "$(send "$work/small.jpg" nosuch)"
check "destination ../evil" 400 "$(send "$work/small.jpg" '../evil')"
check "destination encoded ../evil" 400 "$(send "$work/small.jpg" '%2e%2e%2fevil')"
check "destination general/../../evil" 400 "$(send "$work/small.jpg" 'general/../../evil')"
check "destination /tmp" 400 "$(send "$work/small.jpg" '/tmp')"
check "hidden working folder is not a destination" 400 "$(send "$work/small.jpg" '.incoming')"
check "refused uploads saved nothing" "$before" "$(count_photos)"
check "nothing written outside the inbox" 0 "$(find "$work" -name evil | wc -l | tr -d ' ')"
check "no leftovers in the working folder" 0 "$(ls "$inbox/.incoming" | wc -l | tr -d ' ')"

echo "Destinations"
mkdir "$inbox/newplace"
ln -s /tmp "$inbox/shortcut"
dests=$(curl -s "$base/dests")
check "a new folder in the inbox becomes a button" yes "$(echo "$dests" | grep -q '"newplace"' && echo yes || echo no)"
check "a link out of the inbox does not" no "$(echo "$dests" | grep -q '"shortcut"' && echo yes || echo no)"
check "upload to the new folder works" 200 "$(send "$work/small.jpg" newplace)"

echo "Who may connect"
check "home Wi-Fi address allowed" True "$(/usr/bin/python3 -c "import server; print(server.allowed_client('192.168.1.50', False))")"
check "internet address refused" False "$(/usr/bin/python3 -c "import server; print(server.allowed_client('8.8.8.8', False))")"
check "Tailscale address refused by default" False "$(/usr/bin/python3 -c "import server; print(server.allowed_client('100.100.20.30', False))")"
check "Tailscale address allowed when switched on" True "$(/usr/bin/python3 -c "import server; print(server.allowed_client('100.100.20.30', True))")"

echo "The log"
check "token never written to the log" 0 "$(grep -c "$token" "$work/server.log")"

echo
echo "$passed passed, $failed failed"
[ "$failed" = 0 ]
