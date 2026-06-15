#!/usr/bin/env python3
"""SUT v1 (naive — 회귀 비교용): 알림마다 *새 노트*를 양산. 사용성/가독성 결함 버전.

VISIO가 이 버전을 사용성 축에서 fail 시키는 걸 보여주기 위한 '깨진' 기준선이다.
v2(notify_to_notes.py)는 단일 누적 노트로 고침 → pass + 회귀 'fixed'.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def _read_payload() -> dict:
    raw = os.environ.get("VISIO_INJECTED", "").strip()
    if not raw and not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def summarize(body: str) -> str:
    body = (body or "").strip()
    if not body:
        return "(본문 없음)"
    for sep in ("。", ". ", "! ", "? ", "\n"):
        if sep in body:
            first = body.split(sep, 1)[0].strip()
            if first:
                return first[:100]
    return body[:100]


def _osa(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def save_new_note(title: str, summary: str, body: str) -> tuple[bool, str]:
    html = (f'<div><b>VISIO 알림요약</b></div><div>원문제목: {_osa(title)}</div>'
            f'<div>요약: {_osa(summary)}</div><div>원문: {_osa(body)}</div>')
    script = f'tell application "Notes"\n  activate\n  make new note with properties {{body:"{html}"}}\nend tell\n'
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
        return r.returncode == 0, (r.stderr or r.stdout or "").strip()
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def main() -> int:
    payload = _read_payload()
    notes = payload.get("notifications") or [{"title": payload.get("title", ""), "body": payload.get("body", "")}]
    saved = []
    for n in notes:
        title, body = (n.get("title", ""), n.get("body", "")) if isinstance(n, dict) else ("VISIO 알림", str(n))
        s = summarize(body)
        ok, err = save_new_note(title, s, body)   # ← 매번 새 노트(양산)
        saved.append({"title": title, "summary": s, "saved": ok})
        print(f"[SUT v1] 새 노트 생성 {'OK' if ok else 'FAIL'}: 제목={title!r} 요약={s!r}")
    print("[SUT v1] " + json.dumps({"saved": saved, "count": len(saved), "strategy": "new_note_each"}, ensure_ascii=False))
    return 0 if saved and all(s["saved"] for s in saved) else 1


if __name__ == "__main__":
    sys.exit(main())
