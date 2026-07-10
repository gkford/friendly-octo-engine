const express = require('express');
const app = express();

app.use(express.json());
app.use(express.static('.'));

const config = {
  lavaSpeed: 0.5,
  lavaGravity: 0.03,
  lavaSpawnRate: 80,
  lavaSize: 10,
  shipSpeed: 4,
  bulletSpeed: 8,
  shootDelay: 200,
  shipSize: 24,
  hoseCount: 2,
  restartDelay: 2,
};

app.get('/config', (req, res) => res.json(config));

app.post('/config', (req, res) => {
  Object.assign(config, req.body);
  res.json(config);
});

const PORT = process.env.PORT || 8080;
app.listen(PORT, '::', () => {
  console.log(`Game:      http://graemes-macbook-air.local:${PORT}`);
  console.log(`Dev panel: http://localhost:${PORT}/dev.html`);
});
