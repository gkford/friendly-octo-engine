# Troubleshooting

## GitHub Codespaces

### Port visibility resets after restart

Symptom: iPad suddenly asks to *sign in to GitHub* instead of showing the game.

```bash
gh codespace ports visibility 8080:public -c "$CODESPACE_NAME"
```

### Tunnel 404 after codespace container restart

After a disconnect/container restart, the forwarded URL returns a hard HTTP 404.

**How to diagnose:**
1. Server healthy locally?
   ```bash
   ps aux | grep "node server.js" | grep -v grep
   curl -sI http://localhost:8080/        # expect 200
   ```
2. Port forwarded and public?
   ```bash
   gh codespace ports -c "$CODESPACE_NAME"
   ```
3. Tunnel returning 404?
   ```bash
   curl -sI "https://${CODESPACE_NAME}-8080.app.github.dev/"
   ```
   If 404 comes from `x-served-by: tunnels-prod-...`, the tunnel registration went stale on GitHub's side.

**The fix — stop and restart the codespace:**
1. Go to https://github.com/codespaces → find codespace → **⋯ → Stop codespace**
2. Wait, then reopen it
3. Restart the game server and re-run the port visibility command
4. **Wait up to ~10 minutes** — the Codespaces Virtual Machine Agent (runs outside the container) needs time to re-register the tunnel. Poll:
   ```bash
   watch -n 15 'curl -sI "https://${CODESPACE_NAME}-8080.app.github.dev/" | head -1'
   ```

**Prevention:**
- Pin port config in `.devcontainer/devcontainer.json`:
  ```json
  {
    "forwardPorts": [8080],
    "portsAttributes": {
      "8080": { "label": "Lava Runner", "visibility": "public" }
    }
  }
  ```
  (May still revert on restart — keep the `gh` command handy.)
- Bump the idle timeout (Settings → Codespaces → Default idle timeout) for couch sessions.
- Run a preflight check before each play session: server up → port public → `curl` the public URL.