import { GiftRulesEngine } from '../src/gifts/gift-rules';
import { QueueManager } from '../src/gifts/queue-manager';

describe('GiftRulesEngine & QueueManager', () => {
  test('validates configured gifts correctly', () => {
    const engine = new GiftRulesEngine();
    const giftA = engine.validateGift('gift_01_id');
    expect(giftA).not.toBeNull();
    expect(giftA?.video).toBe('gift-01.mp4');

    const invalid = engine.validateGift('unknown_gift');
    expect(invalid).toBeNull();
  });

  test('enforces deduplication / cooldown', () => {
    const engine = new GiftRulesEngine();
    const now = Date.now();
    
    // First time should not be duplicate
    expect(engine.isDuplicate('user123', 'gift_01_id', now)).toBe(false);
    // Immediate second time should be duplicate
    expect(engine.isDuplicate('user123', 'gift_01_id', now + 1000)).toBe(true);
    // After cooldown should not be duplicate
    expect(engine.isDuplicate('user123', 'gift_01_id', now + 4000)).toBe(false);
  });

  test('queue manager processes items in FIFO order', async () => {
    const played: string[] = [];
    const queue = new QueueManager(async (item) => {
      played.push(item.config.video);
    });

    queue.enqueue({
      id: '1',
      userId: 'u1',
      giftId: 'gift_01_id',
      config: { name: 'A', video: 'gift-01.mp4', durationMs: 10 },
      timestamp: Date.now(),
    });

    queue.enqueue({
      id: '2',
      userId: 'u2',
      giftId: 'gift_02_id',
      config: { name: 'B', video: 'gift-02.mp4', durationMs: 10 },
      timestamp: Date.now(),
    });

    // Wait for queue processing
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(played).toEqual(['gift-01.mp4', 'gift-02.mp4']);
  });
});
