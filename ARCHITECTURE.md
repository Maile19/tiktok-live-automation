# Architecture Document: TikTok Live Automation Engine

## Component Architecture
1. **TikTok Connector (`TikTokClient`)**:
   - Connects to TikTok Live stream via WebSocket/API (`tiktok-live-connector`).
   - Normalizes incoming gift events (`NormalizedGiftEvent`).
2. **Gift Rules Engine (`GiftRulesEngine`)**:
   - Validates gift IDs against `config/gifts.json`.
   - Enforces user-specific cooldown / deduplication logic to prevent spamming.
3. **Queue Manager (`QueueManager`)**:
   - Thread-safe FIFO queue (`maxQueueSize: 50`) ensuring smooth, sequential video playback without overlapping.
4. **OBS WebSocket Client (`OBSClient`)**:
   - Connects to OBS Studio WebSocket (v5).
   - Dynamically updates media source file input and triggers playback.
5. **Health & Simulation API**:
   - Lightweight HTTP server on `127.0.0.1:3000`.
   - Provides `/health` endpoint for monitoring and `/simulate-gift` for offline testing.
