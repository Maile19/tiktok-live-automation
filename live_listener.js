const fs = require('fs');
const path = require('path');
const http = require('http');
const { TikTokLiveConnection } = require('tiktok-live-connector');

const BASE_DIR = __dirname;
const ACCOUNT_CONFIG_FILE = path.join(BASE_DIR, 'account_config.json');
const COOKIES_FILE = path.join(BASE_DIR, 'cookies.json');
const VPN_CONFIG_FILE = path.join(BASE_DIR, 'vpn_config.json');
const DASHBOARD_PORT = process.env.DASHBOARD_PORT || 8089;

function isAutoVpnActive() {
  try {
    if (fs.existsSync(VPN_CONFIG_FILE)) {
      const cfg = JSON.parse(fs.readFileSync(VPN_CONFIG_FILE, 'utf8'));
      return Boolean(cfg && cfg.auto_vpn);
    }
  } catch (e) {}
  return false;
}

function getCookieData() {
  try {
    if (fs.existsSync(COOKIES_FILE)) {
      const cookies = JSON.parse(fs.readFileSync(COOKIES_FILE, 'utf8'));
      if (Array.isArray(cookies)) {
        const header = cookies
          .filter(c => c && c.name && c.value)
          .map(c => `${c.name}=${c.value}`)
          .join('; ');
        
        let sessionId = '';
        let ttTargetIdc = 'alisg';
        for (const c of cookies) {
          if (c.name === 'sessionid' || c.name === 'sessionid_ss') {
            sessionId = c.value;
          }
          if (c.name === 'tt-target-idc') {
            ttTargetIdc = c.value;
          }
        }
        return { cookieHeader: header, sessionId, ttTargetIdc };
      }
    }
  } catch (e) {
    console.error('[LiveListener] Error reading cookies.json:', e.message);
  }
  return { cookieHeader: '', sessionId: '', ttTargetIdc: 'alisg' };
}

function getDeviceProfile() {
  const defaultProfile = {
    user_agent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    browser_name: 'Mozilla',
    browser_version: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    browser_platform: 'Win32',
    os: 'windows',
    sec_ch_ua: '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    sec_ch_ua_mobile: '?0',
    sec_ch_ua_platform: '"Windows"',
    country: 'ID',
    lang: 'id',
    lang_country: 'id-ID',
    tz_name: 'Asia/Jakarta',
    screen_width: 1920,
    screen_height: 1080
  };

  try {
    if (fs.existsSync(ACCOUNT_CONFIG_FILE)) {
      const data = JSON.parse(fs.readFileSync(ACCOUNT_CONFIG_FILE, 'utf8'));
      if (data && data.device_profile) {
        return { ...defaultProfile, ...data.device_profile };
      }
    }
  } catch (e) {}
  return defaultProfile;
}

function getUsername() {
  try {
    if (fs.existsSync(ACCOUNT_CONFIG_FILE)) {
      const data = JSON.parse(fs.readFileSync(ACCOUNT_CONFIG_FILE, 'utf8'));
      if (data && data.username) {
        return data.username.replace(/^@/, '').trim();
      }
    }
  } catch (e) {
    console.error('[LiveListener] Error reading account_config.json:', e.message);
  }
  return '';
}

function sendToDashboard(endpoint, data, retriesLeft = 2) {
  const payload = JSON.stringify(data);
  const options = {
    hostname: '127.0.0.1',
    port: DASHBOARD_PORT,
    path: endpoint,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(payload)
    },
    timeout: 3000
  };

  const req = http.request(options, (res) => {
    res.on('data', () => {});
    if (res.statusCode && res.statusCode >= 400 && retriesLeft > 0) {
      setTimeout(() => sendToDashboard(endpoint, data, retriesLeft - 1), 500);
    }
  });

  req.on('error', (err) => {
    if (retriesLeft > 0) {
      setTimeout(() => sendToDashboard(endpoint, data, retriesLeft - 1), 500);
    }
  });

  req.on('timeout', () => {
    req.destroy();
    if (retriesLeft > 0) {
      setTimeout(() => sendToDashboard(endpoint, data, retriesLeft - 1), 500);
    }
  });

  req.write(payload);
  req.end();
}

let connection = null;
let isRunning = true;
let isConnected = false;
let currentRoomId = null;

