import * as fs from 'fs';
import * as path from 'path';

export async function fetchTikTokStreamKey(): Promise<{ rtmpUrl: string; streamKey: string; rawResponse?: any }> {
  const cookiesPath = path.resolve('/home/ubuntu/tiktok-live-automation/cookies.json');
  if (!fs.existsSync(cookiesPath)) {
    throw new Error('cookies.json not found. Please login or upload cookies first.');
  }

  const cookiesRaw = fs.readFileSync(cookiesPath, 'utf-8');
  const cookies = JSON.parse(cookiesRaw);
  const cookieHeader = cookies.map((c: any) => `${c.name}=${c.value}`).join('; ');

  const url = 'https://webcast.tiktok.com/webcast/room/create/?aid=1988&app_name=tiktok_web';
  
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Cookie': cookieHeader,
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Referer': 'https://livecenter.tiktok.com/producer',
      'Origin': 'https://livecenter.tiktok.com',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({})
  });

  const text = await response.text();
  let json: any;
  try {
    json = JSON.parse(text);
  } catch (err) {
    throw new Error(`Invalid JSON response from TikTok: ${text.substring(0, 200)}`);
  }

  const rtmpPushUrl = json.data?.stream_url?.rtmp_push_url || '';
  if (!rtmpPushUrl) {
    throw new Error(`TikTok API Response Error: ${JSON.stringify(json)}`);
  }

  // Parse rtmp_push_url (e.g. rtmp://push-rtmp-l11-sg01.tiktokcdn.com/stage/stream-xxx?...)
  const lastSlash = rtmpPushUrl.lastIndexOf('/');
  const rtmpUrl = rtmpPushUrl.substring(0, lastSlash);
  const streamKey = rtmpPushUrl.substring(lastSlash + 1);

  return { rtmpUrl, streamKey, rawResponse: json };
}
