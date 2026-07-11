# Lava Runner 🌋

A browser-based vertical shooter built as a father-son project. Lava blobs rain down from oil pump hoses; you dodge the big harmful red blobs, collect small blue numbered blobs to hit an exact target sum (a sneaky math game), and charge a super shot that zooms around the screen and destroys a hose. Destroy all the hoses to win.

## The couch co-op dev setup

- **Son plays on the iPad** — Safari, fullscreen, with an 8BitDo Pro 3 controller paired over Bluetooth.
- **Dad sits on the laptop** — running a coding agent to make live changes, plus the **dev panel** (`/dev.html`) to tweak game parameters in real time.

The game polls the server for config every second, so slider changes on the laptop show up on the iPad within ~1 second without a reload.

## Architecture

Three files, deliberately simple:

| File | Purpose |
|---|---|
| `index.html` | The entire game — canvas rendering, game loop, gamepad input, procedural music (Web Audio), all in one inline `<script>` |
| `dev.html` | Dev dashboard — sliders/selects that GET and POST `/config` |
| `sound.html` | Sound lab — design sound effects and preview procedural music tracks |
| `server.js` | Express server — serves static files, holds in-memory `config`, exposes `GET/POST /config` |

Run it with:

```bash
node server.js
# Game:      http://localhost:8080
# Dev panel: http://localhost:8080/dev.html
# Sound lab: http://localhost:8080/sound.html
```

No build step, no persistence — config lives in server memory and resets on restart.

## Running from GitHub Codespaces

1. `node server.js` (port 8080)
2. Make the port public: `gh codespace ports visibility 8080:public -c "$CODESPACE_NAME"`
3. Get the URL: `gh codespace ports -c "$CODESPACE_NAME"`
4. Open that URL in Safari on the iPad

→ **Troubleshooting for Codespaces issues** (port visibility, tunnel 404s, etc.): see [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)