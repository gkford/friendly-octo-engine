const express = require('express');
const fs = require('fs');
const path = require('path');
const app = express();

app.use(express.json());
app.use(express.static(__dirname)); // serve the game folder only, regardless of cwd

// Gameplay logs shipped from the iPad (sendBeacon posts as text/plain,
// the fetch fallback as JSON — accept both). Read with ./infra/logs.sh.
const CLIENT_LOG = path.join(__dirname, '..', 'infra', 'client.log');

app.post('/log', express.text({ type: '*/*' }), (req, res) => {
  let data = req.body;
  if (typeof data === 'string') {
    try { data = JSON.parse(data); } catch (e) { data = null; }
  }
  if (!data || !Array.isArray(data.lines)) return res.status(400).json({ ok: false });
  const now = new Date().toISOString();
  const session = String(data.session || 'unknown').slice(0, 12);
  const out = data.lines.slice(0, 200)
    .map(l => `${now} [${session}] ${String(l).slice(0, 500)}\n`).join('');
  try {
    if (fs.existsSync(CLIENT_LOG) && fs.statSync(CLIENT_LOG).size > 5e6) {
      fs.renameSync(CLIENT_LOG, CLIENT_LOG + '.1'); // cap growth, keep one previous chunk
    }
    fs.appendFileSync(CLIENT_LOG, out);
  } catch (e) {}
  res.json({ ok: true });
});

const config = {
  lavaSpeed: 1.5,
  lavaGravity: 0.03,
  lavaSpawnRate: 80,
  lavaSizeMin: 10,
  lavaSizeMax: 50,
  lavaHarmPercent: 50,
  shipSpeed: 6,
  bulletSpeed: 14,
  shootDelay: 200,
  shipSize: 24,
  hoseCount: 2,
  restartDelay: 5,
  uiLayout: 'bottom',
  hoseStyle: 'simple',
  allowedTargets: '10',
  pityEvery: 4,
  bulletColors: 'black,red,orange,yellow,green,cyan,blue,purple,pink', // enabled rotation colors
};

app.get('/config', (req, res) => res.json(config));

app.post('/config', (req, res) => {
  Object.assign(config, req.body);
  res.json(config);
});

let resetPending = false;

app.post('/reset', (req, res) => {
  resetPending = true;
  res.json({ ok: true });
});

app.get('/reset-check', (req, res) => {
  const val = resetPending;
  resetPending = false;
  res.json({ reset: val });
});

const PORT = process.env.PORT || 8080;
app.listen(PORT, '::', () => {
  console.log(`Game:      http://graemes-macbook-air.local:${PORT}`);
  console.log(`Dev panel: http://localhost:${PORT}/dev.html`);
});
