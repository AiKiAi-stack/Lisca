"""Exercise channel selection and reopening against a running WebUI.

Uses a local IPTV/HLS fixture server. Selection must not create playback or ASR sessions.
Run: python test/e2e_channel_reopen.py http://127.0.0.1:8002/
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\codex_tmp\playwright-browsers")

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002/"
NAMES = ["央视测试一", "央视测试二"]


def main():
    requests = []

    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if self.path == "/channels.m3u8":
                body = "#EXTM3U\n" + "".join(
                    f'#EXTINF:-1 group-title="测试",{name}\n{origin}/station{i}.m3u8\n'
                    for i, name in enumerate(NAMES, 1)
                )
            else:
                body = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:120\n"
                body += "".join(f"#EXTINF:6.0,no desc\nsegment{i}.ts\n" for i in range(5))
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(BASE, wait_until="domcontentloaded")
            session_requests = []
            page.on("request", lambda request: session_requests.append(request.url)
                    if "/api/cloud/sessions" in request.url else None)
            page.locator("#url").fill(origin + "/channels.m3u8")
            page.locator("#chBtn").click()
            page.wait_for_function("document.querySelectorAll('.ch-name').length === 2")
            assert page.locator(".ch-name").all_text_contents() == NAMES
            page.locator(".ch-row").first.click()
            assert page.locator("#url").input_value() == origin + "/station1.m3u8"
            cycles = []
            for _ in range(3):
                page.locator("#chBtn").click()
                cycles.append({"visible": page.locator("#chPanel").is_visible(),
                               "names": page.locator(".ch-name").all_text_contents()})
                page.get_by_role("button", name="关闭频道列表", exact=True).click()
            assert session_requests == [], session_requests
            assert page.locator("#start").is_visible()
            ui_requests = list(requests)
            response = page.request.get(BASE.rstrip("/") + "/channels?url="
                                        + quote(origin + "/station1.m3u8", safe=""))
            result = {"reopen_cycles": cycles, "ui_playlist_requests": ui_requests,
                      "single_hls_status": response.status, "single_hls_body": response.json()}
            print(json.dumps(result, ensure_ascii=False))
            browser.close()
            assert all(item == {"visible": True, "names": NAMES} for item in cycles), result
            assert ui_requests == ["/channels.m3u8"], result
            assert response.status == 422, result
            assert "单路 HLS" in result["single_hls_body"]["detail"], result
            print("PASS: selecting and reopening keeps the named IPTV list; live backend rejects HLS segments")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
