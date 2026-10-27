"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.QueueManager = void 0;
class QueueManager {
    onPlay;
    queue = [];
    isProcessing = false;
    maxQueueSize = 50;
    constructor(onPlay) {
        this.onPlay = onPlay;
    }
    enqueue(item) {
        if (this.queue.length >= this.maxQueueSize) {
            console.warn(`[QueueManager] Queue overflow! Dropping gift event from user ${item.userId}`);
            return false;
        }
        this.queue.push(item);
        this.processNext();
        return true;
    }
    async processNext() {
        if (this.isProcessing || this.queue.length === 0) {
            return;
        }
        this.isProcessing = true;
        const item = this.queue.shift();
        try {
            await this.onPlay(item);
        }
        catch (err) {
            console.error(`[QueueManager] Error processing queue item ${item.id}:`, err);
        }
        finally {
            this.isProcessing = false;
            // Process next in queue
            setImmediate(() => this.processNext());
        }
    }
    getQueueLength() {
        return this.queue.length;
    }
}
exports.QueueManager = QueueManager;
