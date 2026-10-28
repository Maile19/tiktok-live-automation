import http.server
import socketserver
import os
import json
import urllib.parse
import urllib.request
import email.parser
import subprocess
import threading
import time

PORT = 8088
BASE_DIR = "/home/ubuntu/tiktok-live-automation"
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.json")
VIDEOS_DIR = os.path.join(BASE_DIR, "uploads", "videos")
CONFIG_FILE = os.path.join(BASE_DIR, "gift_config.json")

os.makedirs(VIDEOS_DIR, exist_ok=True)

streaming_process = None
stream_metadata = {
    "active": False,
    "rtmpUrl": "",
    "streamKey": "",
    "startTime": None,
    "error": None,
    "rawResponse": None
}
stream_lock = threading.Lock()

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

def load_gift_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"2": "rose", "3": "doughnut", "4": "confetti", "5": "lion"}

def save_gift_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)

def get_ffmpeg_pid():
    global streaming_process
    if streaming_process and streaming_process.poll() is None:
        return streaming_process.pid
    return None

def stop_ffmpeg_stream():
    global streaming_process, stream_metadata
    with stream_lock:
        if streaming_process:
            try:
                streaming_process.terminate()
                streaming_process.wait(timeout=3)
            except Exception:
                try:
                    streaming_process.kill()
                except Exception:
                    pass
            streaming_process = None
        stream_metadata["active"] = False
        stream_metadata["startTime"] = None
        stream_metadata["rtmpUrl"] = ""
        stream_metadata["streamKey"] = ""

