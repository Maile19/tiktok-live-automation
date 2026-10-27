import { GiftRulesEngine } from '../src/gifts/gift-rules';
import { QueueManager } from '../src/gifts/queue-manager';

describe('GiftRulesEngine & QueueManager', () => {
  test('validates official gifts by ID and Name (case-insensitive)', () => {
    const engine = new GiftRulesEngine();
    
    // Test by official ID
    const roseById = engine.validateGift('5655');
    expect(roseById).not.toBeNull();
    expect(roseById?.video).toBe('gift-01.mp4');

    const doughnutById = engine.validateGift('5879');
    expect(doughnutById).not.toBeNull();
    expect(doughnutById?.video).toBe('gift-02.mp4');

    const perfumeById = engine.validateGift('5658');
    expect(perfumeById).not.toBeNull();
    expect(perfumeById?.video).toBe('gift-03.mp4');

    const ilyById = engine.validateGift('5656');
    expect(ilyById).not.toBeNull();
    expect(ilyById?.video).toBe('gift-04.mp4');

    // Test by name (case-insensitive)
    const roseByName = engine.validateGift('unknown', '  RoSe  ');
    expect(roseByName).not.toBeNull();
    expect(roseByName?.video).toBe('gift-01.mp4');

    const ilyByName = engine.validateGift('unknown', 'i love you');
    expect(ilyByName).not.toBeNull();
    expect(ilyByName?.video).toBe('gift-04.mp4');

    const invalid = engine.validateGift('invalid', 'NonExistentGift');
    expect(invalid).toBeNull();
  });

  test('enforces deduplication / cooldown', () => {
    const engine = new GiftRulesEngine();
    const now = Date.now();
    
    expect(engine.isDuplicate('user123', '5655', now)).toBe(false);
    expect(engine.isDuplicate('user123', '5655', now + 1000)).toBe(true);
    expect(engine.isDuplicate('user123', '5655', now + 4000)).toBe(false);
  });

  test('queue manager processes items in FIFO order', async () => {
    const played: string[] = [];
    const queue = new QueueManager(async (item) => {
      played.push(item.config.video);
    });

    queue.enqueue({
      id: '1',
      userId: 'u1',
      giftId: '5655',
      config: { name: 'Rose', video: 'gift-01.mp4', durationMs: 10 },
      timestamp: Date.now(),
    });

    queue.enqueue({
      id: '2',
      userId: 'u2',
      giftId: '5879',
      config: { name: 'Doughnut', video: 'gift-02.mp4', durationMs: 10 },
      timestamp: Date.now(),
    });

    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(played).toEqual(['gift-01.mp4', 'gift-02.mp4']);
  });
});
