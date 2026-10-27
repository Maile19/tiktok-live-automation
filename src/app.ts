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

  obsClient.connect().catch(() => {
    console.warn('[Bootstrap] OBS WebSocket not reachable. Simulation mode active.');
  });

  const queueManager = new QueueManager(async (item: QueueItem) => {
    console.log(`[QueueManager] Playing ${item.config.video} for ${item.config.name} (user: ${item.userId})`);
    await obsClient.playVideo(item.config.video, item.config.durationMs);
  });

  tiktokClient.onGift((event: NormalizedGiftEvent) => {
    const config = rulesEngine.validateGift(event.giftId);
    if (!config) {
      console.debug(`[GiftHandler] Unmapped gift ID: ${event.giftId} (${event.giftName})`);
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

  // HTTP Server for Health & Simulation (bound strictly to 127.0.0.1)
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
          const giftId = data.giftId || 'gift_01_id';

          const config = rulesEngine.validateGift(giftId);
          if (!config) {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'Invalid giftId' }));
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

    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not found' }));
  });

  server.listen(PORT, '127.0.0.1', () => {
    console.log(`[HTTP Server] Health and simulation API listening on http://127.0.0.1:${PORT}`);
  });

  await tiktokClient.connect();
  console.log('[Bootstrap] Engine initialized. Listening for gifts.');
}

bootstrap().catch((err) => {
  console.error('[Bootstrap] Fatal error:', err);
  process.exit(1);
});
