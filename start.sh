#!/usr/bin/env bash
# One command to get the game playable on the iPad:
#   server up → Cloudflare tunnel up → fresh QR code → verified end-to-end.
# Safe to re-run any time; it restarts the tunnel and regenerates the QR.
set -euo pipefail
cd "$(dirname "$0")"

PORT=8080
TOOLS=.tools
CLOUDFLARED=$TOOLS/cloudflared
QR=game-starter-QR.png

# 1. Game server (leave it alone if already up)
if ! curl -sf "http://localhost:$PORT/config" >/dev/null 2>&1; then
  echo "Starting game server..."
  nohup node server.js > server.log 2>&1 &
  for _ in $(seq 1 20); do
    curl -sf "http://localhost:$PORT/config" >/dev/null 2>&1 && break
    sleep 0.5
  done
fi
curl -sf "http://localhost:$PORT/config" >/dev/null || { echo "✗ server failed to start — see server.log"; exit 1; }
echo "✓ server running on :$PORT"

# 2. cloudflared binary (downloaded once, kept in .tools/)
mkdir -p "$TOOLS"
if [ ! -x "$CLOUDFLARED" ]; then
  echo "Downloading cloudflared (one-time)..."
  curl -sL -o "$CLOUDFLARED" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x "$CLOUDFLARED"
fi

# 3. Quick tunnel — fresh one each run (the URL is random per start)
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 0.5
nohup "$CLOUDFLARED" tunnel --url "http://localhost:$PORT" > "$TOOLS/cloudflared.log" 2>&1 &
echo "Starting tunnel..."
URL=""
for _ in $(seq 1 40); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TOOLS/cloudflared.log" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 0.5
done
[ -n "$URL" ] || { echo "✗ tunnel failed to start — see $TOOLS/cloudflared.log"; exit 1; }

# 4. Verify the game is actually reachable from outside
# (fresh tunnel hostnames can take a few seconds to appear in DNS)
CODE=""
for _ in $(seq 1 30); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "$URL/" || true)
  [ "$CODE" = "200" ] && break
  sleep 1
done
[ "$CODE" = "200" ] || { echo "✗ tunnel up but not serving the game (got HTTP $CODE)"; exit 1; }

# 5. Fresh QR code for the iPad
npx --yes qrcode "$URL/" -o "$QR" >/dev/null 2>&1

echo "✓ tunnel verified: $URL"
echo "✓ QR regenerated: $QR"
echo ""
echo "  Open $QR and scan it with the iPad camera. Dev panel: $URL/dev.html"
