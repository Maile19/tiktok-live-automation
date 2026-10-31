import http.server
import socketserver
import os
import sys
import socket
import signal
import atexit
import json
import urllib.parse
import urllib.request
try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None
import email.parser
import subprocess
import threading
import time
import http.cookies
import shutil
import asyncio
import websockets
from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

PORT = int(os.environ.get("PORT", 8089))
WS_PORT = int(os.environ.get("WS_PORT", 8086))
BASE_DIR = "/home/ubuntu/tiktok-live-automation"
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.json")
ACCOUNT_CONFIG_FILE = os.path.join(BASE_DIR, "account_config.json")
VIDEOS_DIR = os.path.join(BASE_DIR, "uploads", "videos")
CONFIG_FILE = os.path.join(BASE_DIR, "gift_config.json")
VIDEO_META_FILE = os.path.join(BASE_DIR, "video_metadata.json")
AVATARS_DIR = os.path.join(BASE_DIR, "uploads", "avatars")
OVERLAYS_DIR = os.path.join(BASE_DIR, "uploads", "overlays")
CHARACTER_CONFIG_FILE = os.path.join(BASE_DIR, "character_config.json")
GUIDE_OVERLAY_FILE = os.path.join(OVERLAYS_DIR, "guide_overlay.png")
FFMPEG_LOG_FILE = os.path.join(BASE_DIR, "ffmpeg_stream.log")
STREAM_META_FILE = os.path.join(BASE_DIR, "stream_metadata.json")
LIVE_LISTENER_LOG_FILE = os.path.join(BASE_DIR, "live_listener.log")
MAX_FFMPEG_LOG_SIZE = 5 * 1024 * 1024  # 5 MB

VPN_DIR = os.path.join(BASE_DIR, "vpn")
VPN_CONFIG_FILE = os.path.join(BASE_DIR, "vpn_config.json")
PROXYCHAINS_CONF = os.path.join(VPN_DIR, "proxychains.conf")
WIREPROXY_CONF = os.path.join(VPN_DIR, "wireproxy.conf")

os.makedirs(VIDEOS_DIR, exist_ok=True)
os.makedirs(AVATARS_DIR, exist_ok=True)
os.makedirs(OVERLAYS_DIR, exist_ok=True)
os.makedirs(VPN_DIR, exist_ok=True)

DEFAULT_VPN_CONFIG = {
    "auto_vpn": True,
    "socks5_proxy": "socks5h://127.0.0.1:40000",
    "http_proxy": "http://127.0.0.1:40001",
    "wireproxy_port": 40000
}

