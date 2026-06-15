#!/usr/bin/env python3
"""읽기전용 feasibility 프로브 — 지정 앱의 *현재 화면*에서 텍스트가 추출되는지 확인.

카톡 읽기 SUT를 빌드할 수 있는지(OCR/AX로 메시지 텍스트가 잡히는지) 판가름하는 용도.
**클릭·타이핑·전송 0** — 앱 활성화 + 스샷 + OCR + AX 읽기만. (네가 열어둔 대화 화면 그대로 읽음.)

  .venv/bin/python probe_read.py [앱이름=KakaoTalk]
"""
from __future__ import annotations

import os
import sys
import time

from sapphi import ocr, perceive, state_snapshot

app = sys.argv[1] if len(sys.argv) > 1 else "KakaoTalk"
out = "visio_out/probe"
os.makedirs(out, exist_ok=True)
shot = os.path.join(out, "probe.png")

print(f"[probe] {app} 활성화(읽기전용, 클릭/입력 없음)…")
perceive.open_app(app)
perceive.focus_app(app)
time.sleep(1.0)
perceive.screenshot(shot, app=app)
print(f"[probe] 스샷 저장: {shot}  (front={perceive.frontmost_app()})")

# ── OCR (화면 글자) ──────────────────────────────────────────────
texts: list[str] = []
if ocr.available():
    items = ocr.ocr_screen(image_path=shot)
    texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
else:
    print("[OCR] ocrground 사용 불가")
print(f"\n[OCR] 텍스트 조각 {len(texts)}개 추출 (샘플 최대 18개):")
for t in texts[:18]:
    print(f"   · {t[:70]}")
if len(texts) > 18:
    print(f"   … (외 {len(texts) - 18}개)")

# ── AX (접근성 트리) ─────────────────────────────────────────────
try:
    snap = state_snapshot.capture(shot, out, 0, 0, isolate_app=app,
                                  expected_front=perceive.frontmost_app())
    ax = getattr(snap, "ax_candidates", None) or []
    labeled = [c for c in ax if (c.get("label") or "").strip()]
    print(f"\n[AX] 후보 {len(ax)}개 (라벨 있는 것 {len(labeled)}개, 샘플 최대 12개):")
    for c in labeled[:12]:
        print(f"   · {c.get('role', '?')}: {(c.get('label') or '')[:50]}")
except Exception as e:
    print(f"\n[AX] 실패: {type(e).__name__}: {str(e)[:120]}")

print(f"\n>>> 위에 *메시지 텍스트*가 보이면 → 카톡 읽기 SUT 빌드 가능(OCR 경로). 안 보이면 다른 방법 강구.")
