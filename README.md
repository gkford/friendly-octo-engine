# Lava Runner 🌋

A browser-based vertical shooter built as a father-son project. Lava blobs rain down from oil pump hoses; you dodge the big harmful red blobs, collect small blue numbered blobs to hit an exact target sum (a sneaky math game), and charge a super shot that zooms around the screen and destroys a hose. Destroy all the hoses to win.

## The big picture — read this first

The game is **played on an iPad** but **served from this machine** (a codespace).
The iPad can't see `localhost`, so "running the game" always means two things:
the game server *and* a Cloudflare tunnel that gives the iPad a public URL.
One script owns all of that:

```bash
./infra/start.sh                                # ensure everything is up: server + tunnel + QR + LINKS.md
LAVA_RESTART_OK=1 ./infra/start.sh --restart    # humans only: kill both and start fresh (changes the URL!)
```

That is the **only** command needed to run this project. It is safe to run at
any time: if everything is already up and healthy it says so and stops; if
something is down it starts just that piece; it verifies the game is reachable
from the outside before declaring success.

After it runs:

- **`LINKS.md`** (repo root) lists every current URL — game, dev panel, sound
  lab, plus localhost equivalents. The tunnel URL is **random on every fresh
  start**, so never reuse an old URL; always check `LINKS.md`.
- **`game-starter-QR.png`** is the current game URL as a QR code — scan it with
  the iPad camera.

Do **not** run `node game/server.js` by itself — that gives you a
localhost-only server the iPad can't reach.

## The couch co-op dev setup

- **Son plays on the iPad** — Safari, fullscreen, with an 8BitDo Pro 3 controller paired over Bluetooth.
- **Dad sits on the laptop** — running a coding agent to make live changes, plus the **dev panel** (`/dev.html`) to tweak game parameters in real time.

The game polls the server for config every second, so slider changes on the laptop show up on the iPad within ~1 second without a reload.

One-time iPad setup: allow `trycloudflare.com` in Screen Time.

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

No build step, no persistence — config lives in server memory and resets on restart (the dev panel's current values are the source of truth while playing).

→ Problems, or why we don't use GitHub's own port forwarding: see [infra/TROUBLESHOOTING.md](./infra/TROUBLESHOOTING.md)
