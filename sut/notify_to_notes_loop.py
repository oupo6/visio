#!/usr/bin/env python3
"""SUT v3: 알림 요약을 '📥 알림 정리' 단일 노트에 누적.

인코딩 크래시 방지:
- osascript stdin(-) 방식으로 스크립트 전달 (셸 인자 인코딩 우회)
- 본문 텍스트는 UTF-8 임시 파일로 저장 후 AppleScript에서 read
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

NOTE_TITLE = "📥 알림 정리"
OLD_NOTE_TITLE = "VISIO 알림요약"


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
    for sep in ("\u3002", ". ", "! ", "? ", "\n"):
        if sep in body:
            first = body.split(sep, 1)[0].strip()
            if first:
                return first[:100]
    return body[:100]


def _run_osa(script: str) -> tuple[bool, str]:
    """AppleScript를 stdin으로 전달해 실행 — 셸 인자 인코딩 완전 우회."""
    try:
        r = subprocess.run(
            ["osascript", "-"],
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        return r.returncode == 0, out or err
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def cleanup_old_notes() -> None:
    """이전 버전(v1)이 생성한 'VISIO 알림요약' 노트 삭제."""
    script = f'''tell application "Notes"
    set oldNotes to (notes whose name is "{OLD_NOTE_TITLE}")
    repeat with n in oldNotes
        delete n
    end repeat
end tell'''
    _run_osa(script)


def ensure_note_exists() -> None:
    """노트가 없으면 빈 노트 1개 생성, 있으면 재사용 (절대 중복 생성 금지)."""
    script = f'''tell application "Notes"
    if (count of (notes whose name is "{NOTE_TITLE}")) = 0 then
        make new note with properties {{name:"{NOTE_TITLE}", body:""}}
    end if
end tell'''
    ok, err = _run_osa(script)
    if not ok:
        print(f"[SUT v3] 노트 확보 오류: {err}")


def append_entries_via_file(entries: list[dict]) -> tuple[bool, str]:
    """내용을 UTF-8 파일로 저장 후 AppleScript에서 읽어 기존 body에 append."""
    lines = []
    for e in entries:
        lines.append(f"[알림] {e['title']}")
        lines.append(f"요약: {e['summary']}")
        lines.append(f"원문: {e['body']}")
        lines.append("---")
    new_text = "\n".join(lines)

    # UTF-8 임시 파일에 저장
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="visio_notify_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_text)

        # AppleScript: 파일에서 읽어 기존 노트 body에 append
        script = f'''set newText to (read POSIX file "{tmp_path}" as «class utf8»)
tell application "Notes"
    if (count of (notes whose name is "{NOTE_TITLE}")) > 0 then
        set theNote to first item of (notes whose name is "{NOTE_TITLE}")
        set currentBody to body of theNote
        if currentBody is "" then
            set body of theNote to newText
        else
            set body of theNote to currentBody & "<br>" & newText
        end if
    else
        make new note with properties {{name:"{NOTE_TITLE}", body:newText}}
    end if
end tell'''
        return _run_osa(script)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def read_note_body() -> str:
    """'📥 알림 정리' 노트의 현재 본문 반환. 없으면 빈 문자열."""
    script = f'''tell application "Notes"
    if (count of (notes whose name is "{NOTE_TITLE}")) > 0 then
        return body of first item of (notes whose name is "{NOTE_TITLE}")
    else
        return ""
    end if
end tell'''
    ok, out = _run_osa(script)
    return out if ok else ""


def verify_entries(entries: list[dict]) -> list[dict]:
    """노트 본문을 직접 읽어 각 항목 존재 여부 확인."""
    current_body = read_note_body()
    verified = []
    for e in entries:
        # 제목+요약 두 가지로 존재 판단 (둘 중 하나라도 있으면 OK)
        marker_title = f"[알림] {e['title']}"
        marker_summary = e["summary"]
        present = (marker_title in current_body) or (
            bool(marker_summary) and marker_summary != "(본문 없음)" and marker_summary in current_body
        )
        verified.append({"title": e["title"], "summary": e["summary"], "present": present})
    return verified


def repair_missing(entries: list[dict], verified: list[dict]) -> list[str]:
    """누락 항목 재append 후 누락 제목 목록 반환."""
    missing = [
        e for e, v in zip(entries, verified) if not v["present"]
    ]
    if missing:
        append_entries_via_file(missing)
    return [e["title"] for e in missing]


def activate_note() -> None:
    """Notes 앱을 앞으로 가져오고 노트를 화면에 표시."""
    script = f'''tell application "Notes"
    activate
    if (count of (notes whose name is "{NOTE_TITLE}")) > 0 then
        set theNote to first item of (notes whose name is "{NOTE_TITLE}")
        show theNote
    end if
end tell'''
    _run_osa(script)


def main() -> int:
    payload = _read_payload()
    notifications = payload.get("notifications") or [
        {"title": payload.get("title", ""), "body": payload.get("body", "")}
    ]

    # v1 잔재 정리
    cleanup_old_notes()

    entries = []
    for n in notifications:
        if isinstance(n, dict):
            title, body = n.get("title", ""), n.get("body", "")
        else:
            title, body = "VISIO 알림", str(n)
        entries.append({"title": title, "summary": summarize(body), "body": body})

    # 1단계: 노트 확보
    ensure_note_exists()

    # 2단계: 파일 경유 안전 append
    ok, err = append_entries_via_file(entries)
    if not ok and err:
        print(f"[SUT v3] append 오류: {err}")

    # 3단계: 본문 읽어 누락 검증 및 재append
    verified = verify_entries(entries)
    repaired = repair_missing(entries, verified)
    if repaired:
        print(f"[SUT v3] 누락 재append: {repaired}")
        verified = verify_entries(entries)  # 재검증

    for v in verified:
        print(f"[SUT v3] 검증 {'OK' if v['present'] else 'FAIL'}: 제목={v['title']!r} 요약={v['summary']!r}")

    all_ok = all(v["present"] for v in verified)

    # 4단계: 노트 활성화
    activate_note()

    print("[SUT v3] " + json.dumps(
        {
            "verified": verified,
            "count": len(verified),
            "all_present": all_ok,
            "repaired": repaired,
            "strategy": "append_single_file",
            "note_title": NOTE_TITLE,
        },
        ensure_ascii=False,
    ))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())