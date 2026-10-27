"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.OBSClient = void 0;
const obs_websocket_js_1 = __importDefault(require("obs-websocket-js"));
class OBSClient {
    obs;
    url;
    password;
    isConnected = false;
    constructor(url, password) {
        this.obs = new obs_websocket_js_1.default();
        this.url = url || process.env.OBS_WEBSOCKET_URL || 'ws://127.0.0.1:4444';
        this.password = password || process.env.OBS_WEBSOCKET_PASSWORD || '';
    }
    async connect() {
        try {
            await this.obs.connect(this.url, this.password);
            this.isConnected = true;
            console.log(`[OBSClient] Connected to OBS WebSocket at ${this.url}`);
        }
        catch (err) {
            this.isConnected = false;
            console.error(`[OBSClient] Connection failed to ${this.url}:`, err);
            throw err;
        }
    }
    async playVideo(videoFileName, durationMs) {
        if (!this.isConnected) {
            console.warn(`[OBSClient] Not connected to OBS. Simulating playback of ${videoFileName} for ${durationMs}ms`);
            await new Promise((resolve) => setTimeout(resolve, Math.min(durationMs, 2000)));
            return;
        }
        try {
            // Example OBS WebSocket v5 command to set media source file and restart
            // Assuming a media source named 'GiftMediaPlayer' exists in OBS
            await this.obs.call('SetInputSettings', {
                inputName: 'GiftMediaPlayer',
                inputSettings: {
                    local_file: videoFileName
                }
            });
            // Wait for duration or media finish
            await new Promise((resolve) => setTimeout(resolve, durationMs));
        }
        catch (err) {
            console.error(`[OBSClient] Error playing video ${videoFileName}:`, err);
            throw err;
        }
    }
    getConnected() {
        return this.isConnected;
    }
}
exports.OBSClient = OBSClient;
