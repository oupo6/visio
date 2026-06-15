#!/usr/bin/env python3
"""SUT(테스트 대상 기능) v2: 맥 알림을 요약해 Apple Notes의 *단일 누적 노트*에 정리.

v1(notify_to_notes_v1.py)은 알림마다 새 노트를 양산했다 — VISIO가 사용성 축에서 fail.
v2는 '📥 알림 정리' 노트 하나에 *append*(없으면 1회 생성)하고 그 노트를 show 한다:
  · 가독성/정리: 알림이 늘어도 한 곳에 누적
  · 검증가능: 만든 노트를 화면에 띄워 VISIO가 본문까지 확인

알림 payload는 VISIO_INJECTED(env, JSON) 또는 stdin 으로 들어온다(알림 훅 모사).
"""
from __future__ import annotations

import datetime
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


def summarize(body: str) -> str:
    """결정론 추출 요약 — 첫 문장 또는 100자 절단. 빈 본문은 명시 처리."""
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


def append_entry(title: str, summary: str, body: str, when: str) -> tuple[bool, str]:
    """단일 '📥 알림 정리' 노트에 항목 추가(없으면 생성) 후 show. 새 노트 양산 안 함."""
    entry = (f'<div>──────────</div><div>[{_osa(when)}] 원문제목: {_osa(title)}</div>'
             f'<div>요약: {_osa(summary)}</div><div>원문: {_osa(body)}</div>')
    header = f'<div><b>{_osa(DIGEST_TITLE)}</b></div><div>맥 알림 요약 모음</div>'
    script = (
        'tell application "Notes"\n'
        '  activate\n'
        f'  set matches to (notes whose name is "{_osa(DIGEST_TITLE)}")\n'
        '  if (count of matches) > 0 then\n'
        '    set theNote to item 1 of matches\n'
        '  else\n'
        f'    set theNote to make new note with properties {{body:"{header}"}}\n'
        '  end if\n'
        f'  set body of theNote to (body of theNote) & "{entry}"\n'
        '  show theNote\n'
        'end tell\n'
    )
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
        return r.returncode == 0, (r.stderr or r.stdout or "").strip()
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def main() -> int:
    payload = _read_payload()
    notes = payload.get("notifications") or [{"title": payload.get("title", ""), "body": payload.get("body", "")}]
    now = datetime.datetime.now().strftime("%H:%M")
    added = []
    for n in notes:
        title, body = (n.get("title", ""), n.get("body", "")) if isinstance(n, dict) else ("VISIO 알림", str(n))
        s = summarize(body)
        ok, err = append_entry(title, s, body, now)
        added.append({"title": title, "summary": s, "appended": ok, "err": err[:120]})
        print(f"[SUT] '{DIGEST_TITLE}'에 append {'OK' if ok else 'FAIL'}: 제목={title!r} 요약={s!r}"
              + (f"  err={err[:100]}" if not ok else ""))
    print("[SUT] " + json.dumps({"added": added, "count": len(added),
                                 "strategy": "single_digest_append", "digest": DIGEST_TITLE}, ensure_ascii=False))
    return 0 if added and all(a["appended"] for a in added) else 1


if __name__ == "__main__":
    sys.exit(main())
