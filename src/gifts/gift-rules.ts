import * as fs from 'fs';
import * as path from 'path';

export interface GiftConfig {
  name: string;
  video: string;
  durationMs: number;
}

export interface GiftMapping {
  [giftId: string]: GiftConfig;
}

export class GiftRulesEngine {
  private gifts: GiftMapping = {};
  private recentEvents: Map<string, number> = new Map();
  private cooldownMs: number = 3000; // 3 seconds per user+gift cooldown

  constructor(configPath?: string) {
    const resolvedPath = configPath || path.join(__dirname, '../../config/gifts.json');
    if (fs.existsSync(resolvedPath)) {
      const raw = fs.readFileSync(resolvedPath, 'utf-8');
      const parsed = JSON.parse(raw);
      this.gifts = parsed.gifts || {};
    }
  }

  public validateGift(giftId: string): GiftConfig | null {
    return this.gifts[giftId] || null;
  }

  public isDuplicate(userId: string, giftId: string, timestamp: number): boolean {
    const key = `${userId}:${giftId}`;
    const lastTime = this.recentEvents.get(key) || 0;
    if (timestamp - lastTime < this.cooldownMs) {
      return true;
    }
    this.recentEvents.set(key, timestamp);
    return false;
  }
}
