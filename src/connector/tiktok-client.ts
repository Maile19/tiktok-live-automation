import * as dotenv from 'dotenv';
dotenv.config();

export interface NormalizedGiftEvent {
  id: string;
  userId: string;
  giftId: string;
  giftName: string;
  repeatCount: number;
  timestamp: number;
}

export type GiftEventHandler = (event: NormalizedGiftEvent) => void;

export class TikTokClient {
  private client: any = null;
  private handler: GiftEventHandler | null = null;
  private username: string;

  constructor(username?: string) {
    this.username = username || process.env.TIKTOK_USERNAME || '';
  }

  public onGift(handler: GiftEventHandler): void {
    this.handler = handler;
  }

  public getConnected(): boolean {
    return this.client !== null;
  }

  public async connect(): Promise<void> {
    if (!this.username) {
      console.warn('[TikTokClient] No TIKTOK_USERNAME set. Skipping live connection.');
      return;
    }

    const { TikTokLiveConnection } = await import('tiktok-live-connector');
    this.client = new TikTokLiveConnection(this.username, {});

    this.client.on('gift', (data: any) => {
      if (!this.handler) return;

      if (data.repeatType === 1 && data.repeatEnd === 0) {
        return;
      }

      const event: NormalizedGiftEvent = {
        id: `${data.id || Date.now()}-${Date.now()}`,
        userId: String(data.userId || data.user?.id || ''),
        giftId: String(data.giftId),
        giftName: data.giftName || data.gift?.name || '',
        repeatCount: data.repeatCount || 1,
        timestamp: Date.now(),
      };

      this.handler(event);
    });

    this.client.on('disconnect', () => {
      console.warn('[TikTokClient] Disconnected from LIVE.');
    });

    this.client.on('error', (err: Error) => {
      console.error('[TikTokClient] Error:', err.message);
    });

    await this.client.connect();
    console.log(`[TikTokClient] Connected to @${this.username}`);
  }
}
