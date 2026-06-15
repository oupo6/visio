"""테스트용 로컬 mock HTTP 서버 — '서버에 제대로 전송됐나'를 결정론으로 검증하기 위한 *테스트 더블*.

기능(SUT)을 이 mock 의 base_url 로 향하게 하면, mock 이 *받은 요청 전부*(method·path·headers·body)를
record_path(JSONL)에 기록한다. 그 뒤 `probes.http_received` 가 그 기록을 읽어 *실제로 뭐가 전송됐는지*
대조한다 — SUT 의 "보냈어요" 자기보고는 안 믿고 *서버가 실제 받은 것*이 ground truth.

표준 테스트 기법(test double). 외부 의존성 0(파이썬 표준 http.server), sudo 불필요, localhost 만.
TLS 가 꼭 필요한(redirect 불가) 경우엔 tcpdump/프록시 캡처가 대안 — 여긴 redirect 가능 케이스용.
"""
from __future__ import annotations

import http.server
import json
import threading
import time


def _make_handler(record_path: str):
    class _Handler(http.server.BaseHTTPRequestHandler):
        def _record(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = 0
            body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            rec = {"method": method, "path": self.path,
                   "headers": {k: v for k, v in self.headers.items()},
                   "body": body, "ts": time.time()}
            with open(record_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):    self._record("GET")
        def do_POST(self):   self._record("POST")
        def do_PUT(self):    self._record("PUT")
        def do_DELETE(self): self._record("DELETE")
        def do_PATCH(self):  self._record("PATCH")

        def log_message(self, *a):  # 조용히(테스트 노이즈 제거)
            return

    return _Handler


class MockServer:
    """컨텍스트 매니저: with MockServer(record_path) as m: ... m.base_url 로 SUT 보냄. 받은 요청은 record_path 에."""

    def __init__(self, record_path: str, port: int = 0):
        self.record_path = record_path
        open(record_path, "w").close()                  # 기록 초기화
        self.httpd = http.server.HTTPServer(("127.0.0.1", port), _make_handler(record_path))
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "MockServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass
