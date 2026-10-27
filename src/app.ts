import * as dotenv from 'dotenv';
dotenv.config();

import * as http from 'http';
import { GiftRulesEngine } from './gifts/gift-rules';
import { QueueManager, QueueItem } from './gifts/queue-manager';
import { OBSClient } from './obs/obs-client';
import { TikTokClient, NormalizedGiftEvent } from './connector/tiktok-client';

const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : 3000;

async function bootstrap() {
  console.log('[Bootstrap] Starting TikTok LIVE Automation Engine...');

  const rulesEngine = new GiftRulesEngine();
  const obsClient = new OBSClient();
  const tiktokClient = new TikTokClient();

  obsClient.connect().then(async () => {
    // Set standby-loop on startup if connected
    await obsClient.setStandbyLoop();
  }).catch(() => {
    console.warn('[Bootstrap] OBS WebSocket not reachable. Simulation mode active.');
  });

  const queueManager = new QueueManager(async (item: QueueItem) => {
    console.log(`[QueueManager] Playing ${item.config.video} for ${item.config.name} (user: ${item.userId})`);
    await obsClient.playVideo(item.config.video, item.config.durationMs);
  });

  tiktokClient.onGift((event: NormalizedGiftEvent) => {
    const config = rulesEngine.validateGift(event.giftId, event.giftName);
    if (!config) {
      console.debug(`[GiftHandler] Unmapped gift ID/Name: ${event.giftId} / ${event.giftName}`);
      return;
    }

    if (rulesEngine.isDuplicate(event.userId, event.giftId, event.timestamp)) {
      console.debug(`[GiftHandler] Cooldown hit for user ${event.userId}, gift ${event.giftId}`);
      return;
    }

    queueManager.enqueue({
      id: event.id,
      userId: event.userId,
      giftId: event.giftId,
      config,
      timestamp: event.timestamp,
    });
  });

  // HTTP Server for Health, Simulation & Stream Control
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url || '/', `http://${req.headers.host}`);
    
    if (req.method === 'GET' && url.pathname === '/health') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'ok',
        obsConnected: obsClient.getConnected(),
        tiktokConnected: tiktokClient.getConnected(),
        queueLength: queueManager.size(),
        uptime: process.uptime(),
        timestamp: new Date().toISOString()
      }));
      return;
    }

    if (req.method === 'POST' && url.pathname === '/simulate-gift') {
      let body = '';
      req.on('data', chunk => { body += chunk; });
      req.on('end', () => {
        try {
          const data = JSON.parse(body || '{}');
          const userId = data.userId || 'sim_user_' + Date.now();
          const giftId = data.giftId || '5655';
          const giftName = data.giftName;

          const config = rulesEngine.validateGift(giftId, giftName);
          if (!config) {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'Invalid giftId or giftName' }));
            return;
          }

          const eventId = 'sim_' + Date.now();
          const timestamp = Date.now();

          queueManager.enqueue({
            id: eventId,
            userId,
            giftId,
            config,
            timestamp
          });

          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ success: true, eventId, userId, giftId, config }));
        } catch (err: any) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: err.message }));
        }
      });
      return;
    }

    if (req.method === 'POST' && url.pathname === '/start-stream') {
      let body = '';
      req.on('data', chunk => { body += chunk; });
      req.on('end', async () => {
        try {
          const data = JSON.parse(body || '{}');
          const { rtmpUrl, streamKey } = data;
          if (!rtmpUrl || !streamKey) {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'rtmpUrl and streamKey are required' }));
            return;
          }

          await obsClient.startStream(rtmpUrl, streamKey);
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ success: true, message: 'Stream started successfully' }));
        } catch (err: any) {
          res.writeHead(500, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: err.message }));
        }
      });
      return;
    }

    if (req.method === 'POST' && url.pathname === '/stop-stream') {
      try {
        await obsClient.stopStream();
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ success: true, message: 'Stream stopped successfully' }));
      } catch (err: any) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: err.message }));
      }
      return;
    }

    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not found' }));
  });

  server.listen(PORT, '127.0.0.1', () => {
    console.log(`[HTTP Server] Health, simulation & stream control API listening on http://127.0.0.1:${PORT}`);
  });

  await tiktokClient.connect();
  console.log('[Bootstrap] Engine initialized. Listening for gifts.');
}

bootstrap().catch((err) => {
  console.error('[Bootstrap] Fatal error:', err);
  process.exit(1);
});
