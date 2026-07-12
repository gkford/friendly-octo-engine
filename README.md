# Lava Runner 🌋

A browser-based vertical shooter built as a father-son project. Lava blobs rain down from oil pump hoses; you dodge the big harmful red blobs, collect small blue numbered blobs to hit an exact target sum (a sneaky math game), and charge a super shot that zooms around the screen and destroys a hose. Destroy all the hoses to win.

## The couch co-op dev setup

- **Son plays on the iPad** — Safari, fullscreen, with an 8BitDo Pro 3 controller paired over Bluetooth.
- **Dad sits on the laptop** — running a coding agent to make live changes, plus the **dev panel** (`/dev.html`) to tweak game parameters in real time.

The game polls the server for config every second, so slider changes on the laptop show up on the iPad within ~1 second without a reload.

## Layout

Two folders, deliberately separate:

- **`game/`** — everything the game *is*. Work on the game? Stay in here.
- **`infra/`** — everything that gets it onto the iPad (tunnel script, agent
  setup, troubleshooting). The game never depends on it.

## Architecture (`game/`)

| File | Purpose |
|---|---|
| `game/index.html` | The entire game — canvas rendering, game loop, gamepad input, all in one inline `<script>` |
| `game/dev.html` | Dev dashboard — sliders/selects that GET and POST `/config` |
| `game/sound.html` | Sound lab — design sound effects and preview procedural music tracks |
| `game/server.js` | Express server — serves the `game/` folder, holds in-memory `config`, exposes `GET/POST /config` |

Run it with:

```bash
node game/server.js
# Game:      http://localhost:8080
# Dev panel: http://localhost:8080/dev.html
# Sound lab: http://localhost:8080/sound.html
```

No build step, no persistence — config lives in server memory and resets on restart.

## Getting it on the iPad (`infra/`)

```bash
./infra/start.sh
```

That starts the server, opens a Cloudflare tunnel, verifies it end-to-end, and regenerates `game-starter-QR.png` **and `LINKS.md`** in the repo root — the latter lists every current URL (game, dev panel, sound lab, local equivalents). Scan the QR with the iPad camera, or grab links from `LINKS.md`. **The URL/QR changes every start — always scan the fresh one.** One-time iPad setup: allow `trycloudflare.com` in Screen Time.

→ Problems, or why we don't use GitHub's own port forwarding: see [infra/TROUBLESHOOTING.md](./infra/TROUBLESHOOTING.md)