import OBSWebSocket from 'obs-websocket-js';
import * as path from 'path';

export class OBSClient {
  private obs: OBSWebSocket;
  private url: string;
  private password: string;
  private isConnected: boolean = false;

  constructor(url?: string, password?: string) {
    this.obs = new OBSWebSocket();
    this.url = url || process.env.OBS_WEBSOCKET_URL || 'ws://127.0.0.1:4455';
    this.password = password || process.env.OBS_WEBSOCKET_PASSWORD || '';
  }

  public async connect(): Promise<void> {
    try {
      await this.obs.connect(this.url, this.password);
      this.isConnected = true;
      console.log(`[OBSClient] Connected to OBS WebSocket at ${this.url}`);
      await this.ensureSceneAndInput();
    } catch (err) {
      this.isConnected = false;
      console.error(`[OBSClient] Connection failed to ${this.url}:`, err);
      throw err;
    }
  }

  private async ensureSceneAndInput(): Promise<void> {
    try {
      const scenesData: any = await this.obs.call('GetSceneList');
      let currentScene = scenesData.currentProgramSceneName;
      if (!currentScene || scenesData.scenes.length === 0) {
        await this.obs.call('CreateScene', { sceneName: 'Scene Utama' });
        await this.obs.call('SetCurrentProgramScene', { sceneName: 'Scene Utama' });
        currentScene = 'Scene Utama';
      }

      const inputsData: any = await this.obs.call('GetInputList');
      const exists = inputsData.inputs.some((i: any) => i.inputName === 'GiftMediaPlayer');

      if (!exists) {
        const defaultVideo = path.resolve('/home/ubuntu/tiktok-live-automation/media/standby-loop.mp4');
        await this.obs.call('CreateInput', {
          sceneName: currentScene,
          inputName: 'GiftMediaPlayer',
          inputKind: 'ffmpeg_source',
          inputSettings: {
            local_file: defaultVideo,
            is_local_file: true,
            looping: true,
            restart_on_activate: true
          }
        });
        console.log('[OBSClient] Created GiftMediaPlayer ffmpeg_source successfully.');
      }
    } catch (err) {
      console.error('[OBSClient] Error ensuring scene and input:', err);
    }
  }

  public async setStandbyLoop(): Promise<void> {
    if (!this.isConnected) return;
    try {
      const standbyPath = path.resolve('/home/ubuntu/tiktok-live-automation/media/standby-loop.mp4');
      await this.obs.call('SetInputSettings', {
        inputName: 'GiftMediaPlayer',
        inputSettings: {
          local_file: standbyPath,
          is_local_file: true,
          looping: true,
          restart_on_activate: true
        }
      });
      console.log('[OBSClient] Standby loop active on GiftMediaPlayer.');
    } catch (err) {
      console.error('[OBSClient] Error setting standby loop:', err);
    }
  }

  public async playVideo(videoFileName: string, durationMs: number): Promise<void> {
    if (!this.isConnected) {
      console.warn(`[OBSClient] Not connected to OBS. Simulating playback of ${videoFileName} for ${durationMs}ms`);
      await new Promise((resolve) => setTimeout(resolve, Math.min(durationMs, 2000)));
      return;
    }

    try {
      const absolutePath = path.resolve(`/home/ubuntu/tiktok-live-automation/media/${videoFileName}`);
      await this.obs.call('SetInputSettings', {
        inputName: 'GiftMediaPlayer',
        inputSettings: {
          local_file: absolutePath,
          is_local_file: true,
          looping: false,
          restart_on_activate: true
        }
      });

      await new Promise((resolve) => setTimeout(resolve, durationMs));

      // Revert to standby loop after gift finishes
      await this.setStandbyLoop();
    } catch (err) {
      console.error(`[OBSClient] Error playing video ${videoFileName}:`, err);
      try {
        await this.setStandbyLoop();
      } catch {}
      throw err;
    }
  }

  public async startStream(rtmpUrl: string, streamKey: string): Promise<void> {
    if (!this.isConnected) {
      throw new Error('OBS not connected');
    }
    await this.obs.call('SetStreamServiceSettings', {
      streamServiceType: 'rtmp_custom',
      streamServiceSettings: {
        server: rtmpUrl,
        key: streamKey
      }
    });
    await this.obs.call('StartStream');
  }

  public async stopStream(): Promise<void> {
    if (!this.isConnected) {
      throw new Error('OBS not connected');
    }
    await this.obs.call('StopStream');
  }

  public getConnected(): boolean {
    return this.isConnected;
  }
}
