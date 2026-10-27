import { GiftConfig } from './gift-rules';

export interface QueueItem {
  id: string;
  userId: string;
  giftId: string;
  config: GiftConfig;
  timestamp: number;
}

export class QueueManager {
  private queue: QueueItem[] = [];
  private isProcessing: boolean = false;
  private maxQueueSize: number = 50;

  constructor(private onPlay: (item: QueueItem) => Promise<void>) {}

  public enqueue(item: QueueItem): boolean {
    if (this.queue.length >= this.maxQueueSize) {
      console.warn(`[QueueManager] Queue overflow! Dropping gift event from user ${item.userId}`);
      return false;
    }
    this.queue.push(item);
    this.processNext();
    return true;
  }

  private async processNext() {
    if (this.isProcessing || this.queue.length === 0) {
      return;
    }

    this.isProcessing = true;
    const item = this.queue.shift()!;

    try {
      await this.onPlay(item);
    } catch (err) {
      console.error(`[QueueManager] Error processing queue item ${item.id}:`, err);
    } finally {
      this.isProcessing = false;
      // Process next in queue
      setImmediate(() => this.processNext());
    }
  }

  public getQueueLength(): number {
    return this.queue.length;
  }

  public size(): number {
    return this.queue.length;
  }
}
