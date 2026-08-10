# Lava Runner — agent instructions

This game is **played on an iPad** and served from here through a Cloudflare
tunnel. A localhost server alone is useless to the player.

- **To run / serve / bring up the game:** `./infra/start.sh` — nothing else.
  It is idempotent: if everything is already up it reports so and stops; if
  something is down it fixes only that piece, keeping the same URL.
- **Never restart the servers.** A restart changes the iPad URL and is gated
  (`--restart` refuses without `LAVA_RESTART_OK=1`). Only run
  `LAVA_RESTART_OK=1 ./infra/start.sh --restart` if the user's own words
  explicitly asked for a restart — never to "fix" something; plain
  `./infra/start.sh` is always the fix.
- **Never** run `node game/server.js` directly — the iPad can't reach localhost.
- Current URLs are in `LINKS.md` after each start. The tunnel URL changes on
  every fresh start, so never quote an old URL from memory.
- Game code lives in `game/` (single-file game in `game/index.html`);
  `infra/` is tunnel/tooling only — don't touch it for gameplay work.
- **To see what happened during gameplay on the iPad:** run `./infra/logs.sh`.
  It prints the logs of the most recent game (errors, controller dropouts,
  game start/over). Run `./infra/logs.sh --live` to watch logs in real time
  while a game is being played. Always use this script — do not read
  `infra/client.log` directly. Log-line meanings are explained in the
  "Getting logs from the iPad" section of `README.md`.
