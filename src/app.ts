import * as dotenv from 'dotenv';
dotenv.config();

import { GiftRulesEngine } from './gifts/gift-rules';
import { QueueManager, QueueItem } from './gifts/queue-manager';
import { OBSClient } from './obs/obs-client';
import { TikTokClient, NormalizedGiftEvent } from './connector/tiktok-client';

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

  await tiktokClient.connect();
  console.log('[Bootstrap] Engine initialized. Listening for gifts.');
}

bootstrap().catch((err) => {
  console.error('[Bootstrap] Fatal error:', err);
  process.exit(1);
});
