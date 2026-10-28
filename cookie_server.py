import http.server
import socketserver
import os
import json
import urllib.parse
import urllib.request
import email.parser

PORT = 8088
COOKIES_FILE = "/home/ubuntu/tiktok-live-automation/cookies.json"
VIDEOS_DIR = "/home/ubuntu/tiktok-live-automation/uploads/videos"

os.makedirs(VIDEOS_DIR, exist_ok=True)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        
        if parsed_url.path == '/' or parsed_url.path == '/index.html':
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            cookies_exist = os.path.exists(COOKIES_FILE)
            cookie_status = "<p style='color:lightgreen;'>Cookies.json sudah tersedia!</p>" if cookies_exist else "<p style='color:orange;'>Cookies.json belum ada.</p>"
            
            video_slots_html = ""
            for i in range(1, 5):
                filename = f"video_{i}.mp4"
                filepath = os.path.join(VIDEOS_DIR, filename)
                if os.path.exists(filepath):
                    size_mb = round(os.path.getsize(filepath) / (1024*1024), 2)
                    video_slots_html += f"<p style='color:lightgreen;'><b>Video {i}:</b> {filename} ({size_mb} MB) - <a href='/delete-video?name={filename}' style='color:#ff3b30;'>Hapus</a></p>"
                else:
                    video_slots_html += f"""
                    <form action="/upload-video" method="POST" enctype="multipart/form-data" style="margin:5px 0;">
                        <input type="hidden" name="slot" value="{i}" />
                        <b>Video {i}:</b> <input type="file" name="videofile" accept="video/mp4" required />
                        <button type="submit" style="background:#25f4ee; color:#000; border:none; padding:5px 10px; cursor:pointer; border-radius:3px;">Upload Video {i}</button>
                    </form>
                    """

            html = f"""
            <html>
                <head><title>TikTok Live Automation - Dashboard</title></head>
                <body style="font-family:sans-serif; background:#111; color:#fff; padding:30px; text-align:center;">
                    <h1>TikTok Live Automation Dashboard</h1>
                    {cookie_status}
                    <hr style="border:1px solid #333; margin:20px 0;"/>
                    
                    <h3>Upload & Kelola Video Live (Maksimal 4 Video)</h3>
                    <div style="background:#222; padding:15px; display:inline-block; border-radius:8px; text-align:left; border:1px solid #444; max-width:600px;">
                        {video_slots_html}
                    </div>

                    <hr style="border:1px solid #333; margin:20px 0;"/>
                    <h3>Upload / Paste Cookies JSON</h3>
                    <form action="/save-cookies" method="POST">
                        <textarea name="cookies" rows="5" cols="50" placeholder="Paste JSON array cookies.json..." style="background:#222; color:#fff; border:1px solid #444; padding:10px;"></textarea><br/><br/>
                        <button type="submit" style="background:#25f4ee; color:#000; border:none; padding:10px 20px; font-weight:bold; cursor:pointer; border-radius:5px;">Simpan Cookies</button>
                    </form>

                    <hr style="border:1px solid #333; margin:20px 0;"/>
                    <h3>Kontrol Start / Stop Live & Simulasi</h3>
                    <div style="margin-bottom:15px;">
                        <form action="/get-stream-key" method="POST" style="display:inline-block; margin-right:10px;">
                            <button type="submit" style="background:#ffb800; color:#000; border:none; padding:12px 20px; font-weight:bold; cursor:pointer; border-radius:5px; font-size:15px;">Start Live (Ambil RTMP & Tayangkan)</button>
                        </form>
                        <form action="/stop-stream" method="POST" style="display:inline-block; margin-right:10px;">
                            <button type="submit" style="background:#ff3b30; color:#fff; border:none; padding:12px 20px; font-weight:bold; cursor:pointer; border-radius:5px; font-size:15px;">Stop Live</button>
                        </form>
                        <form action="/simulate-gift" method="GET" style="display:inline-block;">
                            <button type="submit" style="background:#34c759; color:#fff; border:none; padding:12px 20px; font-weight:bold; cursor:pointer; border-radius:5px; font-size:15px;">Test Simulate Gift</button>
                        </form>
                    </div>
                </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
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
        else:
            self.send_response(404)
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
                    filepath = os.path.join(VIDEOS_DIR, f"video_{slot}.mp4")
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
                with open(COOKIES_FILE, 'w') as f:
                    json.dump(parsed_json, f, indent=2)
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body style='background:#111; color:#fff; text-align:center; padding-top:50px;'><h2>Cookies berhasil disimpan!</h2><a href='/' style='color:#25f4ee;'>Kembali</a></body></html>")
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(f"<html><body style='background:#111; color:#fff; text-align:center; padding-top:50px;'><h2>Format JSON tidak valid: {str(e)}</h2><a href='/' style='color:#25f4ee;'>Kembali</a></body></html>".encode('utf-8'))
        elif parsed_url.path == '/stop-stream':
            try:
                req = urllib.request.Request("http://127.0.0.1:3000/stop-stream", method="POST")
                req.add_header('Content-Type', 'application/json')
                req.data = b'{}'
                res = urllib.request.urlopen(req)
                res_text = res.read().decode('utf-8')
                
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                html = f"""
                <html>
                    <body style='background:#111; color:#fff; text-align:center; padding-top:40px; font-family:sans-serif;'>
                        <h2 style='color:lightgreen;'>Stream Berhasil Dihentikan (Stop Live)</h2>
                        <pre style='background:#000; padding:10px; color:#0ff; text-align:left; max-width:600px; margin:0 auto;'>{res_text}</pre>
                        <br/><br/>
                        <a href='/' style='color:#25f4ee; font-size:16px;'>Kembali ke Dashboard</a>
                    </body>
                </html>
                """
                self.wfile.write(html.encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(f"<html><body style='background:#111; color:#fff; text-align:center; padding-top:50px;'><h2>Gagal menghentikan stream: {str(e)}</h2><a href='/' style='color:#25f4ee;'>Kembali</a></body></html>".encode('utf-8'))
        elif parsed_url.path == '/get-stream-key' or parsed_url.path == '/simulate-gift':
            target_path = '/start-stream' if parsed_url.path == '/get-stream-key' else parsed_url.path
            method = 'GET' if parsed_url.path == '/simulate-gift' else 'POST'
            try:
                req = urllib.request.Request(f"http://127.0.0.1:3000{target_path}", method=method)
                if method == 'POST':
                    req.add_header('Content-Type', 'application/json')
                    req.data = b'{}'
                res = urllib.request.urlopen(req)
                res_text = res.read().decode('utf-8')
                
                try:
                    res_data = json.loads(res_text)
                except:
                    res_data = {"message": res_text}

                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                
                rtmp_url = res_data.get('rtmpUrl', 'N/A')
                stream_key = res_data.get('streamKey', 'N/A')
                stream_details = json.dumps(res_data.get('streamDetails', {}), indent=2)
                raw_resp = json.dumps(res_data.get('rawResponse', {}), indent=2)

                html = f"""
                <html>
                    <body style='background:#111; color:#fff; text-align:center; padding-top:40px; font-family:sans-serif;'>
                        <h2 style='color:lightgreen;'>Hasil Eksekusi Streaming & Upload</h2>
                        <div style='background:#222; padding:20px; display:inline-block; border-radius:8px; text-align:left; border:1px solid #444; max-width:650px;'>
                            <p><b>Aksi:</b> {target_path}</p>
                            <p><b>RTMP Server URL:</b> <span id='rtmp'>{rtmp_url}</span></p>
                            <p><b>Stream Key:</b> <span id='key'>{stream_key}</span></p>
                            <p><b>Status OBS:</b> <span style='color:orange;'>{res_data.get('obsStatus', 'OK')}</span></p>
                            <p><b>Real Stream Details:</b></p>
                            <pre style='background:#000; padding:10px; color:#ff0; overflow-x:auto; font-size:12px;'>{stream_details}</pre>
                            <p><b>Raw TikTok API Response:</b></p>
                            <pre style='background:#000; padding:10px; color:#0ff; overflow-x:auto; font-size:12px;'>{raw_resp}</pre>
                        </div>
                        <br/><br/>
                        <a href='/' style='color:#25f4ee; font-size:16px;'>Kembali ke Dashboard</a>
                    </body>
                </html>
                """
                self.wfile.write(html.encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(f"<html><body style='background:#111; color:#fff; text-align:center; padding-top:50px;'><h2>Gagal mengeksekusi {parsed_url.path}: {str(e)}</h2><a href='/' style='color:#25f4ee;'>Kembali</a></body></html>".encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True

with ThreadingHTTPServer(("", PORT), Handler) as httpd:
    print(f"[Cookie QR Server] Running on http://0.0.0.0:{PORT}")
    httpd.serve_forever()
