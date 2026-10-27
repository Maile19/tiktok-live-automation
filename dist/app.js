"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
const dotenv = __importStar(require("dotenv"));
dotenv.config();
const gift_rules_1 = require("./gifts/gift-rules");
const queue_manager_1 = require("./gifts/queue-manager");
const obs_client_1 = require("./obs/obs-client");
const tiktok_client_1 = require("./connector/tiktok-client");
async function bootstrap() {
    console.log('[Bootstrap] Starting TikTok LIVE Automation Engine...');
    const rulesEngine = new gift_rules_1.GiftRulesEngine();
    const obsClient = new obs_client_1.OBSClient();
    const tiktokClient = new tiktok_client_1.TikTokClient();
    obsClient.connect().catch(() => {
        console.warn('[Bootstrap] OBS WebSocket not reachable. Simulation mode active.');
    });
    const queueManager = new queue_manager_1.QueueManager(async (item) => {
        console.log(`[QueueManager] Playing ${item.config.video} for ${item.config.name} (user: ${item.userId})`);
        await obsClient.playVideo(item.config.video, item.config.durationMs);
    });
    tiktokClient.onGift((event) => {
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
