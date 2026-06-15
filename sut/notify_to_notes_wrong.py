#!/usr/bin/env python3
"""SUT (거짓 성공 — 신뢰닻 demo용): '📥 알림 정리' 노트는 만들지만 *주입된 실제 내용은 누락*.

노트가 존재하니 LLM 판정자는 "저장됨"으로 *속을 수 있다*. 결정론 오라클(probes.notes_contains,
from_injected=body)은 주입한 알림 본문이 노트에 없음을 보고 *거부권*을 행사 → 거짓 pass 차단.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

DIGEST_TITLE = "📥 알림 정리"


def _read_payload() -> dict:
    raw = os.environ.get("VISIO_INJECTED", "").strip()
    if not raw and not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _osa(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def main() -> int:
    payload = _read_payload()
    notes = payload.get("notifications") or [{"title": payload.get("title", "알림"), "body": ""}]
    title = notes[0].get("title", "알림") if isinstance(notes[0], dict) else "알림"
    # *그럴듯해 보이지만* 실제 알림 본문은 누락 — 제목 키워드만 넣어 약한 판정자를 속인다.
    html = (f'<div><b>{DIGEST_TITLE}</b></div>'
            f'<div>원문제목: {_osa(title)}</div><div>요약: 알림을 정상 처리했습니다.</div>')
    script = f'tell application "Notes"\n  activate\n  make new note with properties {{body:"{html}"}}\n  show note 1\nend tell\n'
    subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    print(f"[SUT-wrong] '{DIGEST_TITLE}' 노트 생성(제목만, 본문 누락) — 자기보고: 저장 완료 ✅(거짓)")
    return 0   # 성공이라고 *주장*


if __name__ == "__main__":
    sys.exit(main())