def load_vpn_config():
    if os.path.exists(VPN_CONFIG_FILE):
        try:
            with open(VPN_CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                res = dict(DEFAULT_VPN_CONFIG)
                res.update(cfg)
                return res
        except Exception as e:
            print(f"[VPNConfig] Error loading: {e}")
    return dict(DEFAULT_VPN_CONFIG)

def save_vpn_config(cfg):
    try:
        with open(VPN_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[VPNConfig] Error saving: {e}")

def is_wireproxy_running():
    try:
        with socket.create_connection(("127.0.0.1", 40000), timeout=0.8):
            return True
    except Exception:
        return False

def start_wireproxy_vpn():
    """
    Memastikan service wireproxy Cloudflare WARP aktif (via systemd tiktok-vpn-proxy.service
    atau fallback proses background langsung).
    """
    if is_wireproxy_running():
        return True, "Wireproxy sudah aktif"
    try:
        subprocess.run(["sudo", "systemctl", "start", "tiktok-vpn-proxy.service"], check=True, timeout=5)
        for _ in range(8):
            time.sleep(0.4)
            if is_wireproxy_running():
                return True, "VPN WARP aktif via systemd"
    except Exception as e:
        print(f"[VPN] Warning systemctl start: {e}. Fallback ke binary wireproxy langsung...")
    
    try:
        if os.path.exists(WIREPROXY_CONF):
            subprocess.Popen(["/usr/local/bin/wireproxy", "-c", WIREPROXY_CONF, "-s"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(8):
                time.sleep(0.4)
                if is_wireproxy_running():
                    return True, "VPN WARP aktif via background process"
    except Exception as e2:
        return False, f"Gagal menyalakan wireproxy: {e2}"
        
    return False, "Wireproxy gagal mengikat port 127.0.0.1:40000"

def stop_wireproxy_vpn():
    """
    Menghentikan service wireproxy.
    """
    try:
        subprocess.run(["sudo", "systemctl", "stop", "tiktok-vpn-proxy.service"], timeout=5)
    except Exception:
        pass
    try:
        subprocess.run(["pkill", "-f", "wireproxy.*wireproxy.conf"], timeout=3)
    except Exception:
        pass
    return True

vpn_ip_cache = {"ip": "", "warp": False, "ts": 0, "active": False}

def get_current_egress_info(force_refresh=False):
    global vpn_ip_cache
    now = time.time()
    wp_active = is_wireproxy_running()
    if not force_refresh and (now - vpn_ip_cache["ts"] < 6) and vpn_ip_cache["active"] == wp_active and vpn_ip_cache["ip"]:
        return vpn_ip_cache["ip"], vpn_ip_cache["warp"], wp_active

    ip_str = "Memuat..."
    warp_on = False
    
    if wp_active:
        try:
            cmd = ["curl", "-s", "--max-time", "3", "-x", "socks5h://127.0.0.1:40000", "https://cloudflare.com/cdn-cgi/trace"]
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                if line.startswith("ip="):
                    ip_str = line.split("=", 1)[1].strip()
                elif line.startswith("warp=on"):
                    warp_on = True
        except Exception:
            try:
                cmd = ["curl", "-s", "--max-time", "3", "-x", "socks5h://127.0.0.1:40000", "https://api.ipify.org"]
                ip_str = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
                warp_on = True
            except Exception as e:
                ip_str = f"Proxy error: {e}"
    else:
        try:
            cmd = ["curl", "-s", "--max-time", "3", "https://api.ipify.org"]
            ip_str = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            ip_str = "Direct IP"

    vpn_ip_cache = {"ip": ip_str, "warp": warp_on, "ts": now, "active": wp_active}
    return ip_str, warp_on, wp_active

VPS_DIRECT_IP = "100.53.8.161"

watchdog_status = {
    "state": "IDLE",  # "IDLE", "PROTECTED", "RECONNECTING", "EMERGENCY_STOPPED"
    "reconnect_count": 0,
    "last_check_time": time.time(),
    "last_error": None,
    "is_reconnecting": False,
    "emergency_shutdown": False
}
watchdog_lock = threading.RLock()

def check_vpn_health():
    """
    Pengecekan kesehatan VPN berlapis:
    1. Socket TCP lokal ke 127.0.0.1:40000 (SOCKS5) dan 127.0.0.1:40001 (HTTP)
    2. Cloudflare WARP trace endpoint via socks5h://127.0.0.1:40000 (timeout 5s)
    3. Verifikasi IP egress bukan IP asli VPS (100.53.8.161) dan warp=on
    Returns: (is_healthy: bool, details: str, egress_ip: str)
    """
    for port in (40000, 40001):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                pass
        except Exception as e:
            return False, f"Port {port} tidak merespons ({e})", ""

    try:
        cmd = [
            "curl", "-s", "--max-time", "5",
            "-x", "socks5h://127.0.0.1:40000",
            "https://cloudflare.com/cdn-cgi/trace"
        ]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        parsed = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                parsed[k.strip()] = v.strip()
        
        egress_ip = parsed.get("ip", "")
        warp_val = parsed.get("warp", "off")

        if egress_ip == VPS_DIRECT_IP:
            return False, f"LEAK DETECTED: IP yang keluar adalah IP asli VPS ({egress_ip})!", egress_ip

        if warp_val != "on":
            return False, f"WARP Status bukan 'on' (warp={warp_val})", egress_ip

        return True, "VPN WARP Sehat & Terproteksi", egress_ip
    except Exception as e:
        return False, f"Trace via SOCKS5 gagal: {e}", ""

DEFAULT_DEVICE_PROFILE = {
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "browser_name": "Mozilla",
    "browser_version": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "browser_platform": "Win32",
    "os": "windows",
    "platform": "Windows",
    "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec_ch_ua_mobile": "?0",
    "sec_ch_ua_platform": '"Windows"',
    "country": "ID",
    "lang": "id",
    "lang_country": "id-ID",
    "tz_name": "Asia/Jakarta",
    "screen_width": 1920,
    "screen_height": 1080
}

def load_account_config():
    if os.path.exists(ACCOUNT_CONFIG_FILE):
        try:
            with open(ACCOUNT_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"username": ""}

def save_account_config(cfg):
    current = load_account_config()
    current.update(cfg)
    with open(ACCOUNT_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(current, f, indent=2)

def get_device_profile():
    cfg = load_account_config()
    profile = cfg.get("device_profile")
    if not profile or not isinstance(profile, dict) or not profile.get("user_agent"):
        profile = DEFAULT_DEVICE_PROFILE.copy()
        cfg["device_profile"] = profile
        save_account_config(cfg)
    return profile

def update_cookies_store(new_cookies_dict):
    if not new_cookies_dict or not os.path.exists(COOKIES_FILE):
        return
    try:
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            cookie_list = json.load(f)
        
        cookie_map = {c.get("name"): c for c in cookie_list if c.get("name")}
        updated = False
        for k, v in new_cookies_dict.items():
            if k in cookie_map:
                if cookie_map[k].get("value") != v:
                    cookie_map[k]["value"] = v
                    updated = True
            else:
                cookie_map[k] = {
                    "domain": ".tiktok.com",
                    "name": k,
                    "value": v,
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                    "hostOnly": False
                }
                updated = True
        if updated:
            with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                json.dump(list(cookie_map.values()), f, indent=2)
            print(f"[TikTokGateway] 🍪 Auto-updated {len(new_cookies_dict)} cookie(s) into cookies.json")
    except Exception as e:
        print(f"[TikTokGateway] Warning: Failed to update cookies store: {e}")

def tiktok_request_wrapper(url, method='GET', data=None, headers=None, timeout=12):
    """
    Centralized Gateway & Strict Fail-Closed untuk seluruh permintaan outbound HTTP ke domain TikTok.
    Dilengkapi Anti-Fingerprinting TLS Impersonation (BoringSSL Chrome 124 JA3/JA4) via curl_cffi,
    Client Hints (sec-ch-ua-*), serta sinkronisasi profil perangkat terikat account_config.json.
    Ketika auto_vpn: true pada vpn_config.json:
      1. Memastikan Wireproxy VPN aktif (127.0.0.1:40001 & 40000) sebelum request dikirim.
      2. Melakukan verifikasi kesehatan VPN (check_vpn_health): warp=on dan IP != VPS_DIRECT_IP.
      3. STRICT FAIL-CLOSED: Jika VPN mati / leak / tidak siap, TOLAK request seketika! Jangan pernah fallback ke koneksi Direct VPS.
      4. Memaksa pengiriman via Cloudflare WARP HTTP Proxy (http://127.0.0.1:40001).
    """
    vpn_cfg = load_vpn_config()
    auto_vpn = vpn_cfg.get("auto_vpn", True)

    profile = get_device_profile()
    req_headers = {
        "User-Agent": profile.get("user_agent", DEFAULT_DEVICE_PROFILE["user_agent"]),
        "sec-ch-ua": profile.get("sec_ch_ua", DEFAULT_DEVICE_PROFILE["sec_ch_ua"]),
        "sec-ch-ua-mobile": profile.get("sec_ch_ua_mobile", "?0"),
        "sec-ch-ua-platform": profile.get("sec_ch_ua_platform", '"Windows"'),
        "Accept-Language": f"{profile.get('lang_country', 'id-ID')},{profile.get('lang', 'id')};q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    if headers:
        req_headers.update(headers)

    body_payload = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body_payload = json.dumps(data)
            req_headers.setdefault('Content-Type', 'application/json')
        elif isinstance(data, (str, bytes)):
            body_payload = data

    egress_ip = ""
    proxies = None

    if auto_vpn:
        # 1. Pastikan Wireproxy aktif
        if not is_wireproxy_running():
            print("[TikTokGateway] Wireproxy belum aktif. Menyalakan Wireproxy...")
            ok, msg = start_wireproxy_vpn()
            if not ok:
                raise Exception(f"Request diblokir: VPN belum siap, mencegah IP belang. ({msg})")
            time.sleep(1)

        # 2. Verifikasi status kesehatan VPN
        healthy, detail_msg, egress_ip = check_vpn_health()
        if not healthy:
            raise Exception(f"Request diblokir: VPN belum siap, mencegah IP belang. ({detail_msg})")

        if egress_ip == VPS_DIRECT_IP:
            raise Exception(f"Request diblokir: Terdeteksi potensi IP leak ke IP asli VPS ({egress_ip})!")

        # 3. Kirim strictly lewat HTTP Proxy WARP 127.0.0.1:40001
        print(f"[TikTokGateway] 🛡️ [IP-Lock & TLS Impersonate Active] Mengirim {method} {url} via WARP HTTP Proxy (127.0.0.1:40001) | Egress IP: {egress_ip}")
        proxies = {
            'http': 'http://127.0.0.1:40001',
            'https': 'http://127.0.0.1:40001'
        }
    else:
        print(f"[TikTokGateway] 🌐 [Direct Mode] Mengirim {method} {url} langsung tanpa proxy.")
        egress_ip = VPS_DIRECT_IP

    if cffi_requests is not None:
        try:
            resp = cffi_requests.request(
                method.upper(),
                url,
                data=body_payload,
                headers=req_headers,
                proxies=proxies,
                timeout=timeout,
                impersonate="chrome124"
            )
            if resp.cookies:
                update_cookies_store(dict(resp.cookies))

            return {
                "status_code": resp.status_code,
                "body": resp.text,
                "egress_ip": egress_ip,
                "via": "WARP_PROXY" if auto_vpn else "DIRECT"
            }
        except Exception as e:
            via_str = "VPN WARP" if auto_vpn else "langsung"
            raise Exception(f"Gagal menghubungi TikTok API via {via_str} (curl_cffi): {str(e)}")
    else:
        # Fallback to urllib.request
        body_bytes = None
        if body_payload is not None:
            if isinstance(body_payload, str):
                body_bytes = body_payload.encode('utf-8')
            elif isinstance(body_payload, bytes):
                body_bytes = body_payload

        req = urllib.request.Request(url, data=body_bytes, method=method.upper())
        for k, v in req_headers.items():
            req.add_header(k, v)

        if auto_vpn:
            proxy_handler = urllib.request.ProxyHandler(proxies)
            opener = urllib.request.build_opener(proxy_handler)
        else:
            opener = urllib.request.build_opener()

        try:
            with opener.open(req, timeout=timeout) as resp:
                resp_bytes = resp.read()
                resp_text = resp_bytes.decode('utf-8', errors='ignore')
                return {
                    "status_code": resp.status,
                    "body": resp_text,
                    "egress_ip": egress_ip,
                    "via": "WARP_PROXY" if auto_vpn else "DIRECT"
                }
        except Exception as e:
            via_str = "VPN WARP" if auto_vpn else "langsung"
            raise Exception(f"Gagal menghubungi TikTok API via {via_str}: {str(e)}")

def trigger_kill_switch_and_reconnect():
    """
    Eksekusi Kill-Switch dan Seamless Auto-Reconnect:
    1. Hentikan FFmpeg dan live_listener.js segera (JANGAN tutup room TikTok).
    2. Auto-reconnect loop (maks 5x percobaan, jeda 3 detik).
    3. Jika pulih: jalankan ulang FFmpeg & live_listener.js secara mulus.
    4. Jika gagal total: Emergency Shutdown.
    """
    global streaming_process, listener_process
    print("\n[VPNWatchdog] 🚨 KILL-SWITCH DIPICU! Tunnel VPN putus atau terdeteksi leak!")
    print("[VPNWatchdog] 🛑 Menghentikan FFmpeg & live_listener segera agar tidak ada paket bocor...")

    with watchdog_lock:
        watchdog_status["state"] = "RECONNECTING"
        watchdog_status["is_reconnecting"] = True

    # 1. Kill FFmpeg immediately (Do NOT close TikTok room)
    with stream_lock:
        if streaming_process:
            try:
                streaming_process.terminate()
            except Exception:
                pass
            try:
                os.kill(streaming_process.pid, 9)
            except Exception:
                pass
            streaming_process = None
        try:
            out = subprocess.check_output(["pgrep", "-f", "ffmpeg.*rtmp"], text=True).strip()
            for p in out.splitlines():
                try:
                    os.kill(int(p), 9)
                except Exception:
                    pass
        except Exception:
            pass

    # 2. Kill listener immediately
    if listener_process:
        try:
            listener_process.terminate()
        except Exception:
            pass
        try:
            os.kill(listener_process.pid, 9)
        except Exception:
            pass
        listener_process = None
    try:
        subprocess.run(["pkill", "-9", "-f", "node.*live_listener.js"], timeout=2)
    except Exception:
        pass

    # 3. Auto-reconnect loop (maks 5x, jeda 3s)
    reconnected = False
    for attempt in range(1, 6):
        print(f"[VPNWatchdog] 🔄 Percobaan Reconnect ke-{attempt}/5 (Restarting Wireproxy)...")
        try:
            subprocess.run(["sudo", "systemctl", "restart", "tiktok-vpn-proxy.service"], timeout=8)
        except Exception as err:
            print(f"[VPNWatchdog] Warning restart: {err}")
        
        time.sleep(3)
        h_ok, h_msg, h_ip = check_vpn_health()
        if h_ok:
            print(f"[VPNWatchdog] ✅ Reconnect BERHASIL pada percobaan ke-{attempt}! Egress IP: {h_ip}")
            reconnected = True
            break
        else:
            print(f"[VPNWatchdog] ⚠️ Percobaan ke-{attempt} belum pulih: {h_msg}")

    with watchdog_lock:
        if reconnected:
            watchdog_status["reconnect_count"] += 1
            watchdog_status["state"] = "PROTECTED"
            watchdog_status["is_reconnecting"] = False
            watchdog_status["last_error"] = None
        else:
            watchdog_status["state"] = "EMERGENCY_STOPPED"
            watchdog_status["emergency_shutdown"] = True
            watchdog_status["is_reconnecting"] = False
            watchdog_status["last_error"] = "Gagal reconnect setelah 5 percobaan."

    if reconnected:
        get_current_egress_info(force_refresh=True)

        with stream_lock:
            current_slot = stream_metadata.get("currentSlot", 1)
            rtmp_url = stream_metadata.get("rtmpUrl", "")
            stream_key = stream_metadata.get("rawStreamKey", "")
            room_id = stream_metadata.get("roomId", "")

        if rtmp_url and stream_key:
            print(f"[VPNWatchdog] 🚀 Melanjutkan streaming FFmpeg aktif (Slot {current_slot}) ke TikTok Live...")
            try:
                start_ffmpeg_stream(
                    rtmp_url=rtmp_url,
                    stream_key=stream_key,
                    room_id=room_id,
                    slot_num=current_slot,
                    loop=(current_slot == 1)
                )
            except Exception as e:
                print(f"[VPNWatchdog] Error menyalakan ulang FFmpeg: {e}")

        try:
            ensure_live_listener(force_restart=True)
        except Exception as e:
            print(f"[VPNWatchdog] Error menyalakan ulang live listener: {e}")
    else:
        print("[VPNWatchdog] 🛑 EMERGENCY SHUTDOWN DIKUNCI: Streaming dihentikan permanen. Mencegah transmisi ke TikTok!")
        with stream_lock:
            stream_metadata["active"] = False
            save_stream_metadata()

def vpn_watchdog_loop():
    """
    Background daemon thread yang aktif memantau koneksi setiap 10 detik saat
    live streaming berjalan dan auto_vpn aktif.
    """
    print("[VPNWatchdog] Daemon Watchdog dimulai (interval: 10s)...")
    while True:
        try:
            time.sleep(10)
            now = time.time()
            with watchdog_lock:
                watchdog_status["last_check_time"] = now
                is_reconn = watchdog_status["is_reconnecting"]

            if is_reconn:
                continue

            vpn_cfg = load_vpn_config()
            auto_vpn = vpn_cfg.get("auto_vpn", True)
            is_live = (get_ffmpeg_pid() is not None)

            if not auto_vpn:
                with watchdog_lock:
                    watchdog_status["state"] = "IDLE"
                continue

            with watchdog_lock:
                current_state = watchdog_status["state"]
                is_emerg = watchdog_status["emergency_shutdown"]

            if is_emerg:
                continue

            wp_running = is_wireproxy_running()
            if not is_live and not wp_running and current_state != "PROTECTED":
                with watchdog_lock:
                    watchdog_status["state"] = "IDLE"
                continue

            # Lakukan Health Check berlapis saat Live streaming atau VPN aktif
            healthy, detail_msg, egress_ip = check_vpn_health()
            if not healthy:
                print(f"[VPNWatchdog] ⚠️ Health check ke-1 gagal: {detail_msg}. Retry dalam 2 detik...")
                time.sleep(2)
                healthy, detail_msg, egress_ip = check_vpn_health()
                if not healthy:
                    print(f"[VPNWatchdog] 🚨 Health check ke-2 GAGAL: {detail_msg}!")
                    with watchdog_lock:
                        watchdog_status["last_error"] = detail_msg
                    trigger_kill_switch_and_reconnect()
                else:
                    with watchdog_lock:
                        watchdog_status["state"] = "PROTECTED"
            else:
                with watchdog_lock:
                    watchdog_status["state"] = "PROTECTED"
                    watchdog_status["last_error"] = None

        except Exception as e:
            print(f"[VPNWatchdog] Loop error: {e}")
            time.sleep(2)

def start_vpn_watchdog():
    t = threading.Thread(target=vpn_watchdog_loop, daemon=True)
    t.start()

import collections

def rotate_ffmpeg_log_if_needed():
    if os.path.exists(FFMPEG_LOG_FILE):
        try:
            if os.path.getsize(FFMPEG_LOG_FILE) > MAX_FFMPEG_LOG_SIZE:
                backup_file = FFMPEG_LOG_FILE + ".1"
                if os.path.exists(backup_file):
                    os.remove(backup_file)
                os.replace(FFMPEG_LOG_FILE, backup_file)
        except Exception as e:
            print(f"[LogRotate] Warning: {e}")

def check_has_audio(filepath):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "csv=p=0", filepath],
            capture_output=True, text=True, timeout=3
        )
        return bool(res.stdout.strip())
    except Exception:
        return True

streaming_process = None
dual_screen_active = False
dual_screen_ws_client = None

def is_dual_screen_active():
    global dual_screen_active, streaming_process
    return dual_screen_active and streaming_process is not None and streaming_process.poll() is None
stream_metadata = {
    "active": False,
    "rtmpUrl": "",
    "streamKey": "",
    "rawStreamKey": "",
    "roomId": "",
    "currentSlot": 1,
    "currentVideo": "",
    "startTime": None,
    "error": None,
    "rawResponse": None
}
stream_lock = threading.RLock()

listener_process = None
listener_status = {
    "connected": False,
    "roomId": "",
    "username": "",
    "lastConnectedTime": None,
    "lastError": None,
    "totalGiftsReceived": 0,
    "lastPing": 0
}
gift_queue = collections.deque(maxlen=15)
queue_lock = threading.RLock()
recent_gift_logs = collections.deque(maxlen=10)
global_gift_seq = 0
latest_gift_event = None

def load_stream_metadata():
    global stream_metadata
    if os.path.exists(STREAM_META_FILE):
        try:
            with open(STREAM_META_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                stream_metadata.update(data)
        except Exception as e:
            print(f"[StreamMeta] Error loading: {e}")

def save_stream_metadata():
    try:
        with open(STREAM_META_FILE, 'w', encoding='utf-8') as f:
            json.dump(stream_metadata, f, indent=2)
    except Exception as e:
        print(f"[StreamMeta] Error saving: {e}")

load_stream_metadata()

GIFTS_CATALOG = [
    {"id": "rose", "name": "Mawar (Rose)", "coins": 1, "idr": 250, "icon": "🌹"},
    {"id": "finger_heart", "name": "Finger Heart", "coins": 5, "idr": 1250, "icon": "🫶"},
    {"id": "ice_cream", "name": "Ice Cream", "coins": 10, "idr": 2500, "icon": "🍦"},
    {"id": "confetti", "name": "Confetti", "coins": 100, "idr": 25000, "icon": "🎉"},
    {"id": "doughnut", "name": "Doughnut", "coins": 30, "idr": 7500, "icon": "🍩"},
    {"id": "sunglasses", "name": "Sunglasses", "coins": 199, "idr": 49750, "icon": "🕶️"},
    {"id": "perfume", "name": "Perfume", "coins": 200, "idr": 50000, "icon": "🛍️"},
    {"id": "cap", "name": "Cap", "coins": 99, "idr": 24750, "icon": "🧢"},
    {"id": "lion", "name": "Singa (Lion)", "coins": 29999, "idr": 7499750, "icon": "🦁"},
    {"id": "universe", "name": "TikTok Universe", "coins": 44999, "idr": 11249750, "icon": "🌌"},
    {"id": "whale", "name": "Audacious Whale", "coins": 21500, "idr": 5375000, "icon": "🐳"},
    {"id": "yacht", "name": "Falcon / Yacht", "coins": 5000, "idr": 1250000, "icon": "🛥️"}
]



def validate_tiktok_session(username=None):
    """
    Memvalidasi session/cookie TikTok secara forensik melalui Centralized Gateway tiktok_request_wrapper.
    WAJIB melewati Wireproxy (127.0.0.1:40001 / 40000) jika auto_vpn: true.
    Mencatat last_verified_ip, last_verified_via, dan timestamp ke account_config.json.
    """
    if not os.path.exists(COOKIES_FILE):
        return {
            "success": False,
            "message": "File cookies.json tidak ditemukan. Harap tempelkan cookies terlebih dahulu.",
            "egress_ip": "",
            "via": "NONE"
        }

    try:
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        cookie_header = "; ".join([f"{c.get('name')}={c.get('value')}" for c in cookies if c.get('name') and c.get('value')])
    except Exception as e:
        return {
            "success": False,
            "message": f"Gagal membaca cookies.json: {e}",
            "egress_ip": "",
            "via": "NONE"
        }

    url = "https://www.tiktok.com/passport/web/account/info/"
    headers = {
        "Cookie": cookie_header,
        "Referer": "https://www.tiktok.com/",
        "Accept": "application/json, text/plain, */*"
    }

    try:
        res = tiktok_request_wrapper(url, method="GET", headers=headers, timeout=12)
        body_str = res.get("body", "")
        egress_ip = res.get("egress_ip", "")
        via = res.get("via", "WARP_PROXY")

        json_data = json.loads(body_str)
        user_data = json_data.get("data", {})
        user_id = user_data.get("user_id") or user_data.get("user_id_str")
        resp_username = user_data.get("username", "")
        screen_name = user_data.get("screen_name", "")

        is_valid = bool(user_id)
        now_str = time.strftime("%Y-%m-%d %H:%M:%S WIB", time.localtime())

        account_cfg = load_account_config()
        if username:
            account_cfg["username"] = username.strip()
        elif resp_username and not account_cfg.get("username"):
            account_cfg["username"] = f"@{resp_username}"

        account_cfg["user_id"] = str(user_id) if user_id else ""
        account_cfg["screen_name"] = screen_name
        account_cfg["last_verified_ip"] = egress_ip
        account_cfg["last_verified_via"] = via
        account_cfg["last_verified_time"] = time.time()
        account_cfg["last_verified_str"] = now_str
        account_cfg["session_valid"] = is_valid
        if is_valid:
            account_cfg["last_error"] = None
        else:
            account_cfg["last_error"] = json_data.get("message", "Session tidak valid atau expired")

        save_account_config(account_cfg)

        if is_valid:
            print(f"[AccountValidator] ✅ Session TikTok Valid (@{resp_username or screen_name}) | Route: {via} ({egress_ip})")
            return {
                "success": True,
                "username": resp_username or screen_name,
                "user_id": user_id,
                "egress_ip": egress_ip,
                "via": via,
                "message": f"Session TikTok Valid! Terkunci pada {via} ({egress_ip})"
            }
        else:
            msg = json_data.get("message", "Sessionid atau cookies sudah kedaluwarsa")
            print(f"[AccountValidator] ❌ Session TikTok Tidak Valid: {msg} | Route: {via} ({egress_ip})")
            return {
                "success": False,
                "message": f"Cookies tidak valid: {msg}",
                "egress_ip": egress_ip,
                "via": via
            }
    except Exception as err:
        err_str = str(err)
        print(f"[AccountValidator] 🚨 Error validasi session: {err_str}")
        account_cfg = load_account_config()
        account_cfg["session_valid"] = False
        account_cfg["last_error"] = err_str
        save_account_config(account_cfg)
        return {
            "success": False,
            "message": err_str,
            "egress_ip": "",
            "via": "ERROR"
        }

def load_gift_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"2": "rose", "3": "doughnut", "4": "confetti", "5": "lion", "6": "perfume"}

def save_gift_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)

def load_video_meta():
    if os.path.exists(VIDEO_META_FILE):
        try:
            with open(VIDEO_META_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_video_meta(meta):
    try:
        with open(VIDEO_META_FILE, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        print(f"[VideoMeta] Error saving metadata: {e}")

DEFAULT_CHARACTER_CONFIG = {
    "overlay_enabled": True,
    "position": {"x": 60, "y": 120},
    "characters": {
        "1": {"name": "Karakter 1 (Kucing Kribo)", "avatar": ""},
        "2": {"name": "Karakter 2", "avatar": ""},
        "3": {"name": "Karakter 3", "avatar": ""},
        "4": {"name": "Karakter 4", "avatar": ""}
    },
    "mappings": [
        {"slot": 2, "gift_id": "rose", "character_ids": ["1"]},
        {"slot": 3, "gift_id": "ice_cream", "character_ids": ["2"]},
        {"slot": 4, "gift_id": "cap", "character_ids": ["3"]},
        {"slot": 5, "gift_id": "sunglasses", "character_ids": ["1", "2", "3", "4"]},
        {"slot": 6, "gift_id": "perfume", "character_ids": ["4"]}
    ]
}

CHAR_COLORS = {
    "1": (37, 244, 238),   # Cyan (#25f4ee)
    "2": (255, 43, 84),   # TikTok Pink (#ff2b54)
    "3": (255, 184, 0),   # Yellow/Gold (#ffb800)
    "4": (168, 85, 247)   # Purple (#a855f7)
}

def load_character_config():
    if os.path.exists(CHARACTER_CONFIG_FILE):
        try:
            with open(CHARACTER_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                res = dict(DEFAULT_CHARACTER_CONFIG)
                res.update(data)
                if "characters" not in res:
                    res["characters"] = DEFAULT_CHARACTER_CONFIG["characters"]
                if "mappings" not in res:
                    res["mappings"] = DEFAULT_CHARACTER_CONFIG["mappings"]
                else:
                    existing_slots = {m.get("slot") for m in res["mappings"]}
                    for def_m in DEFAULT_CHARACTER_CONFIG["mappings"]:
                        if def_m["slot"] not in existing_slots:
                            res["mappings"].append(def_m)
                if "position" not in res:
                    res["position"] = DEFAULT_CHARACTER_CONFIG["position"]
                return res
        except Exception as e:
            print(f"[CharConfig] Error loading: {e}")
    return dict(DEFAULT_CHARACTER_CONFIG)

def save_character_config(cfg):
    try:
        with open(CHARACTER_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[CharConfig] Error saving: {e}")

def make_circle_avatar(image_path, size=34, border_color=(255,255,255), char_id='1'):
    badge_font = None
    text_font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.exists(text_font_path):
        try:
            badge_font = ImageFont.truetype(text_font_path, 13)
        except Exception:
            badge_font = ImageFont.load_default()
    else:
        badge_font = ImageFont.load_default()

    if image_path and os.path.exists(image_path):
        try:
            src = Image.open(image_path).convert('RGBA')
            w, h = src.size
            min_dim = min(w, h)
            left = (w - min_dim) // 2
            top = (h - min_dim) // 2
            src = src.crop((left, top, left + min_dim, top + min_dim))
            scale = 4
            src_large = src.resize((size * scale, size * scale), Image.Resampling.LANCZOS)
            mask = Image.new('L', (size * scale, size * scale), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, size * scale - 1, size * scale - 1), fill=255)
            src_large.putalpha(mask)
            circle_img = src_large.resize((size, size), Image.Resampling.LANCZOS)
            border_draw = ImageDraw.Draw(circle_img)
            border_draw.ellipse((0, 0, size - 1, size - 1), outline=border_color, width=2)
            return circle_img
        except Exception:
            pass
    
    badge = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(badge)
    bg_col = CHAR_COLORS.get(str(char_id), (100, 100, 100))
    bdraw.ellipse((0, 0, size - 1, size - 1), fill=bg_col + (240,), outline=(255, 255, 255, 220), width=2)
    label = f'K{char_id}'
    bbox = bdraw.textbbox((0, 0), label, font=badge_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) / 2
    ty = (size - th) / 2 - 1
    bdraw.text((tx, ty), label, fill=(255, 255, 255), font=badge_font)
    return badge

def render_gift_icon(gift_id, size=34):
    gift_map = {g['id']: g for g in GIFTS_CATALOG}
    g_info = gift_map.get(gift_id, {'icon': '🎁', 'name': gift_id})
    icon_char = g_info.get('icon', '🎁')
    icon_img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    
    emoji_font_path = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
    if os.path.exists(emoji_font_path):
        try:
            efont = ImageFont.truetype(emoji_font_path, 109)
            temp = Image.new('RGBA', (130, 130), (0, 0, 0, 0))
            tdraw = ImageDraw.Draw(temp)
            tdraw.text((10, 5), icon_char, font=efont, embedded_color=True)
            icon_img = temp.resize((size, size), Image.Resampling.LANCZOS)
            return icon_img
        except Exception:
            pass
            
    tdraw = ImageDraw.Draw(icon_img)
    tdraw.rounded_rectangle((0, 0, size - 1, size - 1), radius=6, fill=(40, 40, 45, 220), outline=(80, 80, 90, 200), width=1)
    sfont = ImageFont.load_default()
    tdraw.text((4, 6), icon_char, font=sfont, fill=(255, 255, 255))
    return icon_img

def generate_guide_overlay(cfg=None):
    if cfg is None:
        cfg = load_character_config()
            
    mappings = cfg.get('mappings', [])
    if not mappings:
        return None
        
    icon_sz = 34
    pad_x = 16
    pad_y = 12
    row_h = 42
    spacing_y = 8
    num_rows = len(mappings)

    text_font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    text_font = ImageFont.truetype(text_font_path, 16) if os.path.exists(text_font_path) else ImageFont.load_default()

    max_row_w = 0
    for m in mappings:
        c_ids = m.get('character_ids', [])
        row_w = icon_sz + 12 + 18 + 12 + max(1, len(c_ids)) * (icon_sz + 6)
        if row_w > max_row_w:
            max_row_w = row_w

    total_w = max(260, max_row_w + pad_x * 2)
    total_h = pad_y * 2 + num_rows * row_h + (num_rows - 1) * spacing_y

    # 100% transparent background (clean floating overlay)
    overlay = Image.new('RGBA', (total_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    curr_y = pad_y
    for m in mappings:
        g_id = m.get('gift_id', 'rose')
        c_ids = m.get('character_ids', [])
        
        # 1. Gift Icon
        g_icon = render_gift_icon(g_id, size=icon_sz)
        overlay.paste(g_icon, (pad_x, curr_y), g_icon)
        
        # 2. Equal sign '=' with dark drop-shadow/outline for high readability over bright videos
        eq_x = pad_x + icon_sz + 12
        eq_y = curr_y + (icon_sz - 18) // 2
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
            draw.text((eq_x + dx, eq_y + dy), '=', fill=(0, 0, 0, 220), font=text_font)
        draw.text((eq_x, eq_y), '=', fill=(37, 244, 238, 255), font=text_font)
        
        # 3. Target avatars
        start_c_x = eq_x + 20
        if not c_ids:
            empty_badge = Image.new('RGBA', (icon_sz, icon_sz), (0, 0, 0, 0))
            edraw = ImageDraw.Draw(empty_badge)
            edraw.ellipse((0, 0, icon_sz - 1, icon_sz - 1), outline=(100, 100, 100, 180), width=1)
            edraw.text((icon_sz//2 - 4, icon_sz//2 - 8), '-', fill=(150, 150, 150), font=text_font)
            overlay.paste(empty_badge, (start_c_x, curr_y), empty_badge)
        else:
            for idx, cid in enumerate(c_ids):
                ax = start_c_x + idx * (icon_sz + 6)
                av_path = None
                for ext in ('.png', '.jpg', '.jpeg', '.webp'):
                    cand = os.path.join(AVATARS_DIR, f'char_{cid}{ext}')
                    if os.path.exists(cand):
                        av_path = cand
                        break
                bcol = CHAR_COLORS.get(str(cid), (255, 255, 255))
                av_img = make_circle_avatar(av_path, size=icon_sz, border_color=bcol, char_id=cid)
                overlay.paste(av_img, (ax, curr_y), av_img)
            
        curr_y += row_h + spacing_y

    # Atomic write to prevent file corruption during FFmpeg streaming
    temp_overlay_file = GUIDE_OVERLAY_FILE + ".tmp"
    overlay.save(temp_overlay_file, format="PNG")
    os.replace(temp_overlay_file, GUIDE_OVERLAY_FILE)
    return GUIDE_OVERLAY_FILE

def get_ffmpeg_pid():
    global streaming_process
    if streaming_process and streaming_process.poll() is None:
        return streaming_process.pid
    try:
        out = subprocess.check_output(["pgrep", "-f", "ffmpeg.*rtmp"], text=True).strip()
        if out:
            pids = out.splitlines()
            return int(pids[0])
    except Exception:
        pass
    return None

def end_tiktok_live_room(room_id=None):
    if not room_id:
        room_id = stream_metadata.get("roomId", "")
    if not os.path.exists(COOKIES_FILE):
        return
    try:
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        cookie_header = "; ".join([f"{c.get('name')}={c.get('value')}" for c in cookies if c.get('name') and c.get('value')])
        
        target_endpoints = []
        if room_id:
            target_endpoints.append(f"https://webcast.tiktok.com/webcast/room/finish_abnormal/?room_id={room_id}&aid=1988&app_name=tiktok_web")
            target_endpoints.append(f"https://webcast.tiktok.com/webcast/room/finish/?room_id={room_id}&aid=1988&app_name=tiktok_web")
        target_endpoints.append("https://webcast.tiktok.com/webcast/room/finish_abnormal/?aid=1988&app_name=tiktok_web")
        
        headers = {
            'Cookie': cookie_header,
            'Referer': 'https://livecenter.tiktok.com/producer',
            'Origin': 'https://livecenter.tiktok.com',
            'Content-Type': 'application/json'
        }

        for ep in target_endpoints:
            try:
                res = tiktok_request_wrapper(ep, method='POST', data=b'{}', headers=headers, timeout=6)
                print(f"[EndTikTokRoom] Response from {ep} via {res.get('via')} ({res.get('egress_ip')}): {res.get('body')[:150]}")
            except Exception as e:
                print(f"[EndTikTokRoom] Request error {ep}: {e}")
    except Exception as err:
        print(f"[EndTikTokRoom] General error: {err}")

def get_video_duration(video_path):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=5
        )
        return float(res.stdout.strip())
    except Exception:
        return 10.0

def stop_ffmpeg_stream(close_tiktok_room=False):
    global streaming_process, stream_metadata
    with stream_lock:
        if streaming_process:
            proc = streaming_process
            streaming_process = None
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            except Exception:
                pass
            finally:
                if proc.poll() is None:
                    try:
                        os.kill(proc.pid, 9)
                        proc.wait(timeout=1)
                    except Exception:
                        pass
        else:
            try:
                out = subprocess.check_output(["pgrep", "-f", "ffmpeg.*rtmp"], text=True).strip()
                if out:
                    for pid_str in out.splitlines():
                        try:
                            os.kill(int(pid_str), 15)
                        except Exception:
                            pass
            except Exception:
                pass

        if close_tiktok_room:
            # Send termination request to TikTok Live API HANYA jika diminta eksplisit
            try:
                out = subprocess.check_output(["pgrep", "-f", "ffmpeg.*rtmp"], text=True).strip()
                if out:
                    for pid_str in out.splitlines():
                        try:
                            os.kill(int(pid_str), 9)
                        except Exception:
                            pass
            except Exception:
                pass

            active_room_id = stream_metadata.get("roomId", "")
            end_tiktok_live_room(active_room_id)

            stream_metadata["active"] = False
            stream_metadata["startTime"] = None
            stream_metadata["rtmpUrl"] = ""
            stream_metadata["streamKey"] = ""
            stream_metadata["rawStreamKey"] = ""
            stream_metadata["roomId"] = ""
            stream_metadata["currentSlot"] = 1
            stream_metadata["currentVideo"] = ""
            save_stream_metadata()
        else:
            # Hanya mematikan proses FFmpeg, jangan hapus URL RTMP & Stream Key aktif
            stream_metadata["active"] = False
            save_stream_metadata()

def start_ffmpeg_stream(rtmp_url=None, stream_key=None, room_id="", video_override=None, loop=True, slot_num=1):
    global streaming_process, stream_metadata
    # HANYA matikan proses FFmpeg lokal, JANGAN tutup room TikTok
    stop_ffmpeg_stream(close_tiktok_room=False)
    time.sleep(1.2) # Jeda untuk memastikan socket RTMP di sisi edge server TikTok benar-benar rilis
    
    if not rtmp_url:
        rtmp_url = stream_metadata.get("rtmpUrl", "")
    if not stream_key:
        stream_key = stream_metadata.get("rawStreamKey", "")
    if not room_id:
        room_id = stream_metadata.get("roomId", "")

    if not rtmp_url or not stream_key:
        raise Exception("Stream RTMP URL atau Stream Key belum tersedia. Harap Start Live terlebih dahulu.")
    
    video_path = video_override
    if not video_path:
        for ext in ("webm", "mp4"):
            candidate = os.path.join(VIDEOS_DIR, f"video_{slot_num}.{ext}")
            if os.path.exists(candidate):
                video_path = candidate
                break
    if not video_path:
        video_path = os.path.join(BASE_DIR, "media", "standby-loop.mp4")
    
    if not os.path.exists(video_path):
        raise Exception(f"Video file tidak ditemukan untuk Slot {slot_num}: uploads/videos/video_{slot_num}.webm atau media/standby-loop.mp4")

    clean_rtmp = rtmp_url.rstrip('/')
    destination = f"{clean_rtmp}/{stream_key}"
    
    char_cfg = load_character_config()
    overlay_enabled = char_cfg.get("overlay_enabled", True)
    pos = char_cfg.get("position", {"x": 60, "y": 120})
    pos_x = pos.get("x", 60)
    pos_y = pos.get("y", 120)

    if overlay_enabled and not os.path.exists(GUIDE_OVERLAY_FILE):
        try:
            generate_guide_overlay(char_cfg)
        except Exception as e:
            print(f"[StreamingEngine] Error generating overlay: {e}")

    has_overlay = overlay_enabled and os.path.exists(GUIDE_OVERLAY_FILE)
    has_audio = check_has_audio(video_path)

    loop_args = ["-stream_loop", "-1"] if loop else []
    scale_filter = "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2"

    cmd = [
        "ffmpeg",
        "-nostats",
        "-loglevel", "warning",
        "-re"
    ] + loop_args + [
        "-i", video_path
    ]

    if has_overlay:
        cmd += ["-i", GUIDE_OVERLAY_FILE]
        filter_str = f"[0:v]{scale_filter}[vscaled];[vscaled][1:v]overlay=x={pos_x}:y={pos_y}:eof_action=repeat[outv]"
    else:
        filter_str = f"[0:v]{scale_filter}[outv]"

    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        silent_audio_idx = 2 if has_overlay else 1
        audio_map = f"{silent_audio_idx}:a"
    else:
        audio_map = "0:a"

    cmd += [
        "-filter_complex", filter_str,
        "-map", "[outv]",
        "-map", audio_map,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-b:v", "1200k",
        "-maxrate", "1200k",
        "-bufsize", "2400k",
        "-pix_fmt", "yuv420p",
        "-g", "60",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-f", "flv",
        destination
    ]

    vpn_cfg = load_vpn_config()
    should_use_vpn = vpn_cfg.get("auto_vpn", True) and is_wireproxy_running()
    ffmpeg_env = os.environ.copy()
    if should_use_vpn:
        ffmpeg_env["ALL_PROXY"] = "socks5h://127.0.0.1:40000"
        ffmpeg_env["all_proxy"] = "socks5h://127.0.0.1:40000"
        ffmpeg_env["HTTP_PROXY"] = "http://127.0.0.1:40001"
        ffmpeg_env["HTTPS_PROXY"] = "http://127.0.0.1:40001"
        ffmpeg_env["http_proxy"] = "http://127.0.0.1:40001"
        ffmpeg_env["https_proxy"] = "http://127.0.0.1:40001"
        if os.path.exists(PROXYCHAINS_CONF):
            cmd = ["proxychains4", "-q", "-f", PROXYCHAINS_CONF] + cmd

    rotate_ffmpeg_log_if_needed()
    print(f"[StreamingEngine] Starting standardized ffmpeg push to TikTok (Slot {slot_num}): {' '.join(cmd[:12])}...")
    
    with stream_lock:
        err_sink = subprocess.DEVNULL
        try:
            ffmpeg_log_fp = open(FFMPEG_LOG_FILE, "a", encoding="utf-8")
            ffmpeg_log_fp.write(f"\n--- [FFmpeg Stream Start: {time.strftime('%Y-%m-%d %H:%M:%S')} | Slot: {slot_num}] ---\n")
            ffmpeg_log_fp.write(f"Command: {' '.join(cmd)}\n")
            if should_use_vpn:
                ffmpeg_log_fp.write("Network: Cloudflare WARP SOCKS5 Tunnel (127.0.0.1:40000)\n")
            ffmpeg_log_fp.flush()
            err_sink = ffmpeg_log_fp
        except Exception as log_err:
            print(f"[StreamingEngine] Warning: cannot open {FFMPEG_LOG_FILE}: {log_err}")
            err_sink = subprocess.DEVNULL

        streaming_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=err_sink,
            env=ffmpeg_env
        )

        # Close log file descriptor in parent process immediately after Popen
        if err_sink and err_sink != subprocess.DEVNULL:
            try:
                err_sink.close()
            except Exception:
                pass

        stream_metadata["active"] = True
        stream_metadata["startTime"] = time.time()
        stream_metadata["rtmpUrl"] = rtmp_url
        stream_metadata["streamKey"] = stream_key[:10] + "..." if stream_key else ""
        stream_metadata["rawStreamKey"] = stream_key
        stream_metadata["roomId"] = room_id
        stream_metadata["currentSlot"] = slot_num
        stream_metadata["currentVideo"] = os.path.basename(video_path)
        save_stream_metadata()

def start_dual_screen_ffmpeg_stream(rtmp_url=None, stream_key=None, room_id=""):
    global streaming_process, stream_metadata, dual_screen_active
    stop_ffmpeg_stream(close_tiktok_room=False)
    time.sleep(0.5)

    if not rtmp_url or not stream_key:
        load_stream_metadata()
        rtmp_url = stream_metadata.get("rtmpUrl", "")
        stream_key = stream_metadata.get("rawStreamKey", "")
        room_id = stream_metadata.get("roomId", "")

    if not rtmp_url or not stream_key:
        print("[DualScreen] Stream RTMP URL atau Stream Key belum tersedia.")
        return None

    clean_rtmp = rtmp_url.rstrip('/')
    destination = f"{clean_rtmp}/{stream_key}"

    cmd = [
        "ffmpeg",
        "-nostats",
        "-loglevel", "warning",
        "-f", "matroska",
        "-i", "pipe:0",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-b:v", "1500k",
        "-maxrate", "1500k",
        "-bufsize", "3000k",
        "-pix_fmt", "yuv420p",
        "-g", "60",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-f", "flv",
        destination
    ]

    vpn_cfg = load_vpn_config()
    should_use_vpn = vpn_cfg.get("auto_vpn", True) and is_wireproxy_running()
    ffmpeg_env = os.environ.copy()
    if should_use_vpn:
        ffmpeg_env["ALL_PROXY"] = "socks5h://127.0.0.1:40000"
        ffmpeg_env["all_proxy"] = "socks5h://127.0.0.1:40000"
        ffmpeg_env["HTTP_PROXY"] = "http://127.0.0.1:40001"
        ffmpeg_env["HTTPS_PROXY"] = "http://127.0.0.1:40001"
        ffmpeg_env["http_proxy"] = "http://127.0.0.1:40001"
        ffmpeg_env["https_proxy"] = "http://127.0.0.1:40001"
        if os.path.exists(PROXYCHAINS_CONF):
            cmd = ["proxychains4", "-q", "-f", PROXYCHAINS_CONF] + cmd

    rotate_ffmpeg_log_if_needed()
    print(f"[StreamingEngine - DualScreen] Starting ffmpeg stdin push to TikTok: {' '.join(cmd[:12])}...")

    with stream_lock:
        err_sink = subprocess.DEVNULL
        try:
            ffmpeg_log_fp = open(FFMPEG_LOG_FILE, "a", encoding="utf-8")
            ffmpeg_log_fp.write(f"\n--- [FFmpeg Dual-Screen Stream Start: {time.strftime('%Y-%m-%d %H:%M:%S')}] ---\n")
            ffmpeg_log_fp.write(f"Command: {' '.join(cmd)}\n")
            if should_use_vpn:
                ffmpeg_log_fp.write("Network: Cloudflare WARP SOCKS5 Tunnel (127.0.0.1:40000)\n")
            ffmpeg_log_fp.flush()
            err_sink = ffmpeg_log_fp
        except Exception as log_err:
            print(f"[StreamingEngine] Warning: cannot open {FFMPEG_LOG_FILE}: {log_err}")
            err_sink = subprocess.DEVNULL

        streaming_process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=err_sink,
            env=ffmpeg_env
        )

        if err_sink and err_sink != subprocess.DEVNULL:
            try:
                err_sink.close()
            except Exception:
                pass

        stream_metadata["active"] = True
        stream_metadata["startTime"] = time.time()
        stream_metadata["rtmpUrl"] = rtmp_url
        stream_metadata["streamKey"] = stream_key[:10] + "..." if stream_key else ""
        stream_metadata["rawStreamKey"] = stream_key
        stream_metadata["roomId"] = room_id
        stream_metadata["currentSlot"] = 1
        stream_metadata["currentVideo"] = "Dual-Screen Live (Canvas + Cam)"
        dual_screen_active = True
        save_stream_metadata()
        return streaming_process

async def ws_stream_handler(websocket):
    global dual_screen_active, dual_screen_ws_client, streaming_process
    print("[DualScreen WS] Klien terhubung ke WebSocket stream pipe.")
    dual_screen_ws_client = websocket

    load_stream_metadata()
    rtmp_url = stream_metadata.get("rtmpUrl", "")
    stream_key = stream_metadata.get("rawStreamKey", "")
    room_id = stream_metadata.get("roomId", "")

    if not rtmp_url or not stream_key:
        print("[DualScreen WS] RTMP URL atau Stream Key belum tersedia.")
        try:
            await websocket.send(json.dumps({
                "status": "error",
                "message": "TikTok Live belum aktif! Silakan klik 'Start Live' terlebih dahulu."
            }))
            await websocket.close()
        except Exception:
            pass
        dual_screen_active = False
        dual_screen_ws_client = None
        return

    # Selalu nyalakan FFmpeg khusus mode dual-screen stdin pipe
    proc = start_dual_screen_ffmpeg_stream(rtmp_url=rtmp_url, stream_key=stream_key, room_id=room_id)
    if not proc or proc.poll() is not None:
        print("[DualScreen WS] Gagal memulai FFmpeg dual-screen stdin pipe!")
        try:
            await websocket.send(json.dumps({
                "status": "error",
                "message": "Gagal memulai proses FFmpeg dual-screen pada server."
            }))
            await websocket.close()
        except Exception:
            pass
        dual_screen_active = False
        dual_screen_ws_client = None
        return

    dual_screen_active = True
    print(f"[DualScreen WS] Dual-screen FFmpeg aktif (PID: {proc.pid}), siap menerima binary chunks.")
    try:
        await websocket.send(json.dumps({
            "status": "ready",
            "message": "FFmpeg stdin pipe siap menerima stream."
        }))
    except Exception:
        pass

    bytes_received = 0
    chunks_count = 0
    try:
        async for message in websocket:
            if isinstance(message, bytes):
                chunks_count += 1
                bytes_received += len(message)
                if chunks_count % 30 == 0:
                    print(f"[DualScreen WS] Chunks masuk: {chunks_count} ({bytes_received / (1024*1024):.2f} MB), pipe status: {'running' if proc.poll() is None else f'exited ({proc.poll()})'}")
                if proc.poll() is None and proc.stdin:
                    try:
                        proc.stdin.write(message)
                        proc.stdin.flush()
                    except (BrokenPipeError, OSError) as pe:
                        print(f"[DualScreen WS] Pipe write error: {pe}")
                        break
                else:
                    print(f"[DualScreen WS] FFmpeg proses berhenti mendadak (code: {proc.poll()})")
                    break
            elif isinstance(message, str):
                try:
                    payload = json.loads(message)
                    if payload.get("action") == "stop":
                        break
                except Exception:
                    pass
    except websockets.exceptions.ConnectionClosed:
        print("[DualScreen WS] Klien WebSocket terputus.")
    except Exception as e:
        print(f"[DualScreen WS] WebSocket exception: {e}")
    finally:
        dual_screen_active = False
        dual_screen_ws_client = None
        print(f"[DualScreen WS] Sesi WebSocket streaming dual-screen selesai. Total data: {bytes_received} bytes.")
        try:
            if proc and proc.poll() is None:
                if proc.stdin:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
        except Exception:
            pass

        # Kembalikan otomatis ke video standby loop (video_1.mp4) jika stream TikTok masih aktif
        load_stream_metadata()
        if stream_metadata.get("rtmpUrl") and stream_metadata.get("rawStreamKey"):
            print("[DualScreen WS] Kembali ke video standby loop (video_1.mp4)...")
            start_ffmpeg_stream(
                rtmp_url=stream_metadata.get("rtmpUrl"),
                stream_key=stream_metadata.get("rawStreamKey"),
                room_id=stream_metadata.get("roomId", ""),
                slot_num=1,
                loop=True
            )

def start_websocket_server():
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def _main():
            async with websockets.serve(ws_stream_handler, "127.0.0.1", WS_PORT, max_size=10*1024*1024):
                print(f"[DualScreen WS] WebSocket Server aktif di ws://127.0.0.1:{WS_PORT}")
                await asyncio.Future()
        try:
            loop.run_until_complete(_main())
        except Exception as e:
            print(f"[DualScreen WS] Error menjalankan WebSocket server: {e}")

    ws_thread = threading.Thread(target=_run, daemon=True, name="DualScreenWS")
    ws_thread.start()

TIKTOK_GIFT_ID_MAP = {
    "5655": "rose",
    "5827": "ice_cream",
    "5374": "doughnut",
    "5879": "cap",
    "5848": "confetti",
    "6064": "finger_heart",
    "5487": "sunglasses",
    "5755": "perfume",
    "6093": "lion",
    "6104": "whale",
    "6110": "universe",
}

def match_gift_to_slot(gift_name, gift_id_str):
    """
    Mencocokkan giftName dan giftId yang masuk dari live TikTok
    dengan konfigurasi Slot 2 s.d. Slot 6 secara fleksibel.
    Mengembalikan integer slot (2-6) atau None jika tidak cocok.
    """
    char_cfg = load_character_config()
    mappings = char_cfg.get("mappings", [])
    gift_cfg = load_gift_config()
    
    norm_name = str(gift_name).lower().replace(" ", "_").replace("-", "_").strip()
    norm_id = str(gift_id_str).strip()
    
    catalog_id_from_tiktok_id = TIKTOK_GIFT_ID_MAP.get(norm_id)
    
    catalog_id_from_name = None
    for item in GIFTS_CATALOG:
        cat_id = item["id"].lower()
        cat_name = item["name"].lower()
        if cat_id in norm_name or norm_name in cat_id:
            catalog_id_from_name = cat_id
            break
        for word in cat_name.replace("(", "").replace(")", "").replace("/", " ").split():
            if len(word) >= 3 and word in norm_name:
                catalog_id_from_name = cat_id
                break
        if catalog_id_from_name:
            break
            
    for m in mappings:
        slot = m.get("slot")
        cfg_gift = str(m.get("gift_id", "")).lower().strip()
        if not slot or slot < 2 or slot > 6:
            continue
        if cfg_gift == norm_name or cfg_gift == norm_id:
            return slot
        if catalog_id_from_tiktok_id and cfg_gift == catalog_id_from_tiktok_id:
            return slot
        if catalog_id_from_name and cfg_gift == catalog_id_from_name:
            return slot
        if cfg_gift in norm_name or norm_name in cfg_gift:
            return slot
            
    for slot_str, cfg_gift in gift_cfg.items():
        try:
            slot = int(slot_str)
        except Exception:
            continue
        if slot < 2 or slot > 6:
            continue
        cfg_gift_norm = str(cfg_gift).lower().strip()
        if cfg_gift_norm == norm_name or cfg_gift_norm == norm_id:
            return slot
        if catalog_id_from_tiktok_id and cfg_gift_norm == catalog_id_from_tiktok_id:
            return slot
        if catalog_id_from_name and cfg_gift_norm == catalog_id_from_name:
            return slot
        if cfg_gift_norm in norm_name or norm_name in cfg_gift_norm:
            return slot
            
    return None

def drain_queue_until_valid():
    """
    Mengambil item antrean berikutnya secara iteratif (non-rekursif).
    Jika video slot tidak ditemukan, terus coba item berikutnya hingga habis,
    lalu kembali ke Slot 1 (Standby).
    """
    while True:
        next_item = None
        with queue_lock:
            if len(gift_queue) > 0:
                next_item = gift_queue.popleft()
                stream_metadata["currentSlot"] = next_item.get("slot")
                next_item["status"] = "Sedang Diputar"
                for log in recent_gift_logs:
                    if log.get("id") == next_item.get("id"):
                        log["status"] = "Sedang Diputar"
                        break
            else:
                stream_metadata["currentSlot"] = 1
                next_item = None

        if next_item:
            next_slot = next_item.get("slot")
            success = play_slot(next_slot, source_info=next_item)
            if success:
                return
            else:
                print(f"[QueueManager] Slot {next_slot} gagal diputar/tidak valid, mencoba antrean berikutnya...")
                with queue_lock:
                    for log in recent_gift_logs:
                        if log.get("id") == next_item.get("id"):
                            log["status"] = "Gagal (Video Hilang)"
                            break
        else:
            play_slot(1)
            return

def play_slot(slot_num, source_info=None):
    """
    Memutar video slot tertentu (1 = Standby Loop, 2-6 = Reaksi).
    Jika slot reaksi (2-6), pasang timer otomatis berbasis durasi video asli
    untuk memeriksa antrean gift_queue setelah selesai.
    """
    global stream_metadata
    with stream_lock:
        if not stream_metadata.get("rtmpUrl") or not stream_metadata.get("rawStreamKey"):
            print(f"[StreamingEngine] Stream RTMP belum aktif, play_slot({slot_num}) dibatalkan.")
            return False

    slot_video_path = None
    for ext in ("webm", "mp4"):
        cand = os.path.join(VIDEOS_DIR, f"video_{slot_num}.{ext}")
        if os.path.exists(cand):
            slot_video_path = cand
            break
            
    if not slot_video_path:
        if slot_num == 1:
            slot_video_path = os.path.join(BASE_DIR, "media", "standby-loop.mp4")
        else:
            print(f"[StreamingEngine] Video untuk Slot {slot_num} tidak ditemukan!")
            return False

    is_standby = (slot_num == 1)
    
    if is_dual_screen_active():
        print(f"[DualScreen] switch_to_video_slot({slot_num}) dipanggil dalam mode Dual-Screen. Mengubah currentSlot tanpa restart FFmpeg.")
        stream_metadata["currentSlot"] = slot_num
        stream_metadata["currentVideo"] = os.path.basename(slot_video_path) if slot_video_path else f"Slot {slot_num}"
        save_stream_metadata()
        if not is_standby:
            duration = get_video_duration(slot_video_path)
            print(f"[DualScreen Queue] Memutar Slot {slot_num} ({os.path.basename(slot_video_path)}), durasi: {duration:.2f}s")
            def _slot_timer(expected_slot):
                sleep_time = max(1.0, duration - 0.5)
                time.sleep(sleep_time)
                on_slot_finished(expected_slot)
            threading.Thread(target=_slot_timer, args=(slot_num,), daemon=True).start()
        return True

    try:
        start_ffmpeg_stream(
            rtmp_url=stream_metadata.get("rtmpUrl"),
            stream_key=stream_metadata.get("rawStreamKey"),
            room_id=stream_metadata.get("roomId", ""),
            video_override=slot_video_path,
            loop=is_standby,
            slot_num=slot_num
        )
    except Exception as e:
        print(f"[StreamingEngine] Error saat beralih ke slot {slot_num}: {e}")
        return False

    if not is_standby:
        duration = get_video_duration(slot_video_path)
        print(f"[QueueManager] Memutar Slot {slot_num} ({os.path.basename(slot_video_path)}), durasi: {duration:.2f}s")
        
        def _slot_timer(expected_slot):
            sleep_time = max(1.0, duration - 0.5)
            time.sleep(sleep_time)
            on_slot_finished(expected_slot)

        threading.Thread(target=_slot_timer, args=(slot_num,), daemon=True).start()

    return True

def on_slot_finished(finished_slot):
    """
    Dipanggil saat durasi video reaksi (Slot 2-6) telah selesai diputar.
    Mengecek antrean gift_queue:
    - Jika antrean masih ada isinya, putar slot berikutnya dari antrean.
    - Jika antrean sudah kosong, barulah kembali ke Slot 1 (Standby Loop).
    """
    global gift_queue, recent_gift_logs
    next_item = None
    with queue_lock:
        if stream_metadata.get("currentSlot") != finished_slot:
            return

        for log in recent_gift_logs:
            if log.get("slot") == finished_slot and log.get("status") == "Sedang Diputar":
                log["status"] = "Selesai"

        if len(gift_queue) > 0:
            next_item = gift_queue.popleft()
            stream_metadata["currentSlot"] = next_item.get("slot")
            next_item["status"] = "Sedang Diputar"
            for log in recent_gift_logs:
                if log.get("id") == next_item.get("id"):
                    log["status"] = "Sedang Diputar"
                    break
        else:
            # Queue kosong! Set currentSlot = 1 langsung di dalam lock
            stream_metadata["currentSlot"] = 1
            next_item = None

    if next_item:
        next_slot = next_item.get("slot")
        print(f"[QueueManager] Slot {finished_slot} selesai. Memutar item berikutnya dari antrean: Slot {next_slot} (Gift: {next_item.get('giftName')}). Sisa antrean: {len(gift_queue)}")
        success = play_slot(next_slot, source_info=next_item)
        if not success:
            drain_queue_until_valid()
    else:
        print(f"[QueueManager] Slot {finished_slot} selesai. Antrean kosong, kembali ke Slot 1 (Standby Loop)...")
        play_slot(1)

def handle_incoming_gift(gift_name, gift_id, sender_name, repeat_count=1):
    """
    Menangani gift yang masuk dari penonton (live event ataupun simulasi).
    Sistem antrean aman:
    - Tidak memotong video reaksi yang sedang berjalan di tengah jalan.
    - Jika Slot 2 sedang berjalan dan penonton mengirim gift pemicu Slot 3, masukkan Slot 3 ke antrean.
    - Jika sedang Standby (Slot 1), langsung putar slot reaksi.
    """
    global gift_queue, recent_gift_logs, global_gift_seq, latest_gift_event
    
    target_slot = match_gift_to_slot(gift_name, gift_id)
    if not target_slot:
        print(f"[GiftListener] Gift diterima: '{gift_name}' (ID: {gift_id}) dari {sender_name}, tidak ada mapping Slot 2-6.")
        return {"matched": False, "reason": "No slot mapping found"}

    global_gift_seq += 1
    event_id = f"{int(time.time()*1000)}_{target_slot}_{global_gift_seq}"
    gift_info = {
        "id": event_id,
        "seq": global_gift_seq,
        "time": time.strftime("%H:%M:%S"),
        "sender": sender_name or "Penonton",
        "giftName": gift_name,
        "giftId": str(gift_id),
        "count": repeat_count,
        "slot": target_slot,
        "slotName": f"Slot {target_slot}",
        "status": "Dalam Antrean"
    }
    latest_gift_event = gift_info

    should_play_slot = None
    is_reaction_playing = False

    with queue_lock:
        current_slot = stream_metadata.get("currentSlot", 1)
        is_reaction_playing = (current_slot != 1)

        if not is_reaction_playing:
            # Standby! Set state currentSlot langsung ke target_slot secara atomic
            stream_metadata["currentSlot"] = target_slot
            gift_info["status"] = "Sedang Diputar"
            recent_gift_logs.appendleft(gift_info)
            should_play_slot = target_slot
            print(f"[GiftListener] Standby -> Langsung memicu Slot {target_slot} (Gift: '{gift_name}' x{repeat_count} dari {sender_name})")
        else:
            # Reaksi sedang aktif! Cek kapasitas antrean (maks 15)
            if len(gift_queue) >= 15:
                print(f"[GiftListener] Antrean penuh (15 item). Gift '{gift_name}' dari {sender_name} diabaikan.")
                return {"matched": True, "slot": target_slot, "queued": False, "queue_length": 15, "dropped": True}

            gift_info["status"] = "Dalam Antrean"
            recent_gift_logs.appendleft(gift_info)
            gift_queue.append(gift_info)
            print(f"[GiftListener] Slot {current_slot} sedang berjalan. Memasukkan Slot {target_slot} ke antrean. (Antrean: {len(gift_queue)})")

    # Jalankan pemutaran video DI LUAR queue_lock agar tidak menahan request HTTP selama time.sleep()
    if should_play_slot:
        success = play_slot(should_play_slot, source_info=gift_info)
        if not success:
            drain_queue_until_valid()

    return {
        "matched": True,
        "slot": target_slot,
        "queued": is_reaction_playing,
        "queue_length": len(gift_queue)
    }

def switch_to_video_slot(slot_num, auto_return=True):
    """
    Fungsi kompatibilitas untuk beralih slot video.
    Slot 1 mereset antrean dan kembali ke standby.
    Slot 2-6 memutar reaksi dengan antrean aman.
    """
    if slot_num == 1:
        with queue_lock:
            gift_queue.clear()
            for log in recent_gift_logs:
                if log.get("status") == "Dalam Antrean":
                    log["status"] = "Dibatalkan"
            stream_metadata["currentSlot"] = 1
        play_slot(1)
    else:
        char_cfg = load_character_config()
        mappings = char_cfg.get("mappings", [])
        cfg_gift = "rose"
        for m in mappings:
            if m.get("slot") == slot_num:
                cfg_gift = m.get("gift_id", "rose")
                break
        handle_incoming_gift(cfg_gift, "", "ManualDashboard", 1)

def ensure_live_listener(force_restart=False):
    """
    Memastikan proses Node.js live_listener.js berjalan stabil di latar belakang
    dan terhubung ke TikTok Live room (@indopolyglot19).
    """
    global listener_process, listener_status
    with watchdog_lock:
        if watchdog_status.get("is_reconnecting") or watchdog_status.get("emergency_shutdown"):
            return

    vpn_cfg = load_vpn_config()
    auto_vpn_required = vpn_cfg.get("auto_vpn", True)
    wp_running = is_wireproxy_running()

    # Anti-leak check: jika auto_vpn aktif namun wireproxy belum jalan, tahan listener
    if auto_vpn_required and not wp_running:
        return

    if force_restart:
        try:
            if listener_process and listener_process.poll() is None:
                listener_process.terminate()
                listener_process.wait(timeout=2)
        except Exception:
            pass
        try:
            subprocess.run(["pkill", "-f", "node.*live_listener.js"], timeout=2)
        except Exception:
            pass
        listener_process = None

    if listener_process is None or listener_process.poll() is not None:
        try:
            out = subprocess.check_output(["pgrep", "-f", "node.*live_listener.js"], text=True).strip()
            if out and not force_restart:
                return
        except Exception:
            pass
            
        print("[LiveListenerManager] Memulai node live_listener.js background daemon...")
        listener_file = os.path.join(BASE_DIR, "live_listener.js")
        if os.path.exists(listener_file):
            try:
                log_f = open(LIVE_LISTENER_LOG_FILE, "a", encoding="utf-8")
                should_use_vpn = auto_vpn_required and wp_running
                listener_env = os.environ.copy()
                if should_use_vpn:
                    listener_env["ALL_PROXY"] = "socks5h://127.0.0.1:40000"
                    listener_env["TIKTOK_PROXY"] = "socks5h://127.0.0.1:40000"
                    listener_env["HTTP_PROXY"] = "http://127.0.0.1:40001"
                    listener_env["HTTPS_PROXY"] = "http://127.0.0.1:40001"
                    listener_env["AUTO_VPN"] = "1"
                    listener_env["REQUIRE_PROXY"] = "1"
                listener_process = subprocess.Popen(
                    ["node", listener_file],
                    cwd=BASE_DIR,
                    stdout=log_f,
                    stderr=log_f,
                    env=listener_env
                )
            except Exception as e:
                print(f"[LiveListenerManager] Gagal memulai listener: {e}")

def cleanup_all_processes():
    global listener_process, streaming_process
    print("[Shutdown] Membersihkan proses background...")
    if listener_process and listener_process.poll() is None:
        try:
            listener_process.terminate()
            listener_process.wait(timeout=2)
        except Exception:
            try:
                listener_process.kill()
            except Exception:
                pass
    if streaming_process and streaming_process.poll() is None:
        try:
            streaming_process.terminate()
            streaming_process.wait(timeout=2)
        except Exception:
            try:
                streaming_process.kill()
            except Exception:
                pass

atexit.register(cleanup_all_processes)

def _sig_handler(signum, frame):
    cleanup_all_processes()
    sys.exit(0)

try:
    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)
except Exception:
    pass

def start_supervisor():
    def _loop():
        while True:
            try:
                ensure_live_listener()
            except Exception:
                pass
            time.sleep(4)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()

start_supervisor()
start_vpn_watchdog()

if load_vpn_config().get("auto_vpn", True):
    try:
        start_wireproxy_vpn()
    except Exception as e:
        print(f"[Startup] Error launching Wireproxy: {e}")

def refresh_active_stream_overlay():
    """
    Jika status live stream sedang AKTIF, restart subprocess FFmpeg lokal
    pada slot yang sedang aktif menggunakan kredensial RTMP di stream_metadata
    sehingga posisi/tampilan overlay langsung ter-update di TikTok Live
    tanpa menutup room penonton.
    """
    global stream_metadata
    with stream_lock:
        is_live = get_ffmpeg_pid() is not None
        if not is_live:
            return
        current_slot = stream_metadata.get("currentSlot", 1)
        rtmp_url = stream_metadata.get("rtmpUrl", "")
        stream_key = stream_metadata.get("rawStreamKey", "")
        room_id = stream_metadata.get("roomId", "")

    if rtmp_url and stream_key:
        print(f"[StreamingEngine] Refreshing active FFmpeg stream for on-the-fly overlay update (Slot {current_slot})...")
        is_standby = (current_slot == 1)
        try:
            start_ffmpeg_stream(
                rtmp_url=rtmp_url,
                stream_key=stream_key,
                room_id=room_id,
                slot_num=current_slot,
                loop=is_standby
            )
        except Exception as e:
            print(f"[StreamingEngine] Error refreshing stream overlay: {e}")

def fetch_tiktok_stream_key():
    if not os.path.exists(COOKIES_FILE):
        raise Exception("cookies.json belum tersedia. Silakan isi konfigurasi akun dan cookies terlebih dahulu.")

    with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
        cookies = json.load(f)
    
    cookie_header = "; ".join([f"{c.get('name')}={c.get('value')}" for c in cookies if c.get('name') and c.get('value')])

    url = 'https://webcast.tiktok.com/webcast/room/create/?aid=1988&app_name=tiktok_web'
    headers = {
        'Cookie': cookie_header,
        'Referer': 'https://livecenter.tiktok.com/producer',
        'Origin': 'https://livecenter.tiktok.com',
        'Content-Type': 'application/json'
    }

    res = tiktok_request_wrapper(url, method='POST', data=b'{}', headers=headers, timeout=12)
    resp_body = res.get("body", "{}")
    try:
        json_data = json.loads(resp_body)
    except Exception as e:
        raise Exception(f"Gagal mem-parsing respons JSON dari TikTok API: {e} | Raw: {resp_body[:200]}")

    # Catat metadata IP kunci
    try:
        acc_cfg = load_account_config()
        acc_cfg["last_verified_ip"] = res.get("egress_ip", "")
        acc_cfg["last_verified_via"] = res.get("via", "WARP_PROXY")
        acc_cfg["last_verified_time"] = time.time()
        acc_cfg["last_verified_str"] = time.strftime("%Y-%m-%d %H:%M:%S WIB", time.localtime())
        save_account_config(acc_cfg)
    except Exception:
        pass

    stream_url_obj = json_data.get('data', {}).get('stream_url', {})
    rtmp_push_url = stream_url_obj.get('rtmp_push_url', '')
    
    if not rtmp_push_url:
        raise Exception(f"TikTok API mengembalikan respons tanpa RTMP push URL. Respons: {json.dumps(json_data)}")

    last_slash = rtmp_push_url.rfind('/')
    rtmp_url = rtmp_push_url[:last_slash]
    stream_key = rtmp_push_url[last_slash + 1:]
    room_id = json_data.get('data', {}).get('id_str') or str(json_data.get('data', {}).get('id', ''))

    return rtmp_url, stream_key, json_data, room_id

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def check_auth(self):
        client_ip = self.client_address[0]
        if client_ip in ["127.0.0.1", "::1", "localhost"]:
            return True
            
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            c = http.cookies.SimpleCookie(cookie_header)
            if "auth_token" in c and c["auth_token"].value == "verified_191919":
                return True
        return False

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range, Authorization")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET(head_only=True)

    def do_GET(self, head_only=False):
        parsed_url = urllib.parse.urlparse(self.path)
        
        if parsed_url.path == '/logout':
            self.send_response(303)
            self.send_header('Set-Cookie', 'auth_token=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT')
            self.send_header('Location', '/')
            self.end_headers()
            return

        if not self.check_auth() and parsed_url.path not in ['/login', '/health', '/api/listener-status']:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            login_html = """<!DOCTYPE html>
            <html>
                <head>
                    <meta charset="utf-8">
                    <title>Login - TikTok Live Dashboard</title>
                    <style>
                        @keyframes shine {
                            0% { background-position: 0% 0%; }
                            50% { background-position: 100% 100%; }
                            100% { background-position: 0% 0%; }
                        }
                        @keyframes spin-around {
                            0% { transform: translateZ(0) rotate(0deg); }
                            100% { transform: translateZ(0) rotate(360deg); }
                        }
                        @keyframes floating {
                            0%, 100% { transform: translateY(0px); }
                            50% { transform: translateY(-6px); }
                        }
                        .shine-border-card {
                            position: relative;
                            border-radius: 16px;
                            background: #121216;
                            padding: 30px;
                            border: 1px solid #27272a;
                            overflow: hidden;
                            animation: floating 4s ease-in-out infinite;
                            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.6), 0 0 20px rgba(160, 124, 254, 0.15);
                        }
                        .shine-border-card::before {
                            content: "";
                            position: absolute;
                            inset: 0;
                            border-radius: inherit;
                            padding: 2px;
                            background-image: radial-gradient(transparent, transparent, #A07CFE, #FE8FB5, #FFBE7B, transparent, transparent);
                            background-size: 300% 300%;
                            -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
                            -webkit-mask-composite: xor;
                            mask-composite: exclude;
                            animation: shine 14s infinite linear;
                            pointer-events: none;
                        }
                        .shimmer-btn {
                            --spread: 90deg;
                            --shimmer-color: #ffffff;
                            --radius: 100px;
                            --speed: 3s;
                            --cut: 1.5px;
                            --bg: rgba(15, 15, 20, 0.95);
                            position: relative;
                            z-index: 0;
                            display: inline-flex;
                            align-items: center;
                            justify-content: center;
                            overflow: hidden;
                            white-space: nowrap;
                            cursor: pointer;
                            border: 1px solid rgba(255, 255, 255, 0.12);
                            border-radius: var(--radius);
                            background: var(--bg);
                            color: #ffffff;
                            padding: 12px 24px;
                            font-weight: 600;
                            font-size: 14px;
                            transform: translateZ(0);
                            transition: transform 0.3s ease-in-out, box-shadow 0.3s ease-in-out;
                            box-shadow: inset 0 -8px 10px #ffffff1f;
                            width: 100%;
                            text-decoration: none;
                        }
                        .shimmer-btn:hover {
                            box-shadow: inset 0 -6px 10px #ffffff3f;
                        }
                        .shimmer-btn::before {
                            content: "";
                            position: absolute;
                            inset: calc(var(--cut) * -1);
                            z-index: -1;
                            border-radius: inherit;
                            background: conic-gradient(from calc(270deg - (var(--spread) / 2)), transparent 0, var(--shimmer-color) var(--spread), transparent var(--spread));
                            animation: spin-around var(--speed) infinite linear;
                        }
                        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f0f11; color: #f1f1f3; margin: 0; display: flex; align-items: center; justify-content: center; height: 100vh; }
                        .login-container { width: 100%; max-width: 380px; padding: 20px; box-sizing: border-box; }
                        input[type="password"] { width: 100%; background: #0f0f11; color: #25f4ee; border: 1px solid #3f3f46; padding: 12px; border-radius: 8px; font-size: 16px; box-sizing: border-box; margin-bottom: 20px; text-align: center; letter-spacing: 2px; }
                        h2 { color: #fff; text-align: center; margin-top: 0; margin-bottom: 20px; }
                    </style>
                </head>
                <body>
                    <div class="login-container">
                        <div class="shine-border-card">
                            <h2>TikTok Live Dashboard</h2>
                            <p style="color: #a1a1aa; font-size: 13px; text-align: center; margin-bottom: 20px;">Masukkan password akses sistem (191919)</p>
                            <form action="/login" method="POST">
                                <input type="password" name="password" placeholder="••••••" required autofocus />
                                <button type="submit" class="shimmer-btn" style="--shimmer-color: #25f4ee; --bg: rgba(37, 244, 238, 0.15);">Masuk Dashboard</button>
                            </form>
                        </div>
                    </div>
                </body>
            </html>
            """
            self.wfile.write(login_html.encode('utf-8'))
            return

        if parsed_url.path == '/' or parsed_url.path == '/index.html':
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            
            account_cfg = load_account_config()
            tiktok_username = account_cfg.get("username", "").strip()
            cookies_exist = os.path.exists(COOKIES_FILE)
            last_ip = account_cfg.get("last_verified_ip", "")
            last_via = account_cfg.get("last_verified_via", "")
            last_time = account_cfg.get("last_verified_str", "")
            session_valid = account_cfg.get("session_valid", False)
            
            if tiktok_username:
                if session_valid and last_ip:
                    ip_lock_badge = f"<span style='background:rgba(37,244,238,0.18); border:1px solid #25f4ee; color:#25f4ee; padding:2px 10px; border-radius:12px; font-size:11px; margin-left:10px;'>🛡️ Session Route: {last_via.replace('_', ' ')} ({last_ip}) - IP Locked</span>"
                elif last_ip:
                    ip_lock_badge = f"<span style='background:rgba(255,184,0,0.18); border:1px solid #ffb800; color:#ffb800; padding:2px 10px; border-radius:12px; font-size:11px; margin-left:10px;'>🟡 Session Route: {last_via} ({last_ip})</span>"
                else:
                    ip_lock_badge = "<span style='background:rgba(113,113,122,0.18); border:1px solid #71717a; color:#a1a1aa; padding:2px 10px; border-radius:12px; font-size:11px; margin-left:10px;'>⚪ Belum Diverifikasi</span>"

                account_status_banner = f"""<div style='background: rgba(52, 199, 89, 0.15); border: 1px solid #34c759; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; color: #34c759; font-weight: bold; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;'>
                    <div>✅ Akun Terhubung: @{tiktok_username.lstrip('@')} {ip_lock_badge}</div>
                    <div style='font-size:12px; font-weight:normal; color:#aaa;'>Verifikasi Terakhir: {last_time or '-'}</div>
                </div>"""
            else:
                account_status_banner = f"<div style='background: rgba(255, 59, 48, 0.15); border: 1px solid #ff3b30; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; color: #ff3b30; font-weight: bold;'>⚠️ Akun TikTok Belum Dikonfigurasi — Silakan isi konfigurasi akun di bawah sebelum memulai Live</div>"

            is_live = get_ffmpeg_pid() is not None
            live_color = "#34c759" if is_live else "#ff3b30"
            live_text = "AKTIF & STREAMING (Lightweight FFmpeg)" if is_live else "OFFLINE"
            
            is_listener_conn = listener_status.get("connected", False)
            if is_listener_conn:
                listener_color = "#34c759"
                listener_text = f"AKTIF (@{listener_status.get('username', tiktok_username.lstrip('@'))})"
                listener_badge_bg = "rgba(52, 199, 89, 0.15)"
                listener_badge_text = f"🟢 Terhubung ke Room {listener_status.get('roomId', '-')}"
            elif is_live:
                listener_color = "#ffb800"
                listener_text = "MENGHUBUNGKAN..."
                listener_badge_bg = "rgba(255, 184, 0, 0.15)"
                listener_badge_text = "🟡 Menghubungkan ke Room Live..."
            else:
                listener_color = "#71717a"
                listener_text = "STANDBY"
                listener_badge_bg = "rgba(113, 113, 122, 0.15)"
                listener_badge_text = "⚪ Standby (Offline)"

            q_len = len(gift_queue)

            # Watchdog & VPN status info
            egress_ip, is_warp, is_wp_running = get_current_egress_info()
            vpn_cfg = load_vpn_config()
            auto_vpn_on = vpn_cfg.get("auto_vpn", True)
            
            with watchdog_lock:
                wd_state = watchdog_status.get("state", "IDLE")
                reconnects = watchdog_status.get("reconnect_count", 0)
                last_chk = watchdog_status.get("last_check_time")

            sec_ago = max(0, int(time.time() - last_chk)) if last_chk else 0
            last_chk_str = f"{sec_ago}d lalu" if last_chk else "-"

            if wd_state == "PROTECTED":
                wd_badge_text = "PROTECTED (Kill-Switch Ready)"
                wd_badge_color = "#34c759"
                wd_badge_bg = "rgba(52, 199, 89, 0.15)"
                wd_dot_shadow = "0 0 10px #34c759"
            elif wd_state == "RECONNECTING":
                wd_badge_text = "RECONNECTING... (Kill-Switch Active)"
                wd_badge_color = "#ff9500"
                wd_badge_bg = "rgba(255, 149, 0, 0.2)"
                wd_dot_shadow = "0 0 12px #ff9500"
            elif wd_state == "EMERGENCY_STOPPED":
                wd_badge_text = "EMERGENCY STOPPED (VPN DOWN)"
                wd_badge_color = "#ff3b30"
                wd_badge_bg = "rgba(255, 59, 48, 0.2)"
                wd_dot_shadow = "0 0 12px #ff3b30"
            else:
                wd_badge_text = "IDLE (Offline)"
                wd_badge_color = "#71717a"
                wd_badge_bg = "rgba(113, 113, 122, 0.15)"
                wd_dot_shadow = "none"

            if is_wp_running and is_warp:
                vpn_badge_color = "#25f4ee"
                vpn_badge_text = "VPN ACTIVE (WARP)"
                vpn_dot_shadow = "0 0 10px #25f4ee"
            elif is_wp_running:
                vpn_badge_color = "#ffb800"
                vpn_badge_text = "VPN CONNECTING..."
                vpn_dot_shadow = "0 0 8px #ffb800"
            else:
                vpn_badge_color = "#71717a"
                vpn_badge_text = "DIRECT (OFF)"
                vpn_dot_shadow = "none"

            live_status_html = f"""
            <div class="floating-bar">
                <div style="display: flex; align-items: center; gap: 16px; flex-wrap: wrap;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div style="width: 10px; height: 10px; border-radius: 50%; background: {live_color}; box-shadow: 0 0 8px {live_color};"></div>
                        <span style="font-weight: bold; color: #fff;">Live: <span style="color: {live_color};">{live_text}</span></span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div style="width: 10px; height: 10px; border-radius: 50%; background: {listener_color}; box-shadow: 0 0 8px {listener_color};"></div>
                        <span style="font-size: 13px; color: #aaa;">Listener: <span style="color: {listener_color}; font-weight: bold;">{listener_text}</span></span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div id="watchdogDot" style="width: 10px; height: 10px; border-radius: 50%; background: {wd_badge_color}; box-shadow: {wd_dot_shadow};"></div>
                        <span style="font-size: 13px; color: #aaa;">Watchdog: <span id="watchdogBadge" style="color: {wd_badge_color}; font-weight: bold;">{wd_badge_text}</span> <span id="watchdogMeta" style="color: #888; font-size:11px;">(Recon: {reconnects} | Cek: {last_chk_str})</span></span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div id="vpnFloatingDot" style="width: 10px; height: 10px; border-radius: 50%; background: {vpn_badge_color}; box-shadow: {vpn_dot_shadow};"></div>
                        <span style="font-size: 13px; color: #aaa;">VPN: <span id="vpnFloatingBadge" style="color: {vpn_badge_color}; font-weight: bold;">{vpn_badge_text}</span> <span id="vpnFloatingMeta" style="color: #888; font-size:11px;">({egress_ip})</span></span>
                    </div>
                    <div style="background: rgba(37, 244, 238, 0.12); border: 1px solid #25f4ee; padding: 2px 10px; border-radius: 12px; font-size: 12px; color: #25f4ee; font-weight: bold;">
                        Antrean: <span id="queueBadge">{q_len}</span>
                    </div>
                </div>
                <div>
                    {f"<span style='color:#34c759; font-size:12px; margin-right:12px;'>Aktif: Slot {stream_metadata.get('currentSlot', 1)} ({stream_metadata.get('currentVideo', '')})</span><span style='color:#aaa; font-size:12px; margin-right:15px;'>RTMP: {stream_metadata.get('rtmpUrl', '')}</span>" if is_live else ""}
                    <a href="/logout" class="shimmer-btn" style="--shimmer-color: #ff3b30; --bg: rgba(255, 59, 48, 0.15); padding: 6px 14px; font-size: 12px;">Logout</a>
                </div>
            </div>
            """

            with queue_lock:
                logs_copy = list(recent_gift_logs)[:5]

            if not logs_copy:
                gift_logs_table_html = """
                <div id="emptyGiftMsg" style="text-align: center; padding: 18px; background: #121214; border-radius: 8px; border: 1px solid #27272a; color: #71717a; font-size: 13px;">
                    Belum ada gift yang masuk. Begitu penonton mengirim gift di room live TikTok, event dan antrean reaksi akan langsung tercatat di sini secara real-time.
                </div>
                """
            else:
                table_body = ""
                for g in logs_copy:
                    st = g.get("status", "")
                    if st == "Sedang Diputar":
                        badge = '<span style="background:rgba(37,244,238,0.18); color:#25f4ee; border:1px solid #25f4ee; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:bold;">▶️ Sedang Diputar</span>'
                    elif st == "Dalam Antrean":
                        badge = '<span style="background:rgba(255,184,0,0.18); color:#ffb800; border:1px solid #ffb800; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:bold;">⏳ Dalam Antrean</span>'
                    else:
                        badge = '<span style="background:rgba(52,199,89,0.18); color:#34c759; border:1px solid #34c759; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:bold;">✅ Selesai</span>'
                        
                    table_body += f"""
                    <tr style="border-bottom: 1px solid #27272a;">
                        <td style="padding: 10px 12px; color: #aaa; font-size: 12px;">{g.get('time')}</td>
                        <td style="padding: 10px 12px; color: #fff; font-weight: bold; font-size: 13px;">@{g.get('sender')}</td>
                        <td style="padding: 10px 12px; color: #25f4ee; font-size: 13px;"><b>{g.get('giftName')}</b> <span style="color:#888; font-size:11px;">(x{g.get('count', 1)})</span></td>
                        <td style="padding: 10px 12px; color: #ffb800; font-weight: 600; font-size: 13px;">Slot {g.get('slot')}</td>
                        <td style="padding: 10px 12px;">{badge}</td>
                    </tr>
                    """
                gift_logs_table_html = f"""
                <table style="width: 100%; border-collapse: collapse; background: #121214; border-radius: 8px; overflow: hidden; border: 1px solid #27272a; text-align: left;">
                    <thead>
                        <tr style="background: #1c1c1e; color: #888; font-size: 12px; border-bottom: 1px solid #333;">
                            <th style="padding: 8px 12px;">WAKTU</th>
                            <th style="padding: 8px 12px;">PENONTON</th>
                            <th style="padding: 8px 12px;">GIFT</th>
                            <th style="padding: 8px 12px;">SLOT TERPICU</th>
                            <th style="padding: 8px 12px;">STATUS</th>
                        </tr>
                    </thead>
                    <tbody id="giftLogsTableBody">
                        {table_body}
                    </tbody>
                </table>
                """

            live_listener_card_html = f"""
            <div class="shine-border-card" style="animation-delay: 0.3s; margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 10px;">
                    <div>
                        <h3 style="margin: 0; display: flex; align-items: center; gap: 10px;">
                            <span>📡 Live Gift Listener & Reaction Queue</span>
                            <span style="background: {listener_badge_bg}; color: {listener_color}; font-size: 11px; padding: 3px 10px; border-radius: 12px; border: 1px solid {listener_color}; font-weight: normal;">{listener_badge_text}</span>
                        </h3>
                        <p style="color: #a1a1aa; font-size: 12px; margin: 4px 0 0 0;">
                            Memantau room live <b>@{tiktok_username.lstrip('@')}</b>. Menangkap gift penonton dan memicu antrean video reaksi secara tertib tanpa memotong video yang sedang diputar.
                        </p>
                    </div>
                    <div style="text-align: right; background: #121214; padding: 8px 14px; border-radius: 8px; border: 1px solid #27272a;">
                        <span style="color: #888; font-size: 12px;">Status Antrean: </span>
                        <b style="color: #25f4ee; font-size: 14px;"><span id="queueCountVal">{q_len}</span> Gift</b>
                    </div>
                </div>

                <div style="margin-top: 10px;">
                    <div style="font-size: 13px; font-weight: bold; color: #fff; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center;">
                        <span>📋 Riwayat 5 Gift Terakhir Penonton</span>
                        <span style="font-size: 11px; color: #34c759;">⚡ Auto-Sync Real-time</span>
                    </div>
                    <div id="giftLogsContainer">
                        {gift_logs_table_html}
                    </div>
                </div>
            </div>
            """

            gift_config = load_gift_config()
            video_meta = load_video_meta()
            video_slots_html = ""
            
            for i in range(1, 7):
                # ponytail: first-found wins; upgrade when mixed webm+mp4 per slot matters
                existing_file = None
                for ext in ("webm", "mp4"):
                    candidate = os.path.join(VIDEOS_DIR, f"video_{i}.{ext}")
                    if os.path.exists(candidate):
                        existing_file = f"video_{i}.{ext}"
                        break
                
                slot_desc = "Video Utama / Standby Loop" if i == 1 else f"Video Reaksi Pemicu Gift (Slot {i})"
                
                file_status_ui = ""
                preview_html = ""
                if existing_file:
                    size_mb = round(os.path.getsize(os.path.join(VIDEOS_DIR, existing_file)) / (1024*1024), 2)
                    slot_meta = video_meta.get(str(i), {})
                    orig_name = slot_meta.get("original_name")
                    display_name = orig_name if orig_name else existing_file
                    v_mime = "video/mp4" if existing_file.lower().endswith(".mp4") else "video/webm"
                    
                    file_status_ui = f"""
                    <span style='color:#34c759; margin-right:10px;'>📁 <b>{display_name}</b> <span style='color:#888; font-size:12px;'>({size_mb} MB)</span></span>
                    <button type="button" class="modern-btn" style="padding:5px 12px; font-size:11px; margin-right:8px; background:#27272a; color:#25f4ee; border:1px solid #3f3f46; cursor:pointer;" onclick="editVideoName('{i}', '{display_name}')">✏️ Edit Nama</button>
                    <a href='/delete-video?name={existing_file}&slot={i}' class='shimmer-btn' style='background:rgba(255, 59, 48, 0.2); border-color:#ff3b30; color:#ff3b30; padding:6px 14px; font-size:12px; text-decoration:none;'>Hapus</a>
                    """
                    
                    preview_html = f"""
                    <div style='margin-top:10px; background:#121214; padding:10px 14px; border-radius:8px; border:1px solid #27272a; display:flex; gap:16px; align-items:center;'>
                        <video controls preload="metadata" style="max-height:130px; width:auto; max-width:220px; border-radius:6px; background:#000; border:1px solid #3f3f46;">
                            <source src="/video-stream?name={existing_file}" type="{v_mime}">
                            Browser Anda tidak mendukung preview video.
                        </video>
                        <div style="font-size:12px; color:#a1a1aa; line-height:1.6;">
                            <div style="color:#fff; font-weight:bold; font-size:13px; margin-bottom:4px;">🎬 Video Preview Ready</div>
                            <div>Nama File: <span style="color:#25f4ee; font-weight:600;">{display_name}</span></div>
                            <div>Ukuran File: <span style="color:#34c759;">{size_mb} MB</span></div>
                            <div style="color:#888; font-size:11px; margin-top:4px;">Klik tombol play untuk cek video & audio.</div>
                        </div>
                    </div>
                    """
                else:
                    file_status_ui = f"""
                    <form action="/upload-video" method="POST" enctype="multipart/form-data" style='display:flex; align-items:center; gap:10px; margin:0;' onsubmit="return validateUpload(this);">
                        <input type="hidden" name="slot" value="{i}" />
                        <span style="color:#a1a1aa; font-size:12px;">Format .webm / .mp4</span>
                        <input type="file" name="videofile" accept="video/webm,video/mp4,.webm,.mp4" style="color:#aaa; font-size:12px;" />
                        <button type="submit" class="modern-btn btn-upload" style="padding:6px 14px; font-size:12px;">Upload</button>
                    </form>
                    """

                video_slots_html += f"""
                <div style='background:#1c1c1e; padding:12px 15px; margin:10px 0; border-radius:8px; border:1px solid #333;'>
                    <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;'>
                        <div>
                            <span style='font-size:15px; font-weight:bold; color:#fff;'>Slot {i}</span>
                            <span style='font-size:12px; color:#888; margin-left:10px;'>({slot_desc})</span>
                        </div>
                        <div style="display:flex; align-items:center;">{file_status_ui}</div>
                    </div>
                    {preview_html}
                """
                
                if i >= 2:
                    current_gift = gift_config.get(str(i), "rose")
                    video_slots_html += f"""
                    <div style='margin-top:10px; border-top:1px solid #27272a; padding-top:10px; display:flex; justify-content:space-between; align-items:center;'>
                        <span style='font-size:13px; color:#25f4ee;'>Konfigurasi Pemicu Gift (Slot {i}) — Terpilih: <b>{current_gift}</b></span>
                        <div style='display:flex; gap:8px;'>
                            <button type="button" class="modern-btn" style="padding: 6px 12px; font-size: 12px; background: rgba(52, 199, 89, 0.15); border: 1px solid #34c759; color: #34c759; cursor:pointer;" onclick="simulateGift(event, {i})">▶ Uji Slot {i}</button>
                            <button type="button" class="modern-btn btn-gift" style="padding: 6px 14px; font-size: 12px;" onclick="openGiftModal({i})">Pilih Gift</button>
                        </div>
                    </div>
                    """
                video_slots_html += "</div>"

            simulation_buttons_html = ""
            for s in range(2, 7):
                cfg_gift_id = gift_config.get(str(s), "rose")
                gift_title = cfg_gift_id.replace("_", " ").title()
                gift_icon = "🎁"
                for g in GIFTS_CATALOG:
                    if g["id"].lower() == cfg_gift_id.lower():
                        gift_title = g["name"]
                        gift_icon = g.get("icon", "🎁")
                        break
                simulation_buttons_html += f"""
                <button type="button" id="btn-sim-slot-{s}" class="shimmer-btn" style="--shimmer-color: #34c759; --bg: rgba(52, 199, 89, 0.12); padding: 9px 18px; font-size: 13px; font-weight: 600; display: inline-flex; align-items: center; gap: 7px; border-radius: 8px; border: 1px solid rgba(52, 199, 89, 0.35); cursor: pointer; transition: all 0.2s ease;" onclick="simulateGift(event, {s})">
                    <span style="font-size: 16px;">{gift_icon}</span>
                    <span>Slot {s} ({gift_title})</span>
                </button>
                """

            # Load existing cookies content for textarea
            cookies_content = ""
            if cookies_exist:
                try:
                    with open(COOKIES_FILE, 'r', encoding='utf-8') as cf:
                        cookies_content = cf.read()
                except Exception:
                    pass

            char_cfg = load_character_config()
            pos_x = char_cfg.get("position", {}).get("x", 60)
            pos_y = char_cfg.get("position", {}).get("y", 120)
            overlay_is_on = char_cfg.get("overlay_enabled", True)
            chars_dict = char_cfg.get("characters", {})

            # 1. Build thumbnails for 4 characters
            char_thumbs_items = []
            for cid in ["1", "2", "3", "4"]:
                c_data = chars_dict.get(cid, {})
                c_name = c_data.get("name", f"Karakter {cid}")
                b_color_rgb = CHAR_COLORS.get(cid, (37, 244, 238))
                b_hex = f"#{b_color_rgb[0]:02x}{b_color_rgb[1]:02x}{b_color_rgb[2]:02x}"
                
                av_file_exists = False
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    if os.path.exists(os.path.join(AVATARS_DIR, f"char_{cid}{ext}")):
                        av_file_exists = True
                        break
                
                if av_file_exists:
                    thumb_markup = f"""
                    <div style="background:#121214; padding:10px 14px; border-radius:8px; border:1px solid #27272a; display:flex; align-items:center; justify-content:space-between; gap:10px;">
                        <div style="display:flex; align-items:center; gap:10px;">
                            <img src="/avatar-img?char_id={cid}&v={time.time()}" style="width:40px; height:40px; border-radius:50%; object-fit:cover; border:2px solid {b_hex};" />
                            <div>
                                <div style="font-size:13px; font-weight:bold; color:#fff;">{c_name}</div>
                                <div style="font-size:11px; color:#34c759;">Avatar Terpasang</div>
                            </div>
                        </div>
                        <a href="/delete-avatar?char_id={cid}" style="color:#ff3b30; font-size:11px; text-decoration:none; padding:3px 8px; border:1px solid #ff3b30; border-radius:4px;">Hapus</a>
                    </div>
                    """
                else:
                    thumb_markup = f"""
                    <div style="background:#121214; padding:10px 14px; border-radius:8px; border:1px solid #27272a; display:flex; align-items:center; justify-content:space-between; gap:10px;">
                        <div style="display:flex; align-items:center; gap:10px;">
                            <div style="width:40px; height:40px; border-radius:50%; background:{b_hex}22; border:2px dashed {b_hex}; display:flex; align-items:center; justify-content:center; font-weight:bold; color:{b_hex}; font-size:13px;">K{cid}</div>
                            <div>
                                <div style="font-size:13px; font-weight:bold; color:#fff;">{c_name}</div>
                                <div style="font-size:11px; color:#71717a;">Default Badge K{cid}</div>
                            </div>
                        </div>
                        <span style="font-size:11px; color:#71717a;">Standar</span>
                    </div>
                    """
                char_thumbs_items.append(thumb_markup)
            characters_thumbnails_html = "\n".join(char_thumbs_items)

            # 2. Build 5 mapping rows (Slot 2 s.d. Slot 6)
            mappings_list = char_cfg.get("mappings", [])
            mapping_rows_items = []
            for r in range(5):
                slot_target = r + 2
                row_data = mappings_list[r] if r < len(mappings_list) else {"slot": slot_target, "gift_id": "rose", "character_ids": ["1"]}
                cur_gift = row_data.get("gift_id", "rose")
                cur_chars = [str(x) for x in row_data.get("character_ids", [])]

                gift_options = []
                for g in GIFTS_CATALOG:
                    sel = "selected" if g["id"] == cur_gift else ""
                    gift_options.append(f'<option value="{g["id"]}" {sel}>{g["icon"]} {g["name"]} ({g["coins"]} coin)</option>')
                gift_options_str = "\n".join(gift_options)

                char_checkboxes = []
                for cid in ["1", "2", "3", "4"]:
                    chk = "checked" if cid in cur_chars else ""
                    b_color_rgb = CHAR_COLORS.get(cid, (37, 244, 238))
                    b_hex = f"#{b_color_rgb[0]:02x}{b_color_rgb[1]:02x}{b_color_rgb[2]:02x}"
                    c_name_short = f"Karakter {cid}"
                    char_checkboxes.append(f"""
                    <label style="display:inline-flex; align-items:center; gap:5px; margin-right:12px; cursor:pointer; font-size:12px; color:#fff;">
                        <input type="checkbox" name="row_{r}_char_{cid}" value="{cid}" {chk} style="cursor:pointer;" />
                        <span style="color:{b_hex}; font-weight:600;">{c_name_short}</span>
                    </label>
                    """)
                char_checkboxes_str = "".join(char_checkboxes)

                row_html = f"""
                <div style="background:#121214; padding:12px 14px; border-radius:8px; border:1px solid #27272a; display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:10px;">
                    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                        <span style="font-size:12px; font-weight:bold; color:#a1a1aa; width:50px;">Slot {slot_target}:</span>
                        <select name="row_{r}_gift" style="background:#1c1c1e; color:#fff; border:1px solid #3f3f46; border-radius:6px; padding:6px 10px; font-size:12px; outline:none;">
                            {gift_options_str}
                        </select>
                        <span style="font-size:16px; font-weight:bold; color:#25f4ee; padding:0 4px;">=</span>
                        <div style="display:inline-flex; align-items:center; flex-wrap:wrap;">
                            {char_checkboxes_str}
                        </div>
                    </div>
                    <div style="display:flex; gap:6px;">
                        <button type="button" class="modern-btn" style="padding:4px 8px; font-size:11px; background:#27272a; color:#25f4ee; border:1px solid #3f3f46;" onclick="selectAllChars({r}, true)">Semua</button>
                        <button type="button" class="modern-btn" style="padding:4px 8px; font-size:11px; background:#27272a; color:#ff3b30; border:1px solid #3f3f46;" onclick="selectAllChars({r}, false)">Reset</button>
                    </div>
                </div>
                """
                mapping_rows_items.append(row_html)
            mapping_rows_html = "\n".join(mapping_rows_items)

            if not os.path.exists(GUIDE_OVERLAY_FILE):
                try:
                    generate_guide_overlay(char_cfg)
                except Exception:
                    pass

            overlay_enabled_checked = "checked" if overlay_is_on else ""

            overlay_card_html = f"""
            <div class="shine-border-card" style="animation-delay: 1.2s;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                    <h3 style="margin:0; color:#fff;">Panduan Visual Karakter & Overlay Gift</h3>
                    <span style="font-size:11px; background:rgba(37, 244, 238, 0.15); color:#25f4ee; border:1px solid rgba(37, 244, 238, 0.3); padding:4px 10px; border-radius:20px; font-weight:600;">FFmpeg Stream Overlay</span>
                </div>
                <p style="color:#a1a1aa; font-size:13px; margin-bottom:20px;">
                    Upload avatar untuk 4 karakter, atur pemetaan Ikon Gift = Karakter, dan render otomatis sebagai overlay visual di atas kepala karakter pada live stream.
                </p>

                <!-- Bagian 1: Upload Avatar Terpusat -->
                <div style="background:#18181b; padding:16px; border-radius:12px; border:1px solid #27272a; margin-bottom:20px;">
                    <h4 style="margin:0 0 12px 0; color:#25f4ee; font-size:14px; display:flex; align-items:center; gap:8px;">
                        <span>👤</span> Bagian 1: Manajemen Avatar Karakter (Upload Tool)
                    </h4>
                    
                    <form action="/upload-avatar" method="POST" enctype="multipart/form-data" style="display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-bottom:16px; background:#121214; padding:12px; border-radius:8px; border:1px solid #222;">
                        <label style="font-size:13px; color:#fff; font-weight:600;">Target Karakter:</label>
                        <select name="char_id" style="background:#1c1c1e; color:#fff; border:1px solid #3f3f46; border-radius:6px; padding:6px 12px; font-size:13px; outline:none;">
                            <option value="1">Karakter 1 (Kucing Kribo)</option>
                            <option value="2">Karakter 2</option>
                            <option value="3">Karakter 3</option>
                            <option value="4">Karakter 4</option>
                        </select>
                        <input type="file" name="avatarfile" accept="image/png,image/jpeg,image/webp" required style="color:#aaa; font-size:12px;" />
                        <button type="submit" class="modern-btn btn-upload" style="padding:6px 16px; font-size:12px;">📤 Upload Avatar</button>
                    </form>

                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px;">
                        {characters_thumbnails_html}
                    </div>
                </div>

                <!-- Bagian 2 & 3: Konfigurasi Mapping & Preview -->
                <form action="/save-character-mapping" method="POST">
                    <!-- Bagian 2: Pemetaan Visual -->
                    <div style="background:#18181b; padding:16px; border-radius:12px; border:1px solid #27272a; margin-bottom:20px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                            <h4 style="margin:0; color:#25f4ee; font-size:14px; display:flex; align-items:center; gap:8px;">
                                <span>🎯</span> Bagian 2: Pemetaan Visual (Ikon Gift = Karakter Target)
                            </h4>
                            <span style="font-size:11px; color:#888;">Mewakili Pemicu Slot 2 s.d. Slot 6</span>
                        </div>
                        
                        <div style="display:flex; flex-direction:column; gap:10px;">
                            {mapping_rows_html}
                        </div>
                    </div>

                    <!-- Bagian 3: Kontrol & Preview Stream -->
                    <div style="background:#18181b; padding:16px; border-radius:12px; border:1px solid #27272a;">
                        <h4 style="margin:0 0 12px 0; color:#25f4ee; font-size:14px; display:flex; align-items:center; gap:8px;">
                            <span>⚙️</span> Bagian 3: Kontrol Overlay Stream & Preview
                        </h4>

                        <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:flex-start; gap:20px;">
                            <div style="flex:1; min-width:260px;">
                                <div style="margin-bottom:15px;">
                                    <label style="display:inline-flex; align-items:center; gap:10px; cursor:pointer; color:#fff; font-size:13px; font-weight:600;">
                                        <input type="checkbox" name="overlay_enabled" value="on" {overlay_enabled_checked} style="transform:scale(1.2); cursor:pointer;" />
                                        <span>Aktifkan Panduan Visual Overlay di Stream FFmpeg</span>
                                    </label>
                                </div>

                                <div style="display:flex; gap:16px; align-items:center; margin-bottom:18px;">
                                    <div>
                                        <label style="font-size:12px; color:#a1a1aa; display:block; margin-bottom:4px;">Koordinat X (px):</label>
                                        <input type="number" name="pos_x" value="{pos_x}" style="width:80px; padding:6px 10px; border-radius:6px; background:#121214; border:1px solid #3f3f46; color:#fff; font-size:13px;" />
                                    </div>
                                    <div>
                                        <label style="font-size:12px; color:#a1a1aa; display:block; margin-bottom:4px;">Koordinat Y (px):</label>
                                        <input type="number" name="pos_y" value="{pos_y}" style="width:80px; padding:6px 10px; border-radius:6px; background:#121214; border:1px solid #3f3f46; color:#fff; font-size:13px;" />
                                    </div>
                                    <div style="font-size:11px; color:#888; align-self:flex-end; padding-bottom:6px;">
                                        (Default: X=20, Y=30 di atas kepala Kucing Kribo)
                                    </div>
                                </div>

                                <button type="submit" class="shimmer-btn" style="--shimmer-color:#25f4ee; --bg:rgba(37, 244, 238, 0.15); padding:10px 20px; font-size:13px;">
                                    💾 Simpan & Generate Overlay
                                </button>
                            </div>

                            <!-- Preview Gambar Overlay -->
                            <div style="flex:1; min-width:260px; text-align:center; background:#121214; padding:16px; border-radius:10px; border:1px solid #222;">
                                <div style="font-size:12px; font-weight:600; color:#25f4ee; margin-bottom:10px;">
                                    🖼️ Live Preview Output: uploads/overlays/guide_overlay.png
                                </div>
                                <div style="display:inline-block; background-image:linear-gradient(45deg, #1c1c20 25%, transparent 25%), linear-gradient(-45deg, #1c1c20 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #1c1c20 75%), linear-gradient(-45deg, transparent 75%, #1c1c20 75%); background-size:14px 14px; background-position:0 0, 0 7px, 7px -7px, -7px 0px; padding:15px; border-radius:10px; border:1px solid #2f2f35;">
                                    <img src="/overlay-preview?v={time.time()}" alt="Preview Overlay" style="max-height:200px; width:auto; max-width:100%; border-radius:6px; display:block;" />
                                </div>
                                <div style="font-size:11px; color:#71717a; margin-top:8px;">
                                    Ukuran dinamis sesuai karakter terpilih & border anti-aliased.
                                </div>
                            </div>
                        </div>
                    </div>
                </form>
            </div>
            """

            vpn_card_html = f"""
            <div class="shine-border-card" style="animation-delay: 1.3s;">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:12px;">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <div style="width:12px; height:12px; border-radius:50%; background:{vpn_badge_color}; box-shadow:{vpn_dot_shadow};"></div>
                        <h3 style="margin:0; color:#fff;">Status VPN Cloudflare WARP (Local SOCKS5 Proxy)</h3>
                    </div>
                    <div style="background:{'rgba(37, 244, 238, 0.15)' if (is_wp_running and is_warp) else 'rgba(113, 113, 122, 0.2)'}; border:1px solid {vpn_badge_color}; color:{vpn_badge_color}; padding:4px 14px; border-radius:16px; font-weight:bold; font-size:12px;">
                        {'🛡️ VPN ACTIVE (WARP)' if (is_wp_running and is_warp) else ('🟡 CONNECTING...' if is_wp_running else '🌐 DIRECT (OFF)')}
                    </div>
                </div>
                
                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:12px; background:#121214; padding:14px; border-radius:8px; border:1px solid #27272a; margin-bottom:16px;">
                    <div>
                        <div style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">IP Publik Egress Live</div>
                        <div id="vpnEgressIp" style="font-family:monospace; font-size:15px; color:#25f4ee; font-weight:bold; word-break:break-all;">{egress_ip}</div>
                    </div>
                    <div>
                        <div style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">Mode Proxy Terisolasi</div>
                        <div style="font-size:12px; color:#eee;">SOCKS5: <code style="color:#25f4ee;">127.0.0.1:40000</code></div>
                        <div style="font-size:12px; color:#eee;">HTTP: <code style="color:#25f4ee;">127.0.0.1:40001</code></div>
                    </div>
                    <div>
                        <div style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">Keamanan Host & Port 8088</div>
                        <div style="font-size:12px; color:#34c759;">✅ SSH & Port 8088 Tidak Terdampak</div>
                    </div>
                    <div>
                        <div style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">VPN Watchdog (Anti-Leak)</div>
                        <div style="display:flex; align-items:center; gap:6px;">
                            <span id="vpnCardWdDot" style="width:8px; height:8px; border-radius:50%; background:{wd_badge_color}; display:inline-block;"></span>
                            <span id="vpnCardWdState" style="font-size:12px; font-weight:bold; color:{wd_badge_color};">{wd_badge_text}</span>
                        </div>
                        <div style="font-size:11px; color:#a1a1aa; margin-top:2px;">Reconnects: <b id="vpnCardWdCount" style="color:#fff;">{reconnects}</b> | Cek: <span id="vpnCardWdLastCheck">{last_chk_str}</span></div>
                    </div>
                </div>

                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:14px;">
                    <form action="/toggle-vpn" method="POST" style="margin:0; display:flex; align-items:center; gap:10px;">
                        <input type="hidden" name="action" value="toggle_auto" />
                        <label style="display:flex; align-items:center; gap:8px; cursor:pointer; color:#fff; font-size:13px; font-weight:bold;">
                            <input type="checkbox" name="auto_vpn" value="on" {'checked' if auto_vpn_on else ''} onchange="this.form.submit()" style="width:18px; height:18px; accent-color:#25f4ee; cursor:pointer;" />
                            Gunakan Auto-VPN saat Live (Otomatis Aktif saat Start Live)
                        </label>
                    </form>

                    <div style="display:flex; gap:8px;">
                        {f'''<form action="/toggle-vpn" method="POST" style="margin:0;">
                            <input type="hidden" name="action" value="stop" />
                            <button type="submit" class="shimmer-btn" style="--shimmer-color:#ff3b30; --bg:rgba(255, 59, 48, 0.15); font-size:12px; padding:6px 14px;">Matikan Manual VPN</button>
                        </form>''' if is_wp_running else f'''<form action="/toggle-vpn" method="POST" style="margin:0;">
                            <input type="hidden" name="action" value="start" />
                            <button type="submit" class="shimmer-btn" style="--shimmer-color:#25f4ee; --bg:rgba(37, 244, 238, 0.15); font-size:12px; padding:6px 14px;">Nyalakan Manual VPN</button>
                        </form>'''}
                    </div>
                </div>
            </div>
            """

            html = f"""<!DOCTYPE html>
            <html>
                <head>
                    <meta charset="utf-8">
                    <title>TikTok Live Lightweight Dashboard</title>
                    <style>
                        @keyframes shine {{
                            0% {{ background-position: 0% 0%; }}
                            50% {{ background-position: 100% 100%; }}
                            100% {{ background-position: 0% 0%; }}
                        }}
                        @keyframes spin-around {{
                            0% {{ transform: translateZ(0) rotate(0deg); }}
                            100% {{ transform: translateZ(0) rotate(360deg); }}
                        }}
                        @keyframes floating {{
                            0%, 100% {{ transform: translateY(0px); }}
                            50% {{ transform: translateY(-6px); }}
                        }}
                        .shine-border-card {{
                            position: relative;
                            border-radius: 16px;
                            background: #121216;
                            padding: 25px;
                            margin-bottom: 30px;
                            border: 1px solid #27272a;
                            overflow: hidden;
                            animation: floating 4s ease-in-out infinite;
                            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.6), 0 0 20px rgba(160, 124, 254, 0.12);
                        }}
                        .shine-border-card::before {{
                            content: "";
                            position: absolute;
                            inset: 0;
                            border-radius: inherit;
                            padding: 2px;
                            background-image: radial-gradient(transparent, transparent, #A07CFE, #FE8FB5, #FFBE7B, transparent, transparent);
                            background-size: 300% 300%;
                            -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
                            -webkit-mask-composite: xor;
                            mask-composite: exclude;
                            animation: shine 14s infinite linear;
                            pointer-events: none;
                        }}
                        .shine-border-card.online::before {{
                            animation: shine 5s infinite linear;
                            padding: 2.5px;
                        }}
                        .floating-bar {{
                            position: sticky;
                            top: 12px;
                            z-index: 999;
                            backdrop-filter: blur(12px);
                            background: rgba(18, 18, 22, 0.85);
                            border: 1px solid rgba(255, 255, 255, 0.12);
                            border-radius: 12px;
                            padding: 12px 20px;
                            display: flex;
                            justify-content: space-between;
                            align-items: center;
                            margin-bottom: 25px;
                            box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 15px rgba(160, 124, 254, 0.1);
                        }}
                        .shimmer-btn {{
                            --spread: 90deg;
                            --shimmer-color: #ffffff;
                            --radius: 100px;
                            --speed: 3s;
                            --cut: 1.5px;
                            --bg: rgba(15, 15, 20, 0.95);
                            position: relative;
                            z-index: 0;
                            display: inline-flex;
                            align-items: center;
                            justify-content: center;
                            overflow: hidden;
                            white-space: nowrap;
                            cursor: pointer;
                            border: 1px solid rgba(255, 255, 255, 0.12);
                            border-radius: var(--radius);
                            background: var(--bg);
                            color: #ffffff;
                            padding: 10px 22px;
                            font-weight: 600;
                            font-size: 13px;
                            transform: translateZ(0);
                            transition: transform 0.3s ease-in-out, box-shadow 0.3s ease-in-out;
                            box-shadow: inset 0 -8px 10px #ffffff1f;
                            text-decoration: none;
                        }}
                        .shimmer-btn:hover {{
                            box-shadow: inset 0 -6px 10px #ffffff3f;
                        }}
                        .shimmer-btn:active {{
                            transform: translateY(1px);
                            box-shadow: inset 0 -4px 6px #ffffff5f;
                        }}
                        .shimmer-btn::before {{
                            content: "";
                            position: absolute;
                            inset: calc(var(--cut) * -1);
                            z-index: -1;
                            border-radius: inherit;
                            background: conic-gradient(from calc(270deg - (var(--spread) / 2)), transparent 0, var(--shimmer-color) var(--spread), transparent var(--spread));
                            animation: spin-around var(--speed) infinite linear;
                        }}
                        
                        /* Modern Static Buttons (Upload & Gift) without shimmer animation */
                        .modern-btn {{
                            display: inline-flex;
                            align-items: center;
                            justify-content: center;
                            cursor: pointer;
                            border-radius: 8px;
                            font-weight: 600;
                            font-size: 13px;
                            color: #ffffff;
                            border: 1px solid rgba(255, 255, 255, 0.15);
                            transition: background 0.2s ease, border-color 0.2s ease;
                            text-decoration: none;
                        }}
                        .btn-upload {{
                            background: #1e293b;
                        }}
                        .btn-upload:hover {{
                            background: #334155;
                            border-color: rgba(255, 255, 255, 0.3);
                        }}
                        .btn-gift {{
                            background: #2e1065;
                        }}
                        .btn-gift:hover {{
                            background: #4c1d95;
                            border-color: rgba(255, 255, 255, 0.3);
                        }}

                        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f0f11; color: #f1f1f3; margin: 0; padding: 20px; }}
                        .container {{ max-width: 850px; margin: 0 auto; background: #18181b; padding: 30px; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); border: 1px solid #27272a; }}
                        h1, h2, h3 {{ color: #fff; }}
                        hr {{ border: 0; border-top: 1px solid #27272a; margin: 25px 0; }}
                        input[type="text"], textarea {{ width: 100%; background: #0f0f11; color: #25f4ee; border: 1px solid #3f3f46; padding: 12px; border-radius: 6px; font-family: monospace; resize: vertical; box-sizing: border-box; }}
                        pre {{ background: #0f0f11; padding: 15px; border-radius: 6px; color: #25f4ee; overflow-x: auto; font-size: 13px; border: 1px solid #3f3f46; text-align: left; }}
                        
                        /* Modal Overlay */
                        .modal-overlay {{
                            position: fixed;
                            inset: 0;
                            background: rgba(0, 0, 0, 0.75);
                            backdrop-filter: blur(8px);
                            z-index: 1000;
                            display: none;
                            align-items: center;
                            justify-content: center;
                            padding: 20px;
                        }}
                        .modal-content {{
                            background: #18181b;
                            border: 1px solid #3f3f46;
                            border-radius: 16px;
                            width: 100%;
                            max-width: 500px;
                            max-height: 85vh;
                            overflow: hidden;
                            display: flex;
                            flex-direction: column;
                            box-shadow: 0 20px 40px rgba(0,0,0,0.8);
                            animation: floating 4s ease-in-out infinite;
                        }}
                        .modal-header {{
                            padding: 15px 20px;
                            border-bottom: 1px solid #27272a;
                            display: flex;
                            justify-content: space-between;
                            align-items: center;
                        }}
                        .modal-body {{
                            padding: 20px;
                            overflow-y: auto;
                            max-height: calc(85vh - 70px);
                        }}
                    </style>
                    <script>
                        function validateUpload(form) {{
                            const fileInput = form.querySelector('input[type="file"]');
                            if (!fileInput || fileInput.files.length === 0) {{
                                alert("Peringatan: Tidak ada file yang dipilih. Silakan pilih file video (.webm atau .mp4).");
                                return false;
                            }}
                            const filename = fileInput.files[0].name.toLowerCase();
                            if (!filename.endsWith('.webm') && !filename.endsWith('.mp4')) {{
                                alert("Peringatan: Format file harus .webm atau .mp4!");
                                return false;
                            }}
                            return true;
                        }}
                        function validateStartLive() {{
                            const username = "{tiktok_username}";
                            if (!username) {{
                                alert("Peringatan: Akun TikTok belum dikonfigurasi! Harap isi Username TikTok dan simpan konfigurasi akun terlebih dahulu.");
                                return false;
                            }}
                            return true;
                        }}
                        function openGiftModal(slot) {{
                            const modal = document.getElementById('giftModal');
                            const iframe = document.getElementById('giftIframe');
                            iframe.src = '/gift-selector?slot=' + slot;
                            modal.style.display = 'flex';
                        }}
                        function closeGiftModal() {{
                            const modal = document.getElementById('giftModal');
                            modal.style.display = 'none';
                            location.reload();
                        }}
                        function editVideoName(slot, currentName) {{
                            const newName = prompt("Ubah nama tampilan file video Slot " + slot + ":", currentName);
                            if (newName && newName.trim() && newName.trim() !== currentName) {{
                                fetch('/update-video-name?slot=' + slot + '&name=' + encodeURIComponent(newName.trim()))
                                    .then(res => res.text())
                                    .then(() => {{
                                        location.reload();
                                    }});
                            }}
                        }}
                        function selectAllChars(rowIndex, selectAll) {{
                            for (let c = 1; c <= 4; c++) {{
                                const el = document.querySelector(`input[name="row_${{rowIndex}}_char_${{c}}"]`);
                                if (el) el.checked = selectAll;
                            }}
                        }}
                        function syncListenerStatus() {{
                            fetch('/api/listener-status')
                                .then(r => r.json())
                                .then(data => {{
                                    if (data && data.queue_length !== undefined) {{
                                        const qb = document.getElementById('queueBadge');
                                        if (qb) qb.innerText = data.queue_length;
                                        const qc = document.getElementById('queueCountVal');
                                        if (qc) qc.innerText = data.queue_length;
                                    }}
                                    if (data && data.watchdog) {{
                                        const wd = data.watchdog;
                                        let wdText = "IDLE (Offline)";
                                        let wdColor = "#71717a";
                                        let wdShadow = "none";
                                        if (wd.state === "PROTECTED") {{
                                            wdText = "PROTECTED (Kill-Switch Ready)";
                                            wdColor = "#34c759";
                                            wdShadow = "0 0 10px #34c759";
                                        }} else if (wd.state === "RECONNECTING") {{
                                            wdText = "RECONNECTING... (Kill-Switch Active)";
                                            wdColor = "#ff9500";
                                            wdShadow = "0 0 12px #ff9500";
                                        }} else if (wd.state === "EMERGENCY_STOPPED") {{
                                            wdText = "EMERGENCY STOPPED (VPN DOWN)";
                                            wdColor = "#ff3b30";
                                            wdShadow = "0 0 12px #ff3b30";
                                        }}
                                        const wb = document.getElementById('watchdogBadge');
                                        if (wb) {{ wb.innerText = wdText; wb.style.color = wdColor; }}
                                        const wdot = document.getElementById('watchdogDot');
                                        if (wdot) {{ wdot.style.background = wdColor; wdot.style.boxShadow = wdShadow; }}
                                        const wm = document.getElementById('watchdogMeta');
                                        const chkStr = (wd.last_check_ago !== null && wd.last_check_ago !== undefined) ? (wd.last_check_ago + 'd lalu') : '-';
                                        if (wm) wm.innerText = `(Recon: ${{wd.reconnect_count}} | Cek: ${{chkStr}})`;
                                        
                                        const cwState = document.getElementById('vpnCardWdState');
                                        if (cwState) {{ cwState.innerText = wdText; cwState.style.color = wdColor; }}
                                        const cwDot = document.getElementById('vpnCardWdDot');
                                        if (cwDot) cwDot.style.background = wdColor;
                                        const cwCount = document.getElementById('vpnCardWdCount');
                                        if (cwCount) cwCount.innerText = wd.reconnect_count;
                                        const cwCheck = document.getElementById('vpnCardWdLastCheck');
                                        if (cwCheck) cwCheck.innerText = chkStr;
                                    }}
                                    if (data && data.vpn) {{
                                        const v = data.vpn;
                                        const eg = document.getElementById('vpnEgressIp');
                                        if (eg && v.egress_ip) eg.innerText = v.egress_ip;
                                    }}
                                    if (data && data.recent_gifts && data.recent_gifts.length > 0) {{
                                        const container = document.getElementById('giftLogsContainer');
                                        if (container) {{
                                            let html = '<table style="width: 100%; border-collapse: collapse; background: #121214; border-radius: 8px; overflow: hidden; border: 1px solid #27272a; text-align: left;"><thead><tr style="background: #1c1c1e; color: #888; font-size: 12px; border-bottom: 1px solid #333;"><th style="padding: 8px 12px;">WAKTU</th><th style="padding: 8px 12px;">PENONTON</th><th style="padding: 8px 12px;">GIFT</th><th style="padding: 8px 12px;">SLOT TERPICU</th><th style="padding: 8px 12px;">STATUS</th></tr></thead><tbody>';
                                            data.recent_gifts.forEach(g => {{
                                                let badge = '<span style="background:rgba(52,199,89,0.18); color:#34c759; border:1px solid #34c759; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:bold;">✅ Selesai</span>';
                                                if (g.status === 'Sedang Diputar') {{
                                                    badge = '<span style="background:rgba(37,244,238,0.18); color:#25f4ee; border:1px solid #25f4ee; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:bold;">▶️ Sedang Diputar</span>';
                                                }} else if (g.status === 'Dalam Antrean') {{
                                                    badge = '<span style="background:rgba(255,184,0,0.18); color:#ffb800; border:1px solid #ffb800; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:bold;">⏳ Dalam Antrean</span>';
                                                }}
                                                html += `<tr style="border-bottom: 1px solid #27272a;"><td style="padding: 10px 12px; color: #aaa; font-size: 12px;">${{g.time || ''}}</td><td style="padding: 10px 12px; color: #fff; font-weight: bold; font-size: 13px;">@${{g.sender || ''}}</td><td style="padding: 10px 12px; color: #25f4ee; font-size: 13px;"><b>${{g.giftName || ''}}</b> <span style="color:#888; font-size:11px;">(x${{g.count || 1}})</span></td><td style="padding: 10px 12px; color: #ffb800; font-weight: 600; font-size: 13px;">Slot ${{g.slot || ''}}</td><td style="padding: 10px 12px;">${{badge}}</td></tr>`;
                                            }});
                                            html += '</tbody></table>';
                                            container.innerHTML = html;
                                        }}
                                    }}
                                    if (typeof queueDualScreenSlot === 'function' && data) {{
                                        if (data.gift_seq && data.gift_seq > lastProcessedGiftSeq) {{
                                            lastProcessedGiftSeq = data.gift_seq;
                                            if (data.latest_gift && data.latest_gift.slot) {{
                                                const g = data.latest_gift;
                                                if (g.sender !== 'PenontonSimulasi') {{
                                                    queueDualScreenSlot(g.slot, g.giftName, g.sender);
                                                }}
                                            }}
                                        }}
                                    }}
                                }})
                                .catch(() => {{}});
                        }}
                        function triggerAccountValidation(e) {{
                            const btn = e ? e.target : document.getElementById('btnValidateAcc');
                            const origText = btn ? btn.innerText : 'Memvalidasi...';
                            if (btn) {{
                                btn.innerText = "⏳ Memvalidasi via WARP Proxy...";
                                btn.disabled = true;
                            }}
                            fetch('/validate-account', {{ method: 'POST' }})
                                .then(r => r.json())
                                .then(res => {{
                                    if (btn) {{
                                        btn.innerText = origText;
                                        btn.disabled = false;
                                    }}
                                    if (res.success) {{
                                        alert("✅ VALIDASI SESSION BERHASIL!\\n\\n" + (res.message || "") + "\\nSession Route: " + res.via + " (" + res.egress_ip + ")\\nAkun: @" + res.username);
                                    }} else {{
                                        alert("❌ VALIDASI GAGAL!\\n\\n" + (res.message || "Gagal memverifikasi session.") + "\\nRoute: " + (res.via || '-') + "\\nIP: " + (res.egress_ip || '-'));
                                    }}
                                    location.reload();
                                }})
                                .catch(err => {{
                                    if (btn) {{
                                        btn.innerText = origText;
                                        btn.disabled = false;
                                    }}
                                    alert("🚨 Gagal menghubungi endpoint /validate-account: " + err);
                                }});
                        }}
                        function showSimStatus(msg, isSuccess, isQueue) {{
                            const el = document.getElementById('sim-status-banner');
                            if (!el) return;
                            el.style.display = 'block';
                            const color = isQueue ? '#ffb800' : (isSuccess ? '#34c759' : '#ff3b30');
                            const bg = isQueue ? 'rgba(255, 184, 0, 0.12)' : (isSuccess ? 'rgba(52, 199, 89, 0.12)' : 'rgba(255, 59, 48, 0.12)');
                            el.style.borderColor = color;
                            el.style.color = color;
                            el.style.background = bg;
                            el.innerHTML = (isQueue ? '📥 ' : (isSuccess ? '🟢 ' : '⚠️ ')) + msg;
                        }}

                        function simulateGift(e, slot) {{
                            if (e && e.preventDefault) e.preventDefault();
                            console.log('[SimulateGift] Tombol Slot ' + slot + ' diklik.');

                            const btn = document.getElementById('btn-sim-slot-' + slot) || (e ? (e.currentTarget || e.target) : null);
                            const origHtml = btn ? btn.innerHTML : ('Slot ' + slot);
                            
                            if (btn) {{
                                btn.disabled = true;
                                btn.innerHTML = '<span>⏳</span> Mengirim...';
                            }}

                            fetch('/simulate-gift?slot=' + slot, {{
                                method: 'POST',
                                headers: {{ 'Accept': 'application/json' }}
                            }})
                            .then(r => {{
                                if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + r.statusText);
                                return r.json();
                            }})
                            .then(data => {{
                                console.log('[SimulateGift] Response dari server:', data);
                                if (btn) {{
                                    btn.disabled = false;
                                    if (data.queued) {{
                                        btn.innerHTML = '📥 Antrean #' + (data.queue_length || 1);
                                        btn.style.borderColor = '#ffb800';
                                    }} else {{
                                        btn.innerHTML = '🟢 Sedang Diputar';
                                        btn.style.borderColor = '#34c759';
                                    }}
                                    setTimeout(() => {{
                                        btn.innerHTML = origHtml;
                                        btn.style.borderColor = '';
                                    }}, 2500);
                                }}

                                if (data.success) {{
                                    if (typeof queueDualScreenSlot === 'function') {{
                                        queueDualScreenSlot(slot, data.giftName, 'Simulasi');
                                    }}
                                    showSimStatus(data.message || ('Simulasi Slot ' + slot + ' diterima!'), true, data.queued);
                                }} else {{
                                    showSimStatus(data.message || 'Gagal memicu simulasi', false, false);
                                }}
                                if (typeof syncListenerStatus === 'function') syncListenerStatus();
                            }})
                            .catch(err => {{
                                console.error('[SimulateGift] Error:', err);
                                if (btn) {{
                                    btn.disabled = false;
                                    btn.innerHTML = '⚠️ Error';
                                    setTimeout(() => {{
                                        btn.innerHTML = origHtml;
                                        btn.style.borderColor = '';
                                    }}, 2500);
                                }}
                                showSimStatus('Gagal request simulasi: ' + err.message, false, false);
                            }});
                        }}

                        function resetStandby(e) {{
                            if (e && e.preventDefault) e.preventDefault();
                            console.log('[ResetStandby] Reset antrean & kembali ke Standby Slot 1.');
                            const btn = document.getElementById('btn-reset-standby') || (e ? (e.currentTarget || e.target) : null);
                            const origHtml = btn ? btn.innerHTML : 'Reset ke Standby';
                            
                            if (btn) {{
                                btn.disabled = true;
                                btn.innerHTML = '<span>⏳</span> Mereset...';
                            }}

                            fetch('/switch-slot?slot=1', {{
                                method: 'POST',
                                headers: {{ 'Accept': 'application/json' }}
                            }})
                            .then(r => {{
                                if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + r.statusText);
                                return r.json();
                            }})
                            .then(data => {{
                                if (btn) {{
                                    btn.disabled = false;
                                    btn.innerHTML = '✅ Standby Aktif!';
                                    btn.style.borderColor = '#34c759';
                                    setTimeout(() => {{
                                        btn.innerHTML = origHtml;
                                        btn.style.borderColor = '';
                                    }}, 2500);
                                }}
                                if (typeof queueDualScreenSlot === 'function') queueDualScreenSlot(1, 'Standby Loop', '');
                                showSimStatus('Antrean dikosongkan. Kembali ke Standby Loop (Slot 1).', true, false);
                                if (typeof syncListenerStatus === 'function') syncListenerStatus();
                            }})
                            .catch(err => {{
                                console.error('[ResetStandby] Error:', err);
                                if (btn) {{
                                    btn.disabled = false;
                                    btn.innerHTML = '⚠️ Error';
                                    setTimeout(() => {{
                                        btn.innerHTML = origHtml;
                                        btn.style.borderColor = '';
                                    }}, 2500);
                                }}
                                showSimStatus('Gagal reset: ' + err.message, false, false);
                            }});
                        }}
                        setInterval(syncListenerStatus, 1000);

                        // --- Dual-Screen Live Studio (Client-Side Canvas Compositing - Opsi A) ---
                        let isDualScreenActive = false;
                        let dualMediaStream = null;
                        let dualAudioCtx = null;
                        let dualAudioDest = null;
                        let dualMicGain = null;
                        let dualCatAudioSource = null;
                        let dualCanvasStream = null;
                        let dualMediaRecorder = null;
                        let dualStreamWs = null;
                        let dualIsStreaming = false;
                        let dualCurrentSlot = 1;
                        let dualIsMirror = true;
                        let dualIsMicMuted = false;
                        let dualAnimFrame = null;
                        let dualGiftQueue = [];
                        let dualIsReactionPlaying = false;
                        let lastProcessedGiftSeq = 0;
                        let dualActiveToast = null;
                        let dualToastTimeout = null;
                        let dualReactionTimer = null;

                        function showCanvasToast(msg) {{
                            dualActiveToast = msg;
                            if (dualToastTimeout) clearTimeout(dualToastTimeout);
                            dualToastTimeout = setTimeout(() => {{
                                dualActiveToast = null;
                            }}, 4000);
                        }}

                        function queueDualScreenSlot(slot, giftName, sender) {{
                            if (!slot) return;
                            if (slot === 1) {{
                                dualGiftQueue = [];
                                dualIsReactionPlaying = false;
                                if (dualReactionTimer) clearTimeout(dualReactionTimer);
                                playSlotImmediate(1, 'Standby Loop');
                                return;
                            }}
                            dualGiftQueue.push({{
                                slot: slot,
                                giftName: giftName || ('Gift Slot ' + slot),
                                sender: sender || 'Penonton'
                            }});
                            showCanvasToast('🎁 Gift: ' + (giftName || 'Slot ' + slot) + (sender ? ' (@' + sender + ')' : ''));
                            processDualScreenQueue();
                        }}

                        function processDualScreenQueue() {{
                            if (dualIsReactionPlaying) return;
                            
                            if (dualGiftQueue.length > 0) {{
                                const nextItem = dualGiftQueue.shift();
                                dualIsReactionPlaying = true;
                                playSlotImmediate(nextItem.slot, nextItem.giftName);
                            }} else {{
                                dualIsReactionPlaying = false;
                                if (dualCurrentSlot !== 1) {{
                                    playSlotImmediate(1, 'Standby Loop');
                                }}
                            }}
                        }}

                        function playSlotImmediate(slot, giftName) {{
                            const catVid = document.getElementById('dualCatVideo');
                            if (!catVid) return;
                            dualCurrentSlot = slot;
                            const badge = document.getElementById('dualCurrentSlotBadge');
                            if (badge) {{
                                badge.innerText = (slot === 1 ? '🐱 Slot 1 (Standby)' : '🎁 Slot ' + slot + ' (' + (giftName || 'Reaksi') + ')');
                                badge.style.color = (slot === 1 ? '#34c759' : '#ffb800');
                                badge.style.borderColor = (slot === 1 ? '#34c759' : '#ffb800');
                            }}

                            if (dualReactionTimer) clearTimeout(dualReactionTimer);

                            catVid.src = '/video-stream?name=video_' + slot + '.mp4';
                            catVid.loop = (slot === 1);

                            catVid.onended = function() {{
                                if (slot !== 1) {{
                                    if (dualReactionTimer) clearTimeout(dualReactionTimer);
                                    dualIsReactionPlaying = false;
                                    processDualScreenQueue();
                                }}
                            }};

                            catVid.onerror = function(err) {{
                                console.warn('[DualScreen] Cat video error loading slot ' + slot, err);
                                if (slot !== 1) {{
                                    if (dualReactionTimer) clearTimeout(dualReactionTimer);
                                    dualIsReactionPlaying = false;
                                    processDualScreenQueue();
                                }}
                            }};

                            catVid.onloadedmetadata = function() {{
                                if (slot !== 1) {{
                                    const dur = (catVid.duration && !isNaN(catVid.duration) && catVid.duration > 0) ? catVid.duration : 15;
                                    if (dualReactionTimer) clearTimeout(dualReactionTimer);
                                    dualReactionTimer = setTimeout(() => {{
                                        if (dualIsReactionPlaying && dualCurrentSlot === slot) {{
                                            console.log('[DualScreen] Safety timer auto-advance for slot ' + slot);
                                            dualIsReactionPlaying = false;
                                            processDualScreenQueue();
                                        }}
                                    }}, (dur + 1.5) * 1000);
                                }}
                            }};

                            catVid.play().catch(e => {{
                                console.warn('Cat video play warning:', e);
                                if (slot !== 1) {{
                                    if (dualReactionTimer) clearTimeout(dualReactionTimer);
                                    dualIsReactionPlaying = false;
                                    processDualScreenQueue();
                                }}
                            }});
                        }}

                        function initDualScreenStudio() {{
                            const canvas = document.getElementById('dualCompositeCanvas');
                            if (!canvas) return;

                            // Explicitly bind event listeners to gift simulation buttons for max reliability
                            for (let s = 2; s <= 6; s++) {{
                                const simBtn = document.getElementById('btn-sim-slot-' + s);
                                if (simBtn) {{
                                    simBtn.addEventListener('click', (ev) => simulateGift(ev, s));
                                }}
                            }}
                            const rstBtn = document.getElementById('btn-reset-standby');
                            if (rstBtn) {{
                                rstBtn.addEventListener('click', (ev) => resetStandby(ev));
                            }}

                            if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {{
                                navigator.mediaDevices.enumerateDevices().then(devices => {{
                                    const camSelect = document.getElementById('cameraSourceSelect');
                                    const micSelect = document.getElementById('micSourceSelect');
                                    if (camSelect) camSelect.innerHTML = '<option value="">Default Kamera Depan</option>';
                                    if (micSelect) micSelect.innerHTML = '<option value="">Default Mikrofon</option>';
                                    let camCount = 0;
                                    let micCount = 0;
                                    devices.forEach(d => {{
                                        if (d.kind === 'videoinput') {{
                                            camCount++;
                                            const opt = document.createElement('option');
                                            opt.value = d.deviceId;
                                            opt.text = d.label || ('Kamera ' + camCount);
                                            if (camSelect) camSelect.appendChild(opt);
                                        }} else if (d.kind === 'audioinput') {{
                                            micCount++;
                                            const opt = document.createElement('option');
                                            opt.value = d.deviceId;
                                            opt.text = d.label || ('Mikrofon ' + micCount);
                                            if (micSelect) micSelect.appendChild(opt);
                                        }}
                                    }});
                                }}).catch(err => console.warn('enumerateDevices error:', err));
                            }}

                            startCanvasLoop();
                        }}

                        function startCanvasLoop() {{
                            const canvas = document.getElementById('dualCompositeCanvas');
                            if (!canvas) return;
                            const ctx = canvas.getContext('2d');
                            const catVid = document.getElementById('dualCatVideo');
                            const camVid = document.getElementById('dualCamVideo');

                            function draw() {{
                                // Base Background
                                ctx.fillStyle = '#0a0a0f';
                                ctx.fillRect(0, 0, 720, 1280);

                                // 1. Render Cat Video (Top 720x640)
                                if (catVid && catVid.readyState >= 2) {{
                                    const vW = catVid.videoWidth || 720;
                                    const vH = catVid.videoHeight || 640;
                                    const scale = Math.max(720 / vW, 640 / vH);
                                    const drawW = vW * scale;
                                    const drawH = vH * scale;
                                    const drawX = (720 - drawW) / 2;
                                    const drawY = (640 - drawH) / 2;
                                    ctx.save();
                                    ctx.beginPath();
                                    ctx.rect(0, 0, 720, 640);
                                    ctx.clip();
                                    ctx.drawImage(catVid, drawX, drawY, drawW, drawH);
                                    ctx.restore();
                                }} else {{
                                    ctx.fillStyle = '#14141c';
                                    ctx.fillRect(0, 0, 720, 640);
                                    ctx.fillStyle = '#888';
                                    ctx.font = 'bold 22px sans-serif';
                                    ctx.textAlign = 'center';
                                    ctx.fillText('🐱 Video Kucing (Standby)', 360, 320);
                                }}

                                // 2. Render Host Camera (Bottom 720x640)
                                if (camVid && camVid.readyState >= 2) {{
                                    const cW = camVid.videoWidth || 720;
                                    const cH = camVid.videoHeight || 640;
                                    const scale = Math.max(720 / cW, 640 / cH);
                                    const drawW = cW * scale;
                                    const drawH = cH * scale;
                                    const drawX = (720 - drawW) / 2;
                                    const drawY = 640 + (640 - drawH) / 2;
                                    ctx.save();
                                    ctx.beginPath();
                                    ctx.rect(0, 640, 720, 640);
                                    ctx.clip();
                                    if (dualIsMirror) {{
                                        ctx.translate(720, 0);
                                        ctx.scale(-1, 1);
                                        ctx.drawImage(camVid, (720 - drawW) / 2, drawY, drawW, drawH);
                                    }} else {{
                                        ctx.drawImage(camVid, drawX, drawY, drawW, drawH);
                                    }}
                                    ctx.restore();
                                }} else {{
                                    ctx.fillStyle = '#0f0f15';
                                    ctx.fillRect(0, 640, 720, 640);
                                    ctx.fillStyle = '#71717a';
                                    ctx.font = 'bold 20px sans-serif';
                                    ctx.textAlign = 'center';
                                    ctx.fillText('📷 Klik "Aktifkan Kamera & Mic"', 360, 960);
                                }}

                                // 3. Glowing Neon Accent Divider Bar
                                ctx.save();
                                const neonGrad = ctx.createLinearGradient(0, 640, 720, 640);
                                neonGrad.addColorStop(0, '#25f4ee');
                                neonGrad.addColorStop(0.5, '#00f2fe');
                                neonGrad.addColorStop(1, '#fe2c55');
                                ctx.fillStyle = neonGrad;
                                ctx.shadowColor = '#25f4ee';
                                ctx.shadowBlur = 12;
                                ctx.fillRect(0, 638, 720, 4);
                                ctx.restore();

                                // Center HUD Pill Badge
                                ctx.save();
                                ctx.fillStyle = 'rgba(12, 12, 18, 0.9)';
                                ctx.beginPath();
                                ctx.roundRect(210, 618, 300, 44, 22);
                                ctx.fill();
                                ctx.strokeStyle = '#25f4ee';
                                ctx.lineWidth = 2;
                                ctx.shadowColor = '#25f4ee';
                                ctx.shadowBlur = 8;
                                ctx.stroke();

                                ctx.fillStyle = '#fe2c55';
                                ctx.beginPath();
                                ctx.arc(245, 640, 6, 0, Math.PI * 2);
                                ctx.fill();

                                ctx.fillStyle = '#fff';
                                ctx.font = 'bold 15px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                                ctx.textAlign = 'center';
                                ctx.fillText('LIVE HOST VERIFIED', 370, 646);
                                ctx.restore();

                                // Slot Watermark Badge on Top-Left
                                ctx.save();
                                ctx.fillStyle = 'rgba(0, 0, 0, 0.65)';
                                ctx.beginPath();
                                ctx.roundRect(24, 24, 240, 38, 10);
                                ctx.fill();
                                ctx.strokeStyle = (dualCurrentSlot === 1 ? '#34c759' : '#fe2c55');
                                ctx.lineWidth = 1.5;
                                ctx.stroke();
                                ctx.fillStyle = (dualCurrentSlot === 1 ? '#34c759' : '#fe2c55');
                                ctx.font = 'bold 14px sans-serif';
                                ctx.textAlign = 'left';
                                ctx.fillText(dualCurrentSlot === 1 ? '🐱 STANDBY (SLOT 1)' : '🎁 REAKSI (SLOT ' + dualCurrentSlot + ')', 38, 48);
                                ctx.restore();

                                // Live Floating Toast Notification (If Gift Arrives)
                                if (dualActiveToast) {{
                                    ctx.save();
                                    ctx.fillStyle = 'rgba(15, 15, 24, 0.9)';
                                    ctx.beginPath();
                                    ctx.roundRect(30, 560, 660, 50, 14);
                                    ctx.fill();
                                    ctx.strokeStyle = '#fe2c55';
                                    ctx.lineWidth = 2;
                                    ctx.shadowColor = '#fe2c55';
                                    ctx.shadowBlur = 10;
                                    ctx.stroke();
                                    ctx.fillStyle = '#25f4ee';
                                    ctx.font = 'bold 17px -apple-system, BlinkMacSystemFont, sans-serif';
                                    ctx.textAlign = 'center';
                                    ctx.fillText(dualActiveToast, 360, 592);
                                    ctx.restore();
                                }}

                                dualAnimFrame = requestAnimationFrame(draw);
                            }}
                            draw();
                        }}

                        async function toggleCamera() {{
                            const btn = document.getElementById('btnToggleCam');
                            const badge = document.getElementById('dualStatusBadge');
                            const camVid = document.getElementById('dualCamVideo');
                            const catVid = document.getElementById('dualCatVideo');

                            if (dualMediaStream) {{
                                dualMediaStream.getTracks().forEach(t => t.stop());
                                dualMediaStream = null;
                                if (camVid) camVid.srcObject = null;
                                if (btn) {{
                                    btn.innerHTML = '📷 Aktifkan Kamera & Mic';
                                    btn.style.setProperty('--shimmer-color', '#34c759');
                                    btn.style.setProperty('--bg', 'rgba(52, 199, 89, 0.15)');
                                }}
                                if (badge) {{
                                    badge.innerText = '⚪ Standby (Kamera Nonaktif)';
                                    badge.style.color = '#a1a1aa';
                                    badge.style.borderColor = '#71717a';
                                    badge.style.background = 'rgba(113, 113, 122, 0.2)';
                                }}
                                isDualScreenActive = false;
                                return;
                            }}

                            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {{
                                alert('Browser Anda memblokir akses media. Pastikan membuka dashboard via HTTPS (https://staging.apologets.com:8088).');
                                return;
                            }}

                            try {{
                                const camId = document.getElementById('cameraSourceSelect') ? document.getElementById('cameraSourceSelect').value : null;
                                const micId = document.getElementById('micSourceSelect') ? document.getElementById('micSourceSelect').value : null;

                                const constraints = {{
                                    video: camId ? {{ deviceId: {{ exact: camId }}, width: {{ ideal: 720 }}, height: {{ ideal: 640 }} }} : {{ facingMode: 'user', width: {{ ideal: 720 }}, height: {{ ideal: 640 }} }},
                                    audio: micId ? {{ deviceId: {{ exact: micId }} }} : true
                                }};

                                const stream = await navigator.mediaDevices.getUserMedia(constraints);
                                dualMediaStream = stream;
                                if (camVid) {{
                                    camVid.srcObject = stream;
                                    camVid.play().catch(e => console.warn(e));
                                }}

                                if (catVid) {{
                                    catVid.crossOrigin = "anonymous";
                                    catVid.src = '/video-stream?name=video_1.mp4';
                                    catVid.loop = true;
                                    catVid.muted = false;
                                    catVid.play().catch(e => console.warn(e));
                                }}

                                setupDualAudioMixing(stream, catVid);

                                if (btn) {{
                                    btn.innerHTML = '🛑 Matikan Kamera & Mic';
                                    btn.style.setProperty('--shimmer-color', '#ff3b30');
                                    btn.style.setProperty('--bg', 'rgba(255, 59, 48, 0.2)');
                                }}
                                if (badge) {{
                                    badge.innerText = '🟢 Preview Dual-Screen Aktif';
                                    badge.style.color = '#34c759';
                                    badge.style.borderColor = '#34c759';
                                    badge.style.background = 'rgba(52, 199, 89, 0.2)';
                                }}
                                isDualScreenActive = true;
                            }} catch (err) {{
                                alert('Gagal mengakses kamera/mic: ' + err.message);
                                console.error(err);
                            }}
                        }}

                        function setupDualAudioMixing(micStream, catVid) {{
                            try {{
                                if (!dualAudioCtx) {{
                                    dualAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
                                }}
                                if (dualAudioCtx.state === 'suspended') {{
                                    dualAudioCtx.resume();
                                }}
                                dualAudioDest = dualAudioCtx.createMediaStreamDestination();

                                if (micStream.getAudioTracks().length > 0) {{
                                    const micSrc = dualAudioCtx.createMediaStreamSource(micStream);
                                    dualMicGain = dualAudioCtx.createGain();
                                    dualMicGain.gain.value = dualIsMicMuted ? 0 : 1.0;
                                    micSrc.connect(dualMicGain);
                                    dualMicGain.connect(dualAudioDest);

                                    const analyser = dualAudioCtx.createAnalyser();
                                    analyser.fftSize = 64;
                                    micSrc.connect(analyser);
                                    const dataArray = new Uint8Array(analyser.frequencyBinCount);
                                    function updateVu() {{
                                        if (!dualMediaStream) return;
                                        analyser.getByteFrequencyData(dataArray);
                                        let sum = 0;
                                        for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
                                        const avg = sum / dataArray.length;
                                        const pct = Math.min(100, Math.round((avg / 128) * 100));
                                        const bar = document.getElementById('dualVuLevel');
                                        if (bar) bar.style.width = pct + '%';
                                        requestAnimationFrame(updateVu);
                                    }}
                                    updateVu();
                                }}

                                if (catVid && !dualCatAudioSource) {{
                                    try {{
                                        dualCatAudioSource = dualAudioCtx.createMediaElementSource(catVid);
                                        const catGain = dualAudioCtx.createGain();
                                        catGain.gain.value = 1.0;
                                        dualCatAudioSource.connect(catGain);
                                        dualCatAudioSource.connect(dualAudioCtx.destination);
                                        catGain.connect(dualAudioDest);
                                    }} catch (e) {{
                                        console.warn('AudioElementSource warning:', e);
                                    }}
                                }}
                            }} catch (e) {{
                                console.warn('Audio mixing setup warning:', e);
                            }}
                        }}

                        function switchCameraDevice() {{
                            if (dualMediaStream) {{
                                toggleCamera().then(() => toggleCamera());
                            }}
                        }}

                        function switchMicDevice() {{
                            if (dualMediaStream) {{
                                toggleCamera().then(() => toggleCamera());
                            }}
                        }}

                        function toggleMirrorCam() {{
                            dualIsMirror = !dualIsMirror;
                            const btn = document.getElementById('btnToggleMirror');
                            if (btn) btn.innerHTML = '🪞 Mirror Kamera: ' + (dualIsMirror ? 'ON' : 'OFF');
                        }}

                        function toggleMuteMic() {{
                            dualIsMicMuted = !dualIsMicMuted;
                            if (dualMicGain) {{
                                dualMicGain.gain.value = dualIsMicMuted ? 0 : 1.0;
                            }}
                            const btn = document.getElementById('btnToggleMic');
                            if (btn) {{
                                btn.innerHTML = dualIsMicMuted ? '🔇 Mic: MUTE' : '🎙️ Mic: ON';
                                btn.style.setProperty('--shimmer-color', dualIsMicMuted ? '#ff9500' : '#25f4ee');
                            }}
                        }}

                        function startDualStream() {{
                            if (!isDualScreenActive || !dualMediaStream) {{
                                alert('Harap aktifkan Kamera & Mic terlebih dahulu sebelum mulai push stream!');
                                return;
                            }}

                            const canvas = document.getElementById('dualCompositeCanvas');
                            if (!canvas) return;

                            const stream = canvas.captureStream(30);
                            if (dualAudioDest) {{
                                if (dualAudioDest.stream.getAudioTracks().length === 0 && dualAudioCtx) {{
                                    try {{
                                        const osc = dualAudioCtx.createOscillator();
                                        const silent = dualAudioCtx.createGain();
                                        silent.gain.value = 0.0001;
                                        osc.connect(silent);
                                        silent.connect(dualAudioDest);
                                        osc.start();
                                    }} catch(e) {{}}
                                }}
                                if (dualAudioDest.stream.getAudioTracks().length > 0) {{
                                    stream.addTrack(dualAudioDest.stream.getAudioTracks()[0]);
                                }}
                            }}

                            const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                            const wsUrl = proto + '//' + window.location.host + '/ws/stream';

                            console.log('[DualStream] Menghubungkan ke WebSocket pipe:', wsUrl);
                            dualStreamWs = new WebSocket(wsUrl);
                            dualStreamWs.binaryType = 'arraybuffer';

                            dualStreamWs.onmessage = function(ev) {{
                                try {{
                                    const msg = JSON.parse(ev.data);
                                    if (msg.status === 'error') {{
                                        alert('⚠️ Dual-Screen Pipe Error: ' + msg.message);
                                        stopDualStream();
                                    }} else if (msg.status === 'ready') {{
                                        console.log('[DualStream] Server pipe siap menerima data.');
                                    }}
                                }} catch(e) {{}}
                            }};

                            dualStreamWs.onopen = function() {{
                                console.log('[DualStream] WebSocket terhubung! Memulai MediaRecorder 30 FPS...');
                                let mime = 'video/webm;codecs=vp8,opus';
                                if (!MediaRecorder.isTypeSupported(mime)) {{
                                    mime = 'video/webm;codecs=vp8';
                                }}
                                if (!MediaRecorder.isTypeSupported(mime)) {{
                                    mime = 'video/webm';
                                }}
                                try {{
                                    dualMediaRecorder = new MediaRecorder(stream, {{
                                        mimeType: mime,
                                        videoBitsPerSecond: 1500000,
                                        audioBitsPerSecond: 128000
                                    }});
                                }} catch (err) {{
                                    dualMediaRecorder = new MediaRecorder(stream);
                                }}

                                dualMediaRecorder.ondataavailable = async function(e) {{
                                    if (e.data && e.data.size > 0 && dualStreamWs && dualStreamWs.readyState === WebSocket.OPEN) {{
                                        const buffer = await e.data.arrayBuffer();
                                        dualStreamWs.send(buffer);
                                    }}
                                }};

                                dualMediaRecorder.start(500);
                                dualIsStreaming = true;

                                const btnStart = document.getElementById('btnStartDualStream');
                                const btnStop = document.getElementById('btnStopDualStream');
                                if (btnStart) btnStart.style.display = 'none';
                                if (btnStop) btnStop.style.display = 'inline-block';
                                const badge = document.getElementById('dualStatusBadge');
                                if (badge) {{
                                    badge.innerText = '🔴 STREAMING TIKTOK (Dual-Screen Pipeline)';
                                    badge.style.color = '#ff3b30';
                                    badge.style.borderColor = '#ff3b30';
                                    badge.style.background = 'rgba(255, 59, 48, 0.25)';
                                    badge.style.boxShadow = '0 0 15px rgba(255, 59, 48, 0.5)';
                                }}
                            }};

                            dualStreamWs.onclose = function() {{
                                console.log('[DualStream] WebSocket pipe terputus.');
                                stopDualStream();
                            }};

                            dualStreamWs.onerror = function(err) {{
                                console.error('[DualStream] WebSocket error:', err);
                                alert('Gagal menghubungkan WebSocket stream. Pastikan membuka dashboard via HTTPS.');
                                stopDualStream();
                            }};
                        }}

                        function stopDualStream() {{
                            dualIsStreaming = false;
                            if (dualMediaRecorder && dualMediaRecorder.state !== 'inactive') {{
                                try {{ dualMediaRecorder.stop(); }} catch (e) {{}}
                            }}
                            if (dualStreamWs) {{
                                try {{ dualStreamWs.close(); }} catch (e) {{}}
                                dualStreamWs = null;
                            }}

                            const btnStart = document.getElementById('btnStartDualStream');
                            const btnStop = document.getElementById('btnStopDualStream');
                            if (btnStart) btnStart.style.display = 'inline-block';
                            if (btnStop) btnStop.style.display = 'none';

                            const badge = document.getElementById('dualStatusBadge');
                            if (badge) {{
                                badge.innerText = isDualScreenActive ? '🟢 Preview Dual-Screen Aktif' : '⚪ Standby (Kamera Nonaktif)';
                                badge.style.color = isDualScreenActive ? '#34c759' : '#a1a1aa';
                                badge.style.borderColor = isDualScreenActive ? '#34c759' : '#71717a';
                                badge.style.background = isDualScreenActive ? 'rgba(52, 199, 89, 0.2)' : 'rgba(113, 113, 122, 0.2)';
                                badge.style.boxShadow = 'none';
                            }}
                        }}

                        window.addEventListener('DOMContentLoaded', initDualScreenStudio);
                    </script>
                </head>
                <body>
                    <div class="container">
                        <h1>TikTok Live Lightweight Dashboard</h1>
                        <p>Arsitektur Ringan & Stabil (6 Slot Video .webm / .mp4 & Pemicu Gift)</p>
                        
                        <div style="margin: 15px 0;">
                            {account_status_banner}
                        </div>

                        {live_status_html}

                        {live_listener_card_html}

                        <!-- Konfigurasi Akun TikTok Panel -->
                        <div class="shine-border-card" style="animation-delay: 0.5s;">
                            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:12px;">
                                <h3 style="margin:0;">Konfigurasi Akun TikTok</h3>
                                <div style="display:flex; align-items:center; gap:8px;">
                                    <span style="background:{'rgba(37, 244, 238, 0.15)' if (last_via == 'WARP_PROXY' and last_ip) else 'rgba(113, 113, 122, 0.2)'}; border:1px solid {'#25f4ee' if (last_via == 'WARP_PROXY' and last_ip) else '#71717a'}; color:{'#25f4ee' if (last_via == 'WARP_PROXY' and last_ip) else '#aaa'}; padding:4px 12px; border-radius:14px; font-size:12px; font-weight:bold;">
                                        {'🛡️ Session Route: WARP Proxy (' + last_ip + ') - IP Locked' if (last_via == 'WARP_PROXY' and last_ip) else ('🌐 Session Route: ' + last_via + ' (' + last_ip + ')' if last_ip else '⚪ Belum Diverifikasi')}
                                    </span>
                                </div>
                            </div>
                            <p style="color: #a1a1aa; font-size: 13px;">Masukkan Username TikTok (contoh: @username) dan Cookies JSON Anda. Seluruh pengecekan profil, validasi session, dan streaming live WAJIB melewati WARP Proxy (Anti-IP Belang).</p>
                            <form action="/save-account-config" method="POST">
                                <label style="display: block; font-size: 13px; font-weight: bold; margin-bottom: 6px; color: #fff;">Username TikTok:</label>
                                <input type="text" name="username" value="{tiktok_username}" placeholder="@username_tiktok" required /><br/><br/>
                                
                                <label style="display: block; font-size: 13px; font-weight: bold; margin-bottom: 6px; color: #fff;">Cookies JSON (cookies.json):</label>
                                <textarea name="cookies" rows="5" placeholder="Paste array cookies JSON di sini..." required>{cookies_content}</textarea><br/><br/>
                                
                                <div style="display:flex; gap:12px; flex-wrap:wrap; align-items:center;">
                                    <button type="submit" class="shimmer-btn" style="--shimmer-color: #25f4ee; --bg: rgba(37, 244, 238, 0.15);">💾 Simpan & Validasi Akun</button>
                                    <button type="button" id="btnValidateAcc" onclick="triggerAccountValidation(event)" class="shimmer-btn" style="--shimmer-color: #34c759; --bg: rgba(52, 199, 89, 0.15);">🔍 Validasi Session Akun (via WARP Proxy)</button>
                                </div>
                            </form>
                        </div>

                        <div class="shine-border-card" style="animation-delay: 1s;">
                            <h3 style="margin-top:0;">Manajemen Video & Pemicu Gift (Slot 1 - 6)</h3>
                            <p style="color: #a1a1aa; font-size: 13px;">Format .webm / .mp4. Slot 1 standby loop, Slot 2 s.d. 6 reaktif terhadap gift TikTok.</p>
                            <div>
                                {video_slots_html}
                            </div>
                        </div>

                        {overlay_card_html}

                        {vpn_card_html}

                        <hr/>
                        <h3 style="text-align: center;">Kontrol Live Streaming</h3>
                        <div style="display: flex; justify-content: center; align-items: center; flex-wrap: wrap; gap: 14px; margin: 0 auto; text-align: center; margin-bottom: 20px;">
                            <form action="/start-stream" method="POST" style="margin:0;" onsubmit="return validateStartLive();">
                                <button type="submit" class="shimmer-btn" style="--shimmer-color: #ffb800; --bg: rgba(255, 184, 0, 0.15);">Start Live (Ambil RTMP & Push Video)</button>
                            </form>
                            <form action="/stop-stream" method="POST" style="margin:0;">
                                <button type="submit" class="shimmer-btn" style="--shimmer-color: #ff3b30; --bg: rgba(255, 59, 48, 0.15);">Stop Live (Matikan Stream)</button>
                            </form>
                        </div>

                        <!-- Panel Dual-Screen Live Camera + Interactive Cat Studio (Opsi A) -->
                        <div class="shine-border-card" style="margin: 24px 0; border: 1px solid rgba(37, 244, 238, 0.4); background: #121218;">
                            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 16px;">
                                <div>
                                    <h3 style="margin: 0; color: #25f4ee; font-size: 18px; display: flex; align-items: center; gap: 8px;">
                                        <span>🎥</span> Dual-Screen Live Studio (Host Camera + Video Kucing)
                                    </h3>
                                    <p style="color: #a1a1aa; font-size: 13px; margin: 4px 0 0 0;">
                                        Kamera wajah & mic host digabungkan di layar bawah secara real-time. Video reaksi kucing diputar di layar atas (Rasio vertikal 9:16). Bebas lag & tanpa restart koneksi RTMP TikTok saat gift masuk.
                                    </p>
                                </div>
                                <div style="display: flex; gap: 8px; align-items: center;">
                                    <span id="dualStatusBadge" style="background: rgba(113, 113, 122, 0.2); color: #a1a1aa; border: 1px solid #71717a; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold;">
                                        ⚪ Standby (Kamera Nonaktif)
                                    </span>
                                </div>
                            </div>

                            <!-- Hidden Video Elements for Compositing -->
                            <video id="dualCatVideo" src="/video-stream?name=video_1.mp4" playsinline loop muted crossorigin="anonymous" style="display:none;"></video>
                            <video id="dualCamVideo" playsinline autoplay muted style="display:none;"></video>

                            <!-- Studio Grid: Left Preview Canvas (9:16), Right Controls -->
                            <div style="display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; justify-content: center;">
                                
                                <!-- Live Preview Stage (9:16 Ratio) -->
                                <div style="flex: 0 0 auto; width: 300px; height: 533px; background: #000; border-radius: 16px; border: 2px solid #27272a; position: relative; overflow: hidden; box-shadow: 0 10px 30px rgba(0,0,0,0.8), 0 0 20px rgba(37,244,238,0.15);">
                                    <canvas id="dualCompositeCanvas" width="720" height="1280" style="width: 100%; height: 100%; object-fit: cover; display: block;"></canvas>
                                    
                                    <!-- Floating Mini HUD overlay -->
                                    <div style="position: absolute; top: 12px; left: 12px; display: flex; gap: 6px; align-items: center; pointer-events: none;">
                                        <span id="dualCurrentSlotBadge" style="background: rgba(0,0,0,0.7); color: #34c759; border: 1px solid #34c759; padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: bold;">
                                            🐱 Slot 1 (Standby)
                                        </span>
                                    </div>
                                    <div style="position: absolute; bottom: 12px; right: 12px; display: flex; align-items: center; gap: 6px; background: rgba(0,0,0,0.7); padding: 3px 8px; border-radius: 6px; border: 1px solid #333; pointer-events: none;">
                                        <span style="font-size: 11px; color: #25f4ee;">🎙️</span>
                                        <div id="dualVuBar" style="width: 40px; height: 6px; background: #222; border-radius: 3px; overflow: hidden;">
                                            <div id="dualVuLevel" style="width: 0%; height: 100%; background: #34c759; transition: width 0.08s ease;"></div>
                                        </div>
                                    </div>
                                </div>

                                <!-- Controls Column -->
                                <div style="flex: 1; min-width: 280px; display: flex; flex-direction: column; gap: 14px;">
                                    
                                    <!-- Device Selectors -->
                                    <div style="background: #181820; padding: 14px; border-radius: 10px; border: 1px solid #27272a;">
                                        <h4 style="margin: 0 0 10px 0; font-size: 14px; color: #fff;">⚙️ Pengaturan Perangkat Host</h4>
                                        
                                        <label style="display: block; font-size: 12px; color: #a1a1aa; margin-bottom: 4px;">Pilihan Kamera:</label>
                                        <select id="cameraSourceSelect" onchange="switchCameraDevice()" style="width: 100%; padding: 8px; background: #121216; color: #fff; border: 1px solid #3f3f46; border-radius: 6px; font-size: 13px; margin-bottom: 10px;">
                                            <option value="">Pilih Kamera...</option>
                                        </select>

                                        <label style="display: block; font-size: 12px; color: #a1a1aa; margin-bottom: 4px;">Pilihan Mikrofon:</label>
                                        <select id="micSourceSelect" onchange="switchMicDevice()" style="width: 100%; padding: 8px; background: #121216; color: #fff; border: 1px solid #3f3f46; border-radius: 6px; font-size: 13px;">
                                            <option value="">Pilih Mikrofon...</option>
                                        </select>
                                    </div>

                                    <!-- Action Buttons -->
                                    <div style="display: flex; flex-direction: column; gap: 10px;">
                                        <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                                            <button type="button" id="btnToggleCam" onclick="toggleCamera()" class="shimmer-btn" style="--shimmer-color: #34c759; --bg: rgba(52, 199, 89, 0.15); flex: 1; min-width: 140px; padding: 10px 14px; font-weight: bold;">
                                                📷 Aktifkan Kamera & Mic
                                            </button>
                                            <button type="button" id="btnToggleMirror" onclick="toggleMirrorCam()" class="shimmer-btn" style="--shimmer-color: #a07cfe; --bg: rgba(160, 124, 254, 0.15); padding: 10px 14px;">
                                                🪞 Mirror Kamera: ON
                                            </button>
                                            <button type="button" id="btnToggleMic" onclick="toggleMuteMic()" class="shimmer-btn" style="--shimmer-color: #25f4ee; --bg: rgba(37, 244, 238, 0.15); padding: 10px 14px;">
                                                🎙️ Mic: ON
                                            </button>
                                        </div>

                                        <!-- TikTok Live Streaming Actions -->
                                        <div style="background: rgba(37, 244, 238, 0.05); padding: 14px; border-radius: 10px; border: 1px solid rgba(37, 244, 238, 0.2); margin-top: 6px;">
                                            <div style="font-size: 13px; font-weight: bold; color: #25f4ee; margin-bottom: 6px;">🚀 Push Dual-Screen Langsung ke TikTok Live</div>
                                            <p style="font-size: 12px; color: #aaa; margin: 0 0 10px 0;">
                                                Kamera + Audio host & Video Kucing dikirim secara kontinu via WebSocket ke server FFmpeg dan di-push ke RTMPS TikTok.
                                            </p>
                                            <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                                                <button type="button" id="btnStartDualStream" onclick="startDualStream()" class="shimmer-btn" style="--shimmer-color: #ffb800; --bg: rgba(255, 184, 0, 0.18); flex: 1; font-weight: bold; padding: 12px 18px;">
                                                    🚀 Mulai Dual-Screen Stream (Push ke TikTok)
                                                </button>
                                                <button type="button" id="btnStopDualStream" onclick="stopDualStream()" class="shimmer-btn" style="--shimmer-color: #ff3b30; --bg: rgba(255, 59, 48, 0.18); padding: 12px 18px; display: none;">
                                                    ⏹️ Stop Dual-Screen Stream
                                                </button>
                                            </div>
                                        </div>

                                        <!-- HTTPS Guidance Info -->
                                        <div style="font-size: 11px; color: #71717a; line-height: 1.5; padding: 8px 10px; background: #121214; border-radius: 6px; border: 1px solid #222;">
                                            💡 <b>Akses Kamera Aman:</b> Dashboard ini dilindungi SSL/HTTPS pada port 8088 (<code style="color:#25f4ee;">https://staging.apologets.com:8088</code>). Akses kamera & mic diizinkan penuh oleh browser HP dan Laptop.
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Panel Simulasi Gift Event Interaktif -->
                        <div class="shine-border-card" style="margin: 20px 0; border: 1px solid rgba(52, 199, 89, 0.35); background: #16161d;">
                            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 12px;">
                                <div>
                                    <h3 style="margin: 0; color: #34c759; font-size: 17px; display: flex; align-items: center; gap: 8px;">
                                        <span>🎁</span> Simulasi Pemicu Gift Event (Slot 2 s.d. 6)
                                    </h3>
                                    <p style="color: #a1a1aa; font-size: 13px; margin: 4px 0 0 0;">
                                        Uji coba pemicu video reaksi secara instan tanpa reload halaman. Klik beruntun untuk mengisi antrean pemutaran video.
                                    </p>
                                </div>
                                <div>
                                    <button type="button" id="btn-reset-standby" class="shimmer-btn" style="--shimmer-color: #007aff; --bg: rgba(0, 122, 255, 0.15); padding: 8px 18px; font-size: 13px; font-weight: 600;" onclick="resetStandby(event)">
                                        🔄 Reset ke Standby (Slot 1)
                                    </button>
                                </div>
                            </div>

                            <!-- Live Feedback Status Banner -->
                            <div id="sim-status-banner" style="display: none; padding: 10px 14px; border-radius: 6px; font-size: 13px; margin-bottom: 14px; border: 1px solid #34c759; background: rgba(52, 199, 89, 0.1); color: #34c759; transition: all 0.3s ease;">
                            </div>

                            <div style="display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-start; align-items: center;">
                                {simulation_buttons_html}
                            </div>
                        </div>
                    </div>

                    <!-- Floating Modal for Gift Selector -->
                    <div id="giftModal" class="modal-overlay">
                        <div class="modal-content">
                            <div class="modal-header">
                                <h3 style="margin:0; font-size:16px; color:#25f4ee;">Pilih Pemicu Gift TikTok</h3>
                                <button type="button" class="shimmer-btn" style="--shimmer-color: #ff3b30; --bg: rgba(255, 59, 48, 0.2); padding: 4px 12px; font-size: 12px;" onclick="closeGiftModal()">Tutup ✕</button>
                            </div>
                            <div class="modal-body">
                                <iframe id="giftIframe" style="width:100%; height:320px; border:none; background:transparent;" scrolling="yes"></iframe>
                            </div>
                        </div>
                    </div>
                </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
            
        elif parsed_url.path == '/gift-selector':
            query = urllib.parse.parse_qs(parsed_url.query)
            slot = query.get('slot', ['2'])[0]
            gift_config = load_gift_config()
            current_selected = gift_config.get(str(slot), "rose")
            
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            
            gifts_rows = ""
            for g in GIFTS_CATALOG:
                is_sel = "checked" if g["id"] == current_selected else ""
                bg_sel = "background:#22222c; border-color:#a07cfe;" if g["id"] == current_selected else "background:#16161d; border-color:rgba(255, 255, 255, 0.12);"
                idr_formatted = f"Rp {g['idr']:,}".replace(",", ".")
                
                gifts_rows += f"""
                <label class="gift-item" style="display:flex; align-items:center; justify-content:space-between; padding:10px 14px; margin:6px 0; border-radius:8px; width:100%; box-sizing:border-box; border:1px solid; {bg_sel} cursor:pointer; text-align:left; transition: background 0.2s ease, border-color 0.2s ease;">
                    <div style="display:flex; align-items:center; gap:12px;">
                        <span style="font-size:24px;">{g['icon']}</span>
                        <div>
                            <div style="color:#fff; font-size:14px; font-weight:bold;">{g['name']}</div>
                            <div style="color:#a1a1aa; font-size:12px;">{g['coins']} Koin</div>
                        </div>
                    </div>
                    <div style="text-align:right; display:flex; align-items:center; gap:12px;">
                        <span style="color:#34c759; font-size:13px; font-weight:bold;">{idr_formatted}</span>
                        <input type="radio" name="gift_id" value="{g['id']}" {is_sel} onchange="saveGift('{slot}', '{g['id']}')" style="transform: scale(1.2); cursor: pointer;" />
                    </div>
                </label>
                """
                
            iframe_html = f"""<!DOCTYPE html>
            <html>
                <head>
                    <meta charset="utf-8">
                    <style>
                        .gift-item:hover {{
                            background: #22222c !important;
                            border-color: #a07cfe !important;
                        }}
                        body {{ font-family: sans-serif; background: #0f0f11; color: #fff; margin: 0; padding: 0; }}
                        ::-webkit-scrollbar {{ width: 6px; }}
                        ::-webkit-scrollbar-thumb {{ background: #3f3f46; border-radius: 3px; }}
                    </style>
                    <script>
                        function saveGift(slot, giftId) {{
                            fetch('/save-gift-selection?slot=' + slot + '&gift=' + giftId)
                                .then(res => res.text())
                                .then(data => {{
                                    console.log("Saved:", data);
                                }});
                        }}
                    </script>
                </head>
                <body>
                    {gifts_rows}
                </body>
            </html>
            """
            self.wfile.write(iframe_html.encode('utf-8'))
            
        elif parsed_url.path == '/save-gift-selection':
            query = urllib.parse.parse_qs(parsed_url.query)
            slot = query.get('slot', [''])[0]
            gift_id = query.get('gift', [''])[0]
            if slot and gift_id:
                cfg = load_gift_config()
                cfg[str(slot)] = gift_id
                save_gift_config(cfg)
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
            
        elif parsed_url.path == '/delete-video':
            query = urllib.parse.parse_qs(parsed_url.query)
            filename = os.path.basename(query.get('name', [''])[0])
            slot = query.get('slot', [''])[0]
            if filename:
                filepath = os.path.realpath(os.path.join(VIDEOS_DIR, filename))
                real_videos_dir = os.path.realpath(VIDEOS_DIR)
                if filepath.startswith(real_videos_dir) and os.path.exists(filepath):
                    os.remove(filepath)
            
            meta = load_video_meta()
            if slot and str(slot) in meta:
                del meta[str(slot)]
                save_video_meta(meta)
            else:
                to_del = [s for s, v in meta.items() if v.get("filename") == filename]
                for s in to_del:
                    del meta[s]
                if to_del:
                    save_video_meta(meta)

            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            
        elif parsed_url.path == '/update-video-name':
            query = urllib.parse.parse_qs(parsed_url.query)
            slot = query.get('slot', [''])[0]
            new_name = query.get('name', [''])[0].strip()
            if slot and new_name:
                meta = load_video_meta()
                if str(slot) not in meta:
                    meta[str(slot)] = {}
                meta[str(slot)]["original_name"] = new_name
                save_video_meta(meta)
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
            return
            
        elif parsed_url.path == '/video-stream':
            query = urllib.parse.parse_qs(parsed_url.query)
            filename = os.path.basename(query.get('name', [''])[0])
            if not filename:
                self.send_response(400)
                self.end_headers()
                return
            filepath = os.path.realpath(os.path.join(VIDEOS_DIR, filename))
            real_videos_dir = os.path.realpath(VIDEOS_DIR)
            if not filepath.startswith(real_videos_dir) or not os.path.exists(filepath):
                self.send_response(404)
                self.end_headers()
                return

            mime = "video/mp4" if filename.lower().endswith(".mp4") else "video/webm"
            file_size = os.path.getsize(filepath)
            
            range_header = self.headers.get('Range')
            if range_header:
                bytes_range = range_header.replace('bytes=', '').strip()
                parts = bytes_range.split('-')
                start = int(parts[0]) if parts[0] else 0
                end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
                if start >= file_size:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{file_size}')
                    self.end_headers()
                    return
                end = min(end, file_size - 1)
                content_length = end - start + 1
                
                self.send_response(206)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                self.send_header('Content-Length', str(content_length))
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
                self.end_headers()
                
                if head_only:
                    return

                with open(filepath, 'rb') as f:
                    f.seek(start)
                    chunk_size = 64 * 1024
                    bytes_remaining = content_length
                    while bytes_remaining > 0:
                        to_read = min(chunk_size, bytes_remaining)
                        data = f.read(to_read)
                        if not data:
                            break
                        try:
                            self.wfile.write(data)
                        except (BrokenPipeError, ConnectionResetError):
                            break
                        bytes_remaining -= len(data)
            else:
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(file_size))
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
                self.end_headers()
                
                if head_only:
                    return

                with open(filepath, 'rb') as f:
                    try:
                        shutil.copyfileobj(f, self.wfile)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
            return

        elif parsed_url.path == '/overlay-preview':
            if os.path.exists(GUIDE_OVERLAY_FILE):
                try:
                    with open(GUIDE_OVERLAY_FILE, 'rb') as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-type", "image/png")
                    self.send_header("Content-length", str(len(data)))
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(data)
                    return
                except Exception:
                    self.send_response(500)
                    self.end_headers()
                    return
            else:
                self.send_response(404)
                self.end_headers()
                return

        elif parsed_url.path == '/avatar-img':
            query = urllib.parse.parse_qs(parsed_url.query)
            char_id = query.get('char_id', [''])[0]
            if char_id in ["1", "2", "3", "4"]:
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    p = os.path.join(AVATARS_DIR, f"char_{char_id}{ext}")
                    if os.path.exists(p):
                        mime = "image/png"
                        if ext in (".jpg", ".jpeg"):
                            mime = "image/jpeg"
                        elif ext == ".webp":
                            mime = "image/webp"
                        try:
                            with open(p, 'rb') as f:
                                data = f.read()
                            self.send_response(200)
                            self.send_header("Content-type", mime)
                            self.send_header("Content-length", str(len(data)))
                            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                            self.end_headers()
                            if not head_only:
                                self.wfile.write(data)
                            return
                        except Exception:
                            pass
            self.send_response(404)
            self.end_headers()
            return

        elif parsed_url.path == '/delete-avatar':
            query = urllib.parse.parse_qs(parsed_url.query)
            char_id = query.get('char_id', [''])[0]
            if char_id in ["1", "2", "3", "4"]:
                real_avatars_dir = os.path.realpath(AVATARS_DIR)
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    p = os.path.realpath(os.path.join(AVATARS_DIR, f"char_{char_id}{ext}"))
                    if p.startswith(real_avatars_dir) and os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                cfg = load_character_config()
                if "characters" in cfg and char_id in cfg["characters"]:
                    cfg["characters"][char_id]["avatar"] = ""
                    save_character_config(cfg)
                    generate_guide_overlay(cfg)
                    refresh_active_stream_overlay()
            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            return
            
        elif parsed_url.path == '/health':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            status_data = {
                "status": "ok",
                "streamingActive": get_ffmpeg_pid() is not None,
                "ffmpegPid": get_ffmpeg_pid(),
                "uptime": time.time()
            }
            self.wfile.write(json.dumps(status_data).encode('utf-8'))
        elif parsed_url.path == '/api/listener-status':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with queue_lock:
                q_len = len(gift_queue)
                recent_list = list(recent_gift_logs)[:5]
            egress_ip, is_warp, is_wp_running = get_current_egress_info()
            vpn_cfg = load_vpn_config()

            with watchdog_lock:
                wd_state = watchdog_status.get("state", "IDLE")
                reconnects = watchdog_status.get("reconnect_count", 0)
                last_chk = watchdog_status.get("last_check_time")
                last_err = watchdog_status.get("last_error")
                is_recon = watchdog_status.get("is_reconnecting", False)
                emerg = watchdog_status.get("emergency_shutdown", False)

            sec_ago = max(0, int(time.time() - last_chk)) if last_chk else None

            account_cfg = load_account_config()
            resp_data = {
                "status": "ok",
                "listener": listener_status,
                "queue_length": q_len,
                "streamingActive": get_ffmpeg_pid() is not None,
                "currentSlot": stream_metadata.get("currentSlot", 1),
                "latest_gift": latest_gift_event,
                "gift_seq": global_gift_seq,
                "recent_gifts": recent_list,
                "account": {
                    "username": account_cfg.get("username", ""),
                    "last_verified_ip": account_cfg.get("last_verified_ip", ""),
                    "last_verified_via": account_cfg.get("last_verified_via", ""),
                    "last_verified_time": account_cfg.get("last_verified_str", ""),
                    "session_valid": account_cfg.get("session_valid", False)
                },
                "vpn": {
                    "active": is_wp_running,
                    "warp": is_warp,
                    "egress_ip": egress_ip,
                    "auto_vpn": vpn_cfg.get("auto_vpn", True)
                },
                "watchdog": {
                    "state": wd_state,
                    "reconnect_count": reconnects,
                    "last_check_ago": sec_ago,
                    "last_error": last_err,
                    "is_reconnecting": is_recon,
                    "emergency_shutdown": emerg
                }
            }
            self.wfile.write(json.dumps(resp_data).encode('utf-8'))
        elif parsed_url.path == '/validate-account':
            try:
                res = validate_tiktok_session()
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e), "via": "ERROR"}).encode('utf-8'))
        elif parsed_url.path in ('/simulate-gift', '/switch-slot'):
            return self.do_POST()
        else:
            self.send_response(404)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        
        if parsed_url.path in ('/api/live-gift', '/api/listener-status'):
            client_ip = self.client_address[0]
            if client_ip not in ("127.0.0.1", "::1", "localhost"):
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Forbidden: local access only"}')
                return

        if parsed_url.path in ('/api/tikfinity-gift', '/api/webhook/gift'):
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            event_data = data.get("data") if isinstance(data.get("data"), dict) else data
            gname = event_data.get("gift_name") or event_data.get("giftName") or event_data.get("gift") or ""
            gid = str(event_data.get("gift_id") or event_data.get("giftId") or "")
            sender = event_data.get("name") or event_data.get("nickname") or event_data.get("uniqueId") or "Penonton"
            rcount = int(event_data.get("repeat_count") or event_data.get("repeatCount") or event_data.get("count") or 1)
            
            res = handle_incoming_gift(gname, gid, sender, rcount)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "result": res}).encode('utf-8'))
            return

        if parsed_url.path == '/api/live-gift':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            gname = data.get("giftName", "")
            gid = str(data.get("giftId", ""))
            sender = data.get("nickname") or data.get("uniqueId") or "Penonton"
            rcount = int(data.get("repeatCount", 1))

            listener_status["totalGiftsReceived"] += 1
            listener_status["lastPing"] = time.time()

            res = handle_incoming_gift(gname, gid, sender, rcount)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "result": res}).encode('utf-8'))
            return

        if parsed_url.path == '/api/listener-status':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                listener_status["connected"] = bool(data.get("connected", False))
                if data.get("roomId"):
                    listener_status["roomId"] = str(data.get("roomId"))
                if data.get("username"):
                    listener_status["username"] = str(data.get("username"))
                if data.get("error"):
                    listener_status["lastError"] = str(data.get("error"))
                listener_status["lastPing"] = time.time()
            except Exception as e:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "listener": listener_status}).encode('utf-8'))
            return

        if parsed_url.path == '/login':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            parsed = urllib.parse.parse_qs(body)
            password = parsed.get('password', [''])[0]
            if password == "191919":
                self.send_response(303)
                self.send_header('Set-Cookie', 'auth_token=verified_191919; Path=/; Max-Age=604800; HttpOnly')
                self.send_header('Location', '/')
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body style='background:#0f0f11; color:#ff3b30; text-align:center; padding-top:50px; font-family:sans-serif;'><h2>Password Salah!</h2><br/><a href='/' style='color:#25f4ee;'>Coba Lagi</a></body></html>")
            return

        if not self.check_auth():
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            return

        if parsed_url.path == '/validate-account':
            try:
                res = validate_tiktok_session()
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e), "via": "ERROR"}).encode('utf-8'))
            return

        if parsed_url.path == '/save-account-config':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            parsed = urllib.parse.parse_qs(body)
            username = parsed.get('username', [''])[0].strip()
            cookies_raw = parsed.get('cookies', [''])[0].strip()
            
            try:
                # 1. Simpan username & cookies
                save_account_config({"username": username})
                if cookies_raw:
                    parsed_json = json.loads(cookies_raw)
                    with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                        json.dump(parsed_json, f, indent=2)

                # 2. Lakukan validasi session TikTok via Centralized Gateway WARP Proxy
                val_res = validate_tiktok_session(username)
                print(f"[SaveAccountConfig] Hasil validasi akun via {val_res.get('via')} ({val_res.get('egress_ip')}): {val_res.get('message')}")
                
                # Restart listener agar memuat sesi/username baru
                ensure_live_listener(force_restart=True)

                self.send_response(303)
                self.send_header('Location', '/')
                self.end_headers()
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body style='background:#0f0f11; color:#fff; text-align:center; padding-top:50px; font-family:sans-serif;'><h2 style='color:#ff3b30;'>Gagal Menyimpan / Memvalidasi Akun: {str(e)}</h2><br/><a href='/' style='color:#25f4ee;'>Kembali</a></body></html>".encode('utf-8'))
            return

        if parsed_url.path == '/upload-video':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 100 * 1024 * 1024:
                    self.send_response(413)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<html><body style='background:#0f0f11; color:#ff3b30; text-align:center; padding-top:50px;'><h2>File terlalu besar! Maksimum 100 MB.</h2><br/><a href='/' style='color:#25f4ee;'>Kembali</a></body></html>")
                    return

                content_type = self.headers.get('Content-Type', '')
                body = self.rfile.read(content_length)
                
                headers = f"Content-Type: {content_type}\n\n".encode('utf-8')
                msg = email.parser.BytesParser().parsebytes(headers + body)
                
                slot = "1"
                file_data = None
                orig_filename = ""
                
                if msg.is_multipart():
                    for part in msg.walk():
                        cd = part.get("Content-Disposition", "")
                        if 'name="slot"' in cd:
                            payload = part.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                slot = payload.decode('utf-8', errors='ignore').strip()
                        elif 'name="videofile"' in cd:
                            fn_param = part.get_filename()
                            if fn_param:
                                orig_filename = fn_param
                            payload = part.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                file_data = payload
                
                if slot not in ("1", "2", "3", "4", "5", "6"):
                    slot = "1"

                if file_data:
                    ext = ".webm"
                    clean_orig_name = os.path.basename(orig_filename).strip() if orig_filename else ""
                    if clean_orig_name.lower().endswith('.mp4'):
                        ext = ".mp4"
                    
                    for old_ext in (".webm", ".mp4"):
                        old_p = os.path.join(VIDEOS_DIR, f"video_{slot}{old_ext}")
                        if os.path.exists(old_p):
                            os.remove(old_p)
                            
                    saved_filename = f"video_{slot}{ext}"
                    filepath = os.path.join(VIDEOS_DIR, saved_filename)
                    with open(filepath, 'wb') as f:
                        f.write(file_data)

                    meta = load_video_meta()
                    meta[str(slot)] = {
                        "filename": saved_filename,
                        "original_name": clean_orig_name or saved_filename,
                        "uploaded_at": int(time.time()),
                        "size_bytes": len(file_data)
                    }
                    save_video_meta(meta)
            except Exception as e:
                print(f"[Upload Error] {e}")

            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()

        elif parsed_url.path == '/upload-avatar':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 10 * 1024 * 1024:
                    self.send_response(413)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<html><body style='background:#0f0f11; color:#ff3b30; text-align:center; padding-top:50px;'><h2>Avatar terlalu besar! Maksimum 10 MB.</h2><br/><a href='/' style='color:#25f4ee;'>Kembali</a></body></html>")
                    return

                content_type = self.headers.get('Content-Type', '')
                body = self.rfile.read(content_length)
                
                headers = f"Content-Type: {content_type}\n\n".encode('utf-8')
                msg = email.parser.BytesParser().parsebytes(headers + body)
                
                char_id = "1"
                file_data = None
                orig_filename = ""
                
                if msg.is_multipart():
                    for part in msg.walk():
                        cd = part.get("Content-Disposition", "")
                        if 'name="char_id"' in cd:
                            payload = part.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                char_id = payload.decode('utf-8', errors='ignore').strip()
                        elif 'name="avatarfile"' in cd:
                            fn_param = part.get_filename()
                            if fn_param:
                                orig_filename = fn_param
                            payload = part.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                file_data = payload
                
                if char_id not in ("1", "2", "3", "4"):
                    char_id = "1"
                
                if file_data and char_id in ["1", "2", "3", "4"]:
                    ext = ".png"
                    clean_orig = os.path.basename(orig_filename).strip().lower() if orig_filename else ""
                    if clean_orig.endswith('.jpg') or clean_orig.endswith('.jpeg'):
                        ext = ".jpg"
                    elif clean_orig.endswith('.webp'):
                        ext = ".webp"
                    
                    for old_ext in (".png", ".jpg", ".jpeg", ".webp"):
                        old_p = os.path.join(AVATARS_DIR, f"char_{char_id}{old_ext}")
                        if os.path.exists(old_p):
                            try:
                                os.remove(old_p)
                            except Exception:
                                pass
                                
                    saved_filename = f"char_{char_id}{ext}"
                    filepath = os.path.join(AVATARS_DIR, saved_filename)
                    with open(filepath, 'wb') as f:
                        f.write(file_data)
                    
                    cfg = load_character_config()
                    if "characters" not in cfg:
                        cfg["characters"] = {}
                    if char_id not in cfg["characters"]:
                        cfg["characters"][char_id] = {"name": f"Karakter {char_id}"}
                    cfg["characters"][char_id]["avatar"] = f"uploads/avatars/{saved_filename}"
                    save_character_config(cfg)
                    generate_guide_overlay(cfg)
                    refresh_active_stream_overlay()
            except Exception as e:
                print(f"[Upload Avatar Error] {e}")

            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()

        elif parsed_url.path == '/save-character-mapping':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8')
                parsed = urllib.parse.parse_qs(body)
                
                overlay_enabled = parsed.get('overlay_enabled', ['off'])[0] in ['on', 'true', '1']
                try:
                    pos_x = int(parsed.get('pos_x', [60])[0])
                except Exception:
                    pos_x = 60
                try:
                    pos_y = int(parsed.get('pos_y', [120])[0])
                except Exception:
                    pos_y = 120
                    
                cfg = load_character_config()
                cfg['overlay_enabled'] = overlay_enabled
                cfg['position'] = {'x': pos_x, 'y': pos_y}
                
                mappings = []
                for r in range(5):
                    slot_num = r + 2
                    gift_id = parsed.get(f'row_{r}_gift', ['rose'])[0]
                    char_ids = []
                    for cid in ["1", "2", "3", "4"]:
                        if parsed.get(f'row_{r}_char_{cid}', [''])[0] in ['on', '1', 'true', cid]:
                            char_ids.append(cid)
                    mappings.append({
                        "slot": slot_num,
                        "gift_id": gift_id,
                        "character_ids": char_ids
                    })
                cfg['mappings'] = mappings
                save_character_config(cfg)
                generate_guide_overlay(cfg)
                refresh_active_stream_overlay()
            except Exception as e:
                print(f"[Save Mapping Error] {e}")

            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            return
            
        elif parsed_url.path == '/toggle-vpn':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else ""
                parsed = urllib.parse.parse_qs(body)
                action = parsed.get("action", ["toggle_auto"])[0]
                vpn_cfg = load_vpn_config()

                if action == "toggle_auto":
                    auto_val = parsed.get("auto_vpn", [""])[0]
                    if auto_val in ("on", "1", "true"):
                        vpn_cfg["auto_vpn"] = True
                    elif auto_val in ("off", "0", "false"):
                        vpn_cfg["auto_vpn"] = False
                    else:
                        vpn_cfg["auto_vpn"] = not vpn_cfg.get("auto_vpn", True)
                    save_vpn_config(vpn_cfg)
                elif action == "start":
                    start_wireproxy_vpn()
                    with watchdog_lock:
                        watchdog_status["emergency_shutdown"] = False
                        watchdog_status["is_reconnecting"] = False
                        watchdog_status["state"] = "PROTECTED" if (get_ffmpeg_pid() is not None) else "IDLE"
                elif action == "stop":
                    stop_wireproxy_vpn()
                    with watchdog_lock:
                        watchdog_status["state"] = "IDLE"
                        watchdog_status["is_reconnecting"] = False

                get_current_egress_info(force_refresh=True)
            except Exception as e:
                print(f"[ToggleVPN Error] {e}")

            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            return

        elif parsed_url.path == '/start-stream':
            try:
                account_cfg = load_account_config()
                if not account_cfg.get("username", "").strip():
                    raise Exception("Akun TikTok belum dikonfigurasi! Harap simpan konfigurasi akun terlebih dahulu.")
                
                # 0. Pastikan VPN aktif jika Auto-VPN dinyalakan
                vpn_cfg = load_vpn_config()
                if vpn_cfg.get("auto_vpn", True):
                    with watchdog_lock:
                        watchdog_status["emergency_shutdown"] = False

                    print("[StartStream] Auto-VPN aktif. Memastikan Wireproxy Cloudflare WARP (127.0.0.1:40000) menyala...")
                    vpn_ok, vpn_msg = start_wireproxy_vpn()
                    if not vpn_ok:
                        raise Exception(f"Gagal mengaktifkan VPN Wireproxy: {vpn_msg}. Koneksi dibatalkan demi keamanan IP.")
                    
                    # Verifikasi kesehatan VPN sebelum request RTMP
                    h_ok, h_ip, h_msg = check_vpn_health()
                    if not h_ok:
                        raise Exception(f"Health check VPN gagal sebelum Start Live: {h_msg}. Koneksi dibatalkan demi keamanan IP.")

                    with watchdog_lock:
                        watchdog_status["state"] = "PROTECTED"
                        watchdog_status["reconnect_count"] = 0
                        watchdog_status["last_check_time"] = time.time()
                        watchdog_status["last_error"] = None
                        watchdog_status["emergency_shutdown"] = False
                        watchdog_status["is_reconnecting"] = False

                    get_current_egress_info(force_refresh=True)
                else:
                    with watchdog_lock:
                        watchdog_status["state"] = "IDLE"
                        watchdog_status["reconnect_count"] = 0
                        watchdog_status["emergency_shutdown"] = False
                        watchdog_status["is_reconnecting"] = False

                # 1. Bersihkan proses ffmpeg lama & tutup room lama (jika ada) SEBELUM membuat room baru
                stop_ffmpeg_stream(close_tiktok_room=True)
                time.sleep(1)
                    
                # 2. Buat room baru di TikTok dan ambil kredensial RTMP (via WARP jika VPN aktif)
                rtmp_url, stream_key, raw_resp, room_id = fetch_tiktok_stream_key()
                
                # 3. Mulai push stream FFmpeg (via WARP jika VPN aktif)
                start_ffmpeg_stream(rtmp_url, stream_key, room_id, slot_num=1, loop=True)
                ensure_live_listener(force_restart=True)
                
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                
                egress_ip, is_warp, is_wp_running = get_current_egress_info()
                vpn_info_line = f"<p><b>VPN Tunnel:</b> <span style='color:#25f4ee;'>Cloudflare WARP Active (Egress: {egress_ip})</span></p>" if is_wp_running else "<p><b>VPN Tunnel:</b> <span style='color:#888;'>Direct (OFF)</span></p>"

                html = f"""<!DOCTYPE html>
                <html>
                    <body style='background:#0f0f11; color:#fff; text-align:center; padding-top:40px; font-family:sans-serif;'>
                        <h2 style='color:#34c759;'>Live Streaming Berhasil Dimulai!</h2>
                        <div style='background:#18181b; padding:20px; display:inline-block; border-radius:8px; text-align:left; border:1px solid #27272a; max-width:650px;'>
                            <p><b>Akun TikTok:</b> <span style='color:#25f4ee;'>@{account_cfg.get("username")}</span></p>
                            <p><b>Room ID:</b> <span style='color:#25f4ee;'>{room_id}</span></p>
                            <p><b>RTMP Server:</b> <span style='color:#25f4ee;'>{rtmp_url}</span></p>
                            <p><b>Stream Key:</b> <span style='color:#25f4ee;'>{stream_key[:10]}...</span></p>
                            {vpn_info_line}
                            <p><b>Engine Status:</b> <span style='color:#34c759;'>FFmpeg Lightweight Stream Active (PID: {get_ffmpeg_pid()})</span></p>
                            <p><b>Log FFmpeg:</b> <span style='color:#a1a1aa;'>Tercatat di ffmpeg_stream.log</span></p>
                            <p><b>Raw TikTok API Response:</b></p>
                            <pre style='background:#0f0f11; padding:10px; color:#25f4ee; overflow-x:auto; font-size:12px;'>{json.dumps(raw_resp, indent=2)}</pre>
                        </div>
                        <br/><br/>
                        <a href='/' class='shimmer-btn' style='color:#25f4ee; text-decoration:none;'>Kembali ke Dashboard</a>
                    </body>
                </html>
                """
                self.wfile.write(html.encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body style='background:#0f0f11; color:#fff; text-align:center; padding-top:50px; font-family:sans-serif;'><h2 style='color:#ff3b30;'>Gagal Start Live: {str(e)}</h2><br/><a href='/' style='color:#25f4ee;'>Kembali</a></body></html>".encode('utf-8'))
                
        elif parsed_url.path == '/stop-stream':
            try:
                # User secara eksplisit menghentikan live: matikan FFmpeg & tutup room di server TikTok
                stop_ffmpeg_stream(close_tiktok_room=True)
                with queue_lock:
                    gift_queue.clear()

                with watchdog_lock:
                    watchdog_status["is_reconnecting"] = False
                    watchdog_status["emergency_shutdown"] = False
                    vpn_cfg = load_vpn_config()
                    auto_vpn = vpn_cfg.get("auto_vpn", True)
                    if auto_vpn and is_wireproxy_running():
                        watchdog_status["state"] = "PROTECTED"
                        print("[StopStream] Stream dihentikan. Mempertahankan tunnel Wireproxy VPN aktif agar IP Egress WARP konsisten (Anti-IP Belang).")
                    else:
                        watchdog_status["state"] = "IDLE"

                ensure_live_listener(force_restart=True)
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                html = """<!DOCTYPE html>
                <html>
                    <body style='background:#0f0f11; color:#fff; text-align:center; padding-top:40px; font-family:sans-serif;'>
                        <h2 style='color:#34c759;'>Stream Berhasil Dihentikan (Stop Live)</h2>
                        <p style='color:#a1a1aa; font-size:14px; margin-top:8px;'>FFmpeg dihentikan & sinyal room finish telah dikirim ke server TikTok.</p>
                        <br/><br/>
                        <a href='/' style='color:#25f4ee; font-size:16px; text-decoration:none; background:#27272a; padding:10px 20px; border-radius:6px;'>Kembali ke Dashboard</a>
                    </body>
                </html>
                """
                self.wfile.write(html.encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body style='background:#0f0f11; color:#fff; text-align:center; padding-top:50px; font-family:sans-serif;'><h2>Gagal stop stream: {str(e)}</h2><br/><a href='/' style='color:#25f4ee;'>Kembali</a></body></html>".encode('utf-8'))
                
        elif parsed_url.path == '/simulate-gift':
            query_params = urllib.parse.parse_qs(parsed_url.query)
            slot_str = query_params.get("slot", [None])[0]
            if not slot_str:
                content_len = int(self.headers.get('Content-Length', 0))
                if content_len > 0:
                    try:
                        raw_body = self.rfile.read(content_len).decode('utf-8')
                        try:
                            body_json = json.loads(raw_body)
                            slot_str = str(body_json.get("slot", "2"))
                        except Exception:
                            body_qs = urllib.parse.parse_qs(raw_body)
                            slot_str = body_qs.get("slot", ["2"])[0]
                    except Exception:
                        slot_str = "2"

            try:
                target_slot = int(slot_str) if slot_str else 2
            except Exception:
                target_slot = 2

            if target_slot < 2 or target_slot > 6:
                target_slot = 2

            gift_cfg = load_gift_config()
            char_cfg = load_character_config()
            mappings = char_cfg.get("mappings", [])

            cfg_gift_id = gift_cfg.get(str(target_slot))
            if not cfg_gift_id:
                for m in mappings:
                    if m.get("slot") == target_slot:
                        cfg_gift_id = m.get("gift_id")
                        break
            if not cfg_gift_id:
                cfg_gift_id = "rose"

            gift_name = cfg_gift_id.replace("_", " ").title()
            gift_icon = "🎁"
            for g in GIFTS_CATALOG:
                if g["id"].lower() == cfg_gift_id.lower():
                    gift_name = g["name"]
                    gift_icon = g.get("icon", "🎁")
                    break

            sim_gift_id = ""
            for tid, cid in TIKTOK_GIFT_ID_MAP.items():
                if cid.lower() == cfg_gift_id.lower():
                    sim_gift_id = tid
                    break

            stream_active = (get_ffmpeg_pid() is not None)
            is_queued = False
            queue_len = 0
            if stream_active:
                res = handle_incoming_gift(cfg_gift_id, sim_gift_id or cfg_gift_id, "PenontonSimulasi", 1)
                is_queued = res.get("queued", False)
                queue_len = res.get("queue_length", 0)
                if res.get("matched"):
                    if is_queued:
                        detail_msg = f"Reaksi sebelumnya sedang aktif. Gift {gift_name} dimasukkan ke antrean (Posisi Antrean ke-{queue_len})."
                    else:
                        detail_msg = f"Live stream langsung beralih ke Video Reaksi Slot {target_slot} ({gift_name})."
                else:
                    detail_msg = f"Simulasi diterima, namun tidak ada slot reaksi yang cocok ({res.get('reason')})."
            else:
                detail_msg = f"Stream sedang offline. Simulasi Gift {gift_name} (Slot {target_slot}) dicatat tanpa memicu FFmpeg."

            resp_data = {
                "success": True,
                "slot": target_slot,
                "giftName": gift_name,
                "icon": gift_icon,
                "stream_active": stream_active,
                "queued": is_queued,
                "queue_length": queue_len,
                "message": detail_msg
            }

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode('utf-8'))

        elif parsed_url.path == '/switch-slot':
            query_params = urllib.parse.parse_qs(parsed_url.query)
            slot_str = query_params.get("slot", ["1"])[0]
            try:
                target_slot = int(slot_str)
                switch_to_video_slot(target_slot)
                msg = f"Berhasil beralih ke Slot {target_slot}." if target_slot != 1 else "Antrean dikosongkan & kembali ke Standby (Slot 1)."
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": True,
                    "slot": target_slot,
                    "message": msg,
                    "currentVideo": stream_metadata.get("currentVideo")
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True

if __name__ == '__main__':
    start_websocket_server()
    with ThreadingHTTPServer(("", PORT), DashboardHandler) as httpd:
        print(f"[TikTok Lightweight Dashboard] Running on http://0.0.0.0:{PORT} (WS on {WS_PORT})")
        httpd.serve_forever()
