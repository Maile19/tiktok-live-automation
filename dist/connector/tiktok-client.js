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
exports.TikTokClient = void 0;
const dotenv = __importStar(require("dotenv"));
dotenv.config();
class TikTokClient {
    client = null;
    handler = null;
    username;
    constructor(username) {
        this.username = username || process.env.TIKTOK_USERNAME || '';
    }
    onGift(handler) {
        this.handler = handler;
    }
    async connect() {
        if (!this.username) {
            console.warn('[TikTokClient] No TIKTOK_USERNAME set. Skipping live connection.');
            return;
        }
        const { TikTokLiveConnection } = await Promise.resolve().then(() => __importStar(require('tiktok-live-connector')));
        this.client = new TikTokLiveConnection(this.username, {});
        this.client.on('gift', (data) => {
            if (!this.handler)
                return;
            if (data.repeatType === 1 && data.repeatEnd === 0) {
                return;
            }
            const event = {
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
        this.client.on('error', (err) => {
            console.error('[TikTokClient] Error:', err.message);
        });
        await this.client.connect();
        console.log(`[TikTokClient] Connected to @${this.username}`);
    }
}
exports.TikTokClient = TikTokClient;