async function startConnection() {
  if (!isRunning) return;

  const username = getUsername();
  if (!username) {
    console.log('[LiveListener] Username TikTok belum diset. Menunggu 10 detik...');
    sendToDashboard('/api/listener-status', {
      connected: false,
      error: 'Username belum diset di account_config.json'
    });
    setTimeout(startConnection, 10000);
    return;
  }

  console.log(`[LiveListener] Mencoba menghubungkan ke live @${username}...`);

  try {
    const autoVpnRequired = isAutoVpnActive() || process.env.AUTO_VPN === '1' || process.env.REQUIRE_PROXY === '1';
    let clientOptions = {};
    const proxyUrl = process.env.ALL_PROXY || process.env.HTTPS_PROXY || process.env.TIKTOK_PROXY || (autoVpnRequired ? 'socks5h://127.0.0.1:40000' : '');

    if (autoVpnRequired) {
      if (!proxyUrl) {
        console.error('[LiveListener] 🚨 [Kill-Switch] Auto-VPN aktif tapi proxyUrl tidak ditemukan! Menolak koneksi direct.');
        sendToDashboard('/api/listener-status', {
          connected: false,
          error: 'Kill-Switch Aktif: Proxy VPN tidak tersedia'
        });
        if (isRunning) setTimeout(startConnection, 6000);
        return;
      }

      try {
        const { SocksProxyAgent } = require('socks-proxy-agent');
        const { HttpsProxyAgent } = require('https-proxy-agent');
        const agent = proxyUrl.startsWith('socks') ? new SocksProxyAgent(proxyUrl) : new HttpsProxyAgent(proxyUrl);
        clientOptions = {
          webClientOptions: {
            agent: {
              http: agent,
              https: agent
            }
          },
          wsClientOptions: {
            agent: agent
          }
        };
        console.log(`[LiveListener] 🛡️ [Anti-Leak] Menggunakan Proxy VPN WARP: ${proxyUrl}`);
      } catch (e) {
        console.error(`[LiveListener] 🚨 [Kill-Switch] Gagal memuat proxy agent (${e.message}). Menolak koneksi direct untuk mencegah kebocoran IP VPS!`);
        sendToDashboard('/api/listener-status', {
          connected: false,
          error: 'Kill-Switch Aktif: Gagal inisialisasi proxy agent'
        });
        if (isRunning) setTimeout(startConnection, 6000);
        return;
      }
    } else if (proxyUrl) {
      try {
        const { SocksProxyAgent } = require('socks-proxy-agent');
        const { HttpsProxyAgent } = require('https-proxy-agent');
        const agent = proxyUrl.startsWith('socks') ? new SocksProxyAgent(proxyUrl) : new HttpsProxyAgent(proxyUrl);
        clientOptions = {
          webClientOptions: {
            agent: {
              http: agent,
              https: agent
            }
          },
          wsClientOptions: {
            agent: agent
          }
        };
      } catch (e) {
        console.error('[LiveListener] Gagal memuat proxy agent opsional:', e.message);
      }
    }

    const profile = getDeviceProfile();
    const { cookieHeader, sessionId, ttTargetIdc } = getCookieData();

    const presets = {
      device: {
        user_agent: profile.user_agent,
        browser_name: profile.browser_name || 'Mozilla',
        browser_version: profile.browser_version || '5.0',
        browser_platform: profile.browser_platform || 'Win32',
        os: profile.os || 'windows'
      },
      location: {
        country: profile.country || 'ID',
        lang: profile.lang || 'id',
        lang_country: profile.lang_country || 'id-ID',
        tz_name: profile.tz_name || 'Asia/Jakarta'
      },
      screen: {
        screen_width: profile.screen_width || 1920,
        screen_height: profile.screen_height || 1080
      }
    };

    const clientHeaders = {
      'User-Agent': profile.user_agent,
      'sec-ch-ua': profile.sec_ch_ua || '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
      'sec-ch-ua-mobile': profile.sec_ch_ua_mobile || '?0',
      'sec-ch-ua-platform': profile.sec_ch_ua_platform || '"Windows"',
      'Accept-Language': `${profile.lang_country || 'id-ID'},${profile.lang || 'id'};q=0.9,en-US;q=0.8,en;q=0.7`,
      'Accept-Encoding': 'gzip, deflate, br, zstd',
      'Sec-Fetch-Site': 'same-site',
      'Sec-Fetch-Mode': 'cors',
      'Sec-Fetch-Dest': 'empty'
    };
    if (cookieHeader) {
      clientHeaders['Cookie'] = cookieHeader;
    }

    clientOptions.clientPresets = presets;
    clientOptions.webConfigOverrides = {
      DEFAULT_HTTP_CLIENT_HEADERS: clientHeaders
    };
    if (sessionId) {
      clientOptions.session = {
        cookie: {
          value: {
            sessionId: sessionId,
            ttTargetIdc: ttTargetIdc || 'alisg'
          }
        }
      };
    }

    console.log(`[LiveListener] 🛡️ [Anti-Fingerprint] Profile: ${profile.browser_name} (${profile.os}) | Loc: ${profile.country}/${profile.tz_name} | Cookies: ${cookieHeader ? 'Loaded' : 'None'}`);

    connection = new TikTokLiveConnection(username, clientOptions);

    connection.on('connected', (state) => {
      isConnected = true;
      currentRoomId = state.roomId;
      console.log(`[LiveListener] ✅ Berhasil terhubung ke Live @${username} (Room ID: ${state.roomId})`);
      sendToDashboard('/api/listener-status', {
        connected: true,
        roomId: state.roomId,
        username: username
      });
    });

    connection.on('disconnected', () => {
      isConnected = false;
      console.warn(`[LiveListener] ⚠️ Terputus dari Live @${username}. Mencoba menyambung kembali dalam 8 detik...`);
      sendToDashboard('/api/listener-status', {
        connected: false,
        error: 'Terputus dari live stream'
      });
      if (isRunning) {
        setTimeout(startConnection, 8000);
      }
    });

    connection.on('error', (err) => {
      console.error(`[LiveListener] Error event: ${err.message || err}`);
    });

    connection.on('gift', (data) => {
      // Abaikan streak yang belum selesai (repeatType 1 dan repeatEnd false / 0 / falsy)
      if (data.repeatType === 1 && !data.repeatEnd) {
        return;
      }

      const giftName = data.giftName || (data.gift && data.gift.name) || '';
      const giftId = String(data.giftId || (data.gift && data.gift.id) || '');
      const repeatCount = data.repeatCount || 1;
      const uniqueId = data.uniqueId || (data.user && data.user.uniqueId) || '';
      const nickname = data.nickname || (data.user && data.user.nickname) || uniqueId || 'Penonton';

      console.log(`[LiveListener] 🎁 Gift Diterima: ${giftName} (ID: ${giftId}) x${repeatCount} dari ${nickname} (@${uniqueId})`);

      sendToDashboard('/api/live-gift', {
        giftName: giftName,
        giftId: giftId,
        repeatCount: repeatCount,
        uniqueId: uniqueId,
        nickname: nickname,
        timestamp: Date.now()
      });
    });

    const state = await connection.connect();
    // In case 'connected' event didn't fire before promise resolved
    if (!isConnected && state && state.roomId) {
      isConnected = true;
      currentRoomId = state.roomId;
      sendToDashboard('/api/listener-status', {
        connected: true,
        roomId: state.roomId,
        username: username
      });
    }

  } catch (err) {
    isConnected = false;
    console.warn(`[LiveListener] Gagal terhubung ke @${username} (${err.message}). Mencoba kembali dalam 10 detik...`);
    sendToDashboard('/api/listener-status', {
      connected: false,
      error: err.message
    });
    if (isRunning) {
      setTimeout(startConnection, 10000);
    }
  }
}

// Heartbeat ping to dashboard every 10s
setInterval(() => {
  if (isRunning && isConnected) {
    sendToDashboard('/api/listener-status', {
      connected: isConnected,
      roomId: currentRoomId,
      username: getUsername()
    });
  }
}, 10000);

process.on('SIGINT', () => {
  isRunning = false;
  if (connection) {
    try { connection.disconnect(); } catch (e) {}
  }
  process.exit(0);
});

process.on('SIGTERM', () => {
  isRunning = false;
  if (connection) {
    try { connection.disconnect(); } catch (e) {}
  }
  process.exit(0);
});

startConnection();