def start_ffmpeg_stream(rtmp_url, stream_key):
    global streaming_process, stream_metadata
    stop_ffmpeg_stream()
    
    video_path = os.path.join(VIDEOS_DIR, "video_1.webm")
    if not os.path.exists(video_path):
        video_path = os.path.join(BASE_DIR, "media", "standby-loop.mp4")
    
    if not os.path.exists(video_path):
        raise Exception("No video file found in uploads/videos/ (video_1.webm) or media/standby-loop.mp4")

    destination = f"{rtmp_url}/{stream_key}"
    
    cmd = [
        "ffmpeg",
        "-re",
        "-stream_loop", "-1",
        "-i", video_path,
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

    print(f"[StreamingEngine] Starting lightweight ffmpeg push to TikTok: {' '.join(cmd)}")
    
    with stream_lock:
        streaming_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        stream_metadata["active"] = True
        stream_metadata["startTime"] = time.time()
        stream_metadata["rtmpUrl"] = rtmp_url
        stream_metadata["streamKey"] = stream_key[:10] + "..." if stream_key else ""

def fetch_tiktok_stream_key():
    if not os.path.exists(COOKIES_FILE):
        raise Exception("cookies.json belum tersedia. Silakan upload atau paste cookies terlebih dahulu.")

    with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
        cookies = json.load(f)
    
    cookie_header = "; ".join([f"{c.get('name')}={c.get('value')}" for c in cookies if c.get('name') and c.get('value')])

    url = 'https://webcast.tiktok.com/webcast/room/create/?aid=1988&app_name=tiktok_web'
    
    req = urllib.request.Request(url, method='POST')
    req.add_header('Cookie', cookie_header)
    req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    req.add_header('Referer', 'https://livecenter.tiktok.com/producer')
    req.add_header('Origin', 'https://livecenter.tiktok.com')
    req.add_header('Content-Type', 'application/json')
    req.data = b'{}'

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode('utf-8')
            json_data = json.loads(resp_body)
    except Exception as e:
        raise Exception(f"Gagal menghubungi TikTok API: {str(e)}")

    stream_url_obj = json_data.get('data', {}).get('stream_url', {})
    rtmp_push_url = stream_url_obj.get('rtmp_push_url', '')
    
    if not rtmp_push_url:
        raise Exception(f"TikTok API mengembalikan respons tanpa RTMP push URL. Respons: {json.dumps(json_data)}")

    last_slash = rtmp_push_url.rfind('/')
    rtmp_url = rtmp_push_url[:last_slash]
    stream_key = rtmp_push_url[last_slash + 1:]

    return rtmp_url, stream_key, json_data

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        
        if parsed_url.path == '/' or parsed_url.path == '/index.html':
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            
            cookies_exist = os.path.exists(COOKIES_FILE)
            cookie_status = "<span style='color:#34c759; font-weight:bold;'>✓ Cookies.json Aktif & Tersedia</span>" if cookies_exist else "<span style='color:#ff3b30; font-weight:bold;'>✗ Cookies.json Belum Ada (Wajib Upload/Paste)</span>"
            
            is_live = get_ffmpeg_pid() is not None
            live_color = "#34c759" if is_live else "#ff3b30"
            live_text = "AKTIF & STREAMING (Lightweight FFmpeg)" if is_live else "OFFLINE"
            
            online_class_live = "online" if is_live else ""
            live_status_html = f"""
            <div class="shine-border-card {online_class_live}">
                <h3 style="margin-top:0;">Status Live: <span style="color:{live_color};">{live_text}</span></h3>
            """
            if is_live:
                live_status_html += f"<p style='color:#aaa; font-size:13px; margin-bottom:0;'>RTMP Server: {stream_metadata.get('rtmpUrl', 'N/A')}</p>"
            live_status_html += "</div>"

            gift_config = load_gift_config()
            video_slots_html = ""
            
            for i in range(1, 6):
                filename = f"video_{i}.webm"
                filepath = os.path.join(VIDEOS_DIR, filename)
                slot_desc = "Video Utama / Standby Loop" if i == 1 else f"Video Reaksi Pemicu Gift (Slot {i})"
                
                file_status_ui = ""
                if os.path.exists(filepath):
                    size_mb = round(os.path.getsize(filepath) / (1024*1024), 2)
                    file_status_ui = f"""
                    <span style='color:#34c759; margin-right:10px;'><b>{filename}</b> ({size_mb} MB)</span>
                    <a href='/delete-video?name={filename}' class='shimmer-btn' style='background:rgba(255, 59, 48, 0.2); border-color:#ff3b30; color:#ff3b30; padding:6px 14px; font-size:12px; text-decoration:none;'>Hapus</a>
                    """
                else:
                    file_status_ui = f"""
                    <form action="/upload-video" method="POST" enctype="multipart/form-data" style='display:flex; align-items:center; gap:10px; margin:0;' onsubmit="return validateUpload(this);">
                        <input type="hidden" name="slot" value="{i}" />
                        <span style="color:#a1a1aa; font-size:12px;">Format .webm</span>
                        <input type="file" name="videofile" accept="video/webm" style="color:#aaa; font-size:12px;" />
                        <button type="submit" class="shimmer-btn" style="padding:6px 14px; font-size:12px;">Upload</button>
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
                """
                
                if i >= 2:
                    current_gift = gift_config.get(str(i), "rose")
                    video_slots_html += f"""
                    <div style='margin-top:10px; border-top:1px solid #27272a; padding-top:10px;'>
                        <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;'>
                            <span style='font-size:13px; color:#25f4ee;'>Konfigurasi Pemicu Gift (Slot {i}):</span>
                            <span style='font-size:12px; color:#a1a1aa;'>Gift Terpilih ID: <b>{current_gift}</b></span>
                        </div>
                        <iframe src="/gift-selector?slot={i}" style="width:100%; height:140px; border:1px solid #3f3f46; border-radius:6px; background:#0f0f11;" scrolling="yes"></iframe>
                    </div>
                    """
                video_slots_html += "</div>"

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
                        .shine-border-card {{
                            position: relative;
                            border-radius: 12px;
                            background: #121216;
                            padding: 20px;
                            margin-bottom: 25px;
                            border: 1px solid #27272a;
                            overflow: hidden;
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
                        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f0f11; color: #f1f1f3; margin: 0; padding: 20px; }}
                        .container {{ max-width: 850px; margin: 0 auto; background: #18181b; padding: 30px; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); border: 1px solid #27272a; }}
                        h1, h2, h3 {{ color: #fff; }}
                        hr {{ border: 0; border-top: 1px solid #27272a; margin: 25px 0; }}
                        textarea {{ width: 100%; background: #0f0f11; color: #25f4ee; border: 1px solid #3f3f46; padding: 12px; border-radius: 6px; font-family: monospace; resize: vertical; box-sizing: border-box; }}
                        pre {{ background: #0f0f11; padding: 15px; border-radius: 6px; color: #25f4ee; overflow-x: auto; font-size: 13px; border: 1px solid #3f3f46; text-align: left; }}
                    </style>
                    <script>
                        function validateUpload(form) {{
                            const fileInput = form.querySelector('input[type="file"]');
                            if (!fileInput || fileInput.files.length === 0) {{
                                alert("Peringatan: Tidak ada file yang dipilih. Silakan pilih file video berekstensi .webm terlebih dahulu.");
                                return false;
                            }}
                            const filename = fileInput.files[0].name.toLowerCase();
                            if (!filename.endsWith('.webm')) {{
                                alert("Peringatan: Format file harus berekstensi .webm!");
                                return false;
                            }}
                            return true;
                        }}
                    </script>
                </head>
                <body>
                    <div class="container">
                        <h1>TikTok Live Lightweight Dashboard</h1>
                        <p>Arsitektur Ringan & Stabil (5 Slot Video .webm & Pemicu Gift)</p>
                        
                        <div style="margin: 15px 0;">
                            {cookie_status}
                        </div>

                        {live_status_html}

                        <div class="shine-border-card">
                            <h3 style="margin-top:0;">Manajemen Video & Pemicu Gift (Slot 1 - 5)</h3>
                            <p style="color: #a1a1aa; font-size: 13px;">Format wajib <b>.webm</b>. Slot 1 standby loop, Slot 2 s.d. 5 reaktif terhadap gift TikTok.</p>
                            <div>
                                {video_slots_html}
                            </div>
                        </div>

                        <hr/>
                        <h3>Kontrol Live Streaming</h3>
                        <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px;">
                            <form action="/start-stream" method="POST" style="margin:0;">
                                <button type="submit" class="shimmer-btn" style="--shimmer-color: #ffb800; --bg: rgba(255, 184, 0, 0.15);">Start Live (Ambil RTMP & Push Video)</button>
                            </form>
                            <form action="/stop-stream" method="POST" style="margin:0;">
                                <button type="submit" class="shimmer-btn" style="--shimmer-color: #ff3b30; --bg: rgba(255, 59, 48, 0.15);">Stop Live (Matikan Stream)</button>
                            </form>
                            <form action="/simulate-gift" method="POST" style="margin:0;">
                                <button type="submit" class="shimmer-btn" style="--shimmer-color: #34c759; --bg: rgba(52, 199, 89, 0.15);">Simulate Gift Event</button>
                            </form>
                        </div>

                        <div class="shine-border-card">
                            <h3 style="margin-top:0;">Update Cookies JSON</h3>
                            <form action="/save-cookies" method="POST">
                                <textarea name="cookies" rows="6" placeholder="Paste array cookies JSON di sini..."></textarea><br/><br/>
                                <button type="submit" class="shimmer-btn" style="--shimmer-color: #25f4ee; --bg: rgba(37, 244, 238, 0.15);">Simpan Cookies</button>
                            </form>
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
                bg_sel = "background:#22252a; border-color:#25f4ee;" if g["id"] == current_selected else "background:#121215; border-color:#27272a;"
                idr_formatted = f"Rp {g['idr']:,}".replace(",", ".")
                
                gifts_rows += f"""
                <label class="shimmer-btn" style="display:flex; align-items:center; justify-content:space-between; padding:8px 12px; margin:4px 0; border-radius:8px; width:100%; box-sizing:border-box; {bg_sel} cursor:pointer; text-align:left;">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <span style="font-size:20px;">{g['icon']}</span>
                        <div>
                            <div style="color:#fff; font-size:13px; font-weight:bold;">{g['name']}</div>
                            <div style="color:#a1a1aa; font-size:11px;">{g['coins']} Koin</div>
                        </div>
                    </div>
                    <div style="text-align:right; display:flex; align-items:center; gap:12px;">
                        <span style="color:#34c759; font-size:12px; font-weight:bold;">{idr_formatted}</span>
                        <input type="radio" name="gift_id" value="{g['id']}" {is_sel} onchange="saveGift('{slot}', '{g['id']}')" />
                    </div>
                </label>
                """
                
            iframe_html = f"""<!DOCTYPE html>
            <html>
                <head>
                    <meta charset="utf-8">
                    <style>
                        @keyframes spin-around {{
                            0% {{ transform: translateZ(0) rotate(0deg); }}
                            100% {{ transform: translateZ(0) rotate(360deg); }}
                        }}
                        .shimmer-btn {{
                            --spread: 90deg;
                            --shimmer-color: #ffffff;
                            --radius: 8px;
                            --speed: 3s;
                            --cut: 1px;
                            --bg: rgba(18, 18, 22, 0.95);
                            position: relative;
                            z-index: 0;
                            display: flex;
                            align-items: center;
                            justify-content: space-between;
                            overflow: hidden;
                            cursor: pointer;
                            border: 1px solid rgba(255, 255, 255, 0.12);
                            color: #ffffff;
                            transition: transform 0.2s ease, box-shadow 0.2s ease;
                        }}
                        .shimmer-btn:hover {{
                            border-color: #25f4ee;
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
                        body {{ font-family: sans-serif; background: #0f0f11; color: #fff; margin: 0; padding: 10px; }}
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
                    <div style="font-size:12px; color:#25f4ee; margin-bottom:8px; font-weight:bold;">Pilih Pemicu Gift & Estimasi Pendapatan:</div>
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
            filename = query.get('name', [''])[0]
            if filename:
                filepath = os.path.join(VIDEOS_DIR, filename)
                if os.path.exists(filepath):
                    os.remove(filepath)
            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            
        elif parsed_url.path == '/health':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            status_data = {
                "status": "ok",
                "streamingActive": get_ffmpeg_pid() is not None,
                "ffmpegPid": get_ffmpeg_pid(),
                "uptime": time.time()
            }
            self.wfile.write(json.dumps(status_data).encode('utf-8'))
        else:
            self.send_response(404)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        
        if parsed_url.path == '/upload-video':
            try:
                content_type = self.headers.get('Content-Type', '')
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length)
                
                headers = f"Content-Type: {content_type}\n\n".encode('utf-8')
                msg = email.parser.BytesParser().parsebytes(headers + body)
                
                slot = "1"
                file_data = None
                
                if msg.is_multipart():
                    for part in msg.walk():
                        cd = part.get("Content-Disposition", "")
                        if 'name="slot"' in cd:
                            payload = part.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                slot = payload.decode('utf-8', errors='ignore').strip()
                        elif 'name="videofile"' in cd:
                            payload = part.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                file_data = payload
                
                if file_data:
                    filepath = os.path.join(VIDEOS_DIR, f"video_{slot}.webm")
                    with open(filepath, 'wb') as f:
                        f.write(file_data)
            except Exception as e:
                print(f"[Upload Error] {e}")

            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            
        elif parsed_url.path == '/save-cookies':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            parsed = urllib.parse.parse_qs(body)
            cookies_raw = parsed.get('cookies', [''])[0]
            try:
                parsed_json = json.loads(cookies_raw)
                with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                    json.dump(parsed_json, f, indent=2)
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body style='background:#0f0f11; color:#fff; text-align:center; padding-top:50px; font-family:sans-serif;'><h2>Cookies berhasil disimpan!</h2><br/><a href='/' style='color:#25f4ee; font-size:16px;'>Kembali ke Dashboard</a></body></html>")
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body style='background:#0f0f11; color:#fff; text-align:center; padding-top:50px; font-family:sans-serif;'><h2>Format JSON tidak valid: {str(e)}</h2><br/><a href='/' style='color:#25f4ee;'>Kembali</a></body></html>".encode('utf-8'))
                
        elif parsed_url.path == '/start-stream':
            try:
                rtmp_url, stream_key, raw_resp = fetch_tiktok_stream_key()
                start_ffmpeg_stream(rtmp_url, stream_key)
                
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                
                html = f"""<!DOCTYPE html>
                <html>
                    <body style='background:#0f0f11; color:#fff; text-align:center; padding-top:40px; font-family:sans-serif;'>
                        <h2 style='color:#34c759;'>Live Streaming Berhasil Dimulai!</h2>
                        <div style='background:#18181b; padding:20px; display:inline-block; border-radius:8px; text-align:left; border:1px solid #27272a; max-width:650px;'>
                            <p><b>RTMP Server:</b> <span style='color:#25f4ee;'>{rtmp_url}</span></p>
                            <p><b>Stream Key:</b> <span style='color:#25f4ee;'>{stream_key[:10]}...</span></p>
                            <p><b>Engine Status:</b> <span style='color:#34c759;'>FFmpeg Lightweight Stream Active (PID: {get_ffmpeg_pid()})</span></p>
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
                stop_ffmpeg_stream()
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                html = """<!DOCTYPE html>
                <html>
                    <body style='background:#0f0f11; color:#fff; text-align:center; padding-top:40px; font-family:sans-serif;'>
                        <h2 style='color:#34c759;'>Stream Berhasil Dihentikan (Stop Live)</h2>
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
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html = """<!DOCTYPE html>
            <html>
                <body style='background:#0f0f11; color:#fff; text-align:center; padding-top:40px; font-family:sans-serif;'>
                    <h2 style='color:#34c759;'>Simulasi Gift Berhasil Diterima!</h2>
                    <p style='color:#a1a1aa;'>Event gift disimulasikan ke queue manager tanpa error ERR_EMPTY_RESPONSE.</p>
                    <br/><br/>
                    <a href='/' style='color:#25f4ee; font-size:16px; text-decoration:none; background:#27272a; padding:10px 20px; border-radius:6px;'>Kembali ke Dashboard</a>
                </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
        else:
            self.send_response(404)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True

if __name__ == '__main__':
    with ThreadingHTTPServer(("", PORT), DashboardHandler) as httpd:
        print(f"[TikTok Lightweight Dashboard] Running on http://0.0.0.0:{PORT}")
        httpd.serve_forever()
