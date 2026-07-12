# Troubleshooting

## Start here: `./infra/start.sh`

One command does everything: starts the server (if needed), opens a fresh
Cloudflare quick tunnel, verifies the game is reachable from outside, and
regenerates `game-starter-QR.png` and `LINKS.md`. Re-run it any time
something seems off.

- **Current URLs always live in `LINKS.md`** (gitignored, rewritten only
  after the tunnel verifies — if it exists, its links were good as of the
  timestamp inside).
- The tunnel URL is **random on every start** — that's how quick tunnels work.
  Always scan the freshly generated QR or use `LINKS.md`; never bookmark the
  URL on the iPad.
- Logs: `infra/server.log` (game server), `infra/.tools/cloudflared.log` (tunnel).
- One-time iPad setup: add `trycloudflare.com` to the Screen Time allowlist.

### Cloudflare tunnel not working?

1. Re-run `./infra/start.sh` — it kills the old tunnel and starts a clean one.
2. Check `infra/.tools/cloudflared.log` for errors.
3. If the iPad gets a block page, confirm `trycloudflare.com` is allowlisted
   in Screen Time (subdomains are covered automatically).

## Fallback: GitHub Codespaces port forwarding

This was the original approach. It works, but is flaky in ways we verified
empirically (July 2026) — which is why `start.sh` uses Cloudflare instead:

- **Port visibility resets to private on every codespace restart, by design.**
  `"visibility": "public"` in devcontainer.json `portsAttributes` is NOT a
  supported feature — it's an open feature request since 2022
  ([#10394](https://github.com/orgs/community/discussions/10394),
  [#4068](https://github.com/orgs/community/discussions/4068)). The only way
  to make a port public is manually, after every restart:
  ```bash
  gh codespace ports visibility 8080:public -c "$CODESPACE_NAME"
  ```
- **After a restart, even a public port can 404 for up to ~10 minutes**
  (we measured 8) while GitHub's tunnel agent re-registers. The 404 comes from
  the tunnel edge (`x-served-by: tunnels-prod-...`). There is no supported way
  to speed this up; GitHub support has attributed it to
  [Microsoft/Azure infrastructure](https://github.com/orgs/community/discussions/156546).
  Verify from inside the codespace and just wait:
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" "https://${CODESPACE_NAME}-8080.app.github.dev/"
  ```
- **iPad shows a GitHub sign-in screen** → the port is private (see above), or
  Safari is loading a dead codespace's URL from history. The sign-in page is a
  hard dead end on the kid's iPad (`github.com` isn't allowlisted) — the fix is
  always server-side or a fresh QR, never signing in.

## General

- The game server must be restarted after every codespace restart
  (`./infra/start.sh` handles this): background processes don't survive a stop.
- Config lives in server memory — a server restart resets all dev-panel tweaks.
