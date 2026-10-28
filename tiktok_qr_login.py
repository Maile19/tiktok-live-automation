import asyncio
import http.server
import socketserver
import threading
import os
import requests
from playwright.async_api import async_playwright

QR_IMAGE_PATH = "/home/ubuntu/tiktok-live-automation/qrcode.png"
PORT = 3000

class QRHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            html = """
            <html>
                <head><title>TikTok Login QR Code</title><meta http-equiv="refresh" content="3"></head>
                <body style="text-align:center; font-family:sans-serif; background:#111; color:#fff; padding-top:50px;">
                    <h2>Scan QR Code untuk Login TikTok</h2>
                    <p>Buka aplikasi TikTok di HP Anda, masuk ke Profil -> Garis Tiga -> Pengaturan -> QR Code / Scan.</p>
                    <img src="/qrcode.png" style="width:300px; height:300px; border:5px solid white; border-radius:10px; background:white; padding:10px;" />
                    <p>Halaman ini akan auto-refresh setiap 3 detik.</p>
                </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
        elif self.path == '/qrcode.png':
            if os.path.exists(QR_IMAGE_PATH):
                self.send_response(200)
                self.send_header("Content-type", "image/png")
                self.end_headers()
                with open(QR_IMAGE_PATH, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"QR Code not generated yet.")
        else:
            self.send_response(404)
            self.end_headers()

def run_server():
    handler = QRHandler
    # Disable background thread server, run server main or handle safely
    print(f"[HTTP Server] Skipping duplicate thread server bind.")

async def main():
    # server_thread removed to avoid address in use conflict
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()

        print("[*] Membuka halaman login TikTok...")
        await page.goto("https://www.tiktok.com/login/phone-or-email/qr-code")

        print("[*] Menunggu QR Code termuat...")
        try:
            # Tunggu elemen QR code muncul (biasanya tag img di dalam kontainer QR)
            qr_img_element = await page.wait_for_selector("img", timeout=15000)
            # Ambil screenshot khusus area atau halaman agar user bisa scan
            await page.screenshot(path=QR_IMAGE_PATH, full_page=True)
            print(f"[+] Screenshot QR Code berhasil disimpan ke {QR_IMAGE_PATH}")
            print(f"[+] Akses viewer di http://<IP_VPS>:{PORT}/ atau lihat file {QR_IMAGE_PATH}")
        except Exception as e:
            print(f"[!] Gagal mengambil screenshot QR: {e}")
            await page.screenshot(path=QR_IMAGE_PATH)

        print("[*] Menunggu Anda melakukan scan QR dan login di HP (timeout 5 menit)...")
        
        session_id = None
        for _ in range(60):
            await asyncio.sleep(5)
            cookies = await context.cookies()
            for cookie in cookies:
                if cookie.get('name') == 'sessionid':
                    session_id = cookie.get('value')
                    break
            if session_id:
                break
            print("[...] Belum login, masih menunggu scan QR...")

        if session_id:
            print(f"\n[+] LOGIN BERHASIL! Ditemukan sessionid:")
            print(session_id)
            
            env_path = "/home/ubuntu/tiktok-live-automation/.env"
            with open(env_path, "r") as f:
                env_content = f.read()
            
            if "TIKTOK_SESSION_ID=" in env_content:
                lines = env_content.splitlines()
                new_lines = []
                for line in lines:
                    if line.startswith("TIKTOK_SESSION_ID="):
                        new_lines.append(f"TIKTOK_SESSION_ID={session_id}")
                    else:
                        new_lines.append(line)
                env_content = "\n".join(new_lines) + "\n"
            else:
                env_content += f"\nTIKTOK_SESSION_ID={session_id}\n"
            
            with open(env_path, "w") as f:
                f.write(env_content)
            print("[+] sessionid berhasil disimpan ke .env!")

            print("[*] Meminta RTMP Stream Key dari TikTok Live Studio API...")
            url = "https://webcast.tiktok.com/webcast/room/create/"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) TikTokLIVEStudio/0.62.2 Chrome/108.0.5359.215 Electron/22.3.18 Safari/537.36",
                "Cookie": f"sessionid={session_id}; sessionid_ss={session_id};"
            }
            payload = {
                "title": "Rehat sejenak & main bareng!",
                "live_studio": "1",
                "gen_replay": "true"
            }
            res = requests.post(url, headers=headers, data=payload)
            data = res.json()
            
            if data.get("status_code") == 0 and "data" in data:
                stream_url_data = data["data"].get("stream_url", {})
                rtmp_push_url = stream_url_data.get("rtmp_push_url")
                if rtmp_push_url:
                    parts = rtmp_push_url.rsplit("/", 1)
                    server_url = parts[0]
                    stream_key = parts[1]
                    print(f"\n[+] RTMP Server URL : {server_url}")
                    print(f"[+] RTMP Stream Key : {stream_key}")

                    print("[*] Memicu /start-stream ke OBS Headless...")
                    control_res = requests.post("http://127.0.0.1:3000/start-stream", json={
                        "rtmpUrl": server_url,
                        "streamKey": stream_key
                    })
                    print(f"[+] Respon OBS Start Stream: {control_res.text}")
                else:
                    print("[!] Akun belum diizinkan Live Studio atau RTMP URL kosong.")
            else:
                print(f"[!] Gagal membuat room TikTok Live: {data}")
        else:
            print("[!] Timeout: QR Code tidak di-scan dalam waktu 5 menit.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
