const express = require('express');
const app = express();

app.use(express.json());
app.use(express.static(__dirname)); // serve the game folder only, regardless of cwd

const config = {
  lavaSpeed: 0.5,
  lavaGravity: 0.03,
  lavaSpawnRate: 80,
  lavaSizeMin: 10,
  lavaSizeMax: 50,
  lavaHarmPercent: 50,
  shipSpeed: 4,
  bulletSpeed: 8,
  shootDelay: 200,
  shipSize: 24,
  hoseCount: 2,
  restartDelay: 5,
  uiLayout: 'bottom',
  hoseStyle: 'simple',
  allowedTargets: '10',
  pityEvery: 4,
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
