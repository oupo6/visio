#!/usr/bin/env python3
"""SUT(에이전트가 만든 기능): *이미 화면에 열려있는* 카톡 대화를 요약 → Notes 노트로 저장.

★역할 경계: 네비게이션(앱/채팅 열기)·노트 띄우기는 *하지 않는다* — 그건 테스터(VISIO/SAPPHI 손)의 일.
 기능은 딱 '보이는 대화 읽기 → 요약 → 조용히 저장'만. (activate/show/클릭 없음.)
"""
from __future__ import annotations

import subprocess
import sys

DIGEST = "📥 카톡 요약"


def _osa(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def summarize(convo: str) -> str:
    """간이 추출 요약(파이프라인 검증용 — 품질은 판정 대상 아님)."""
    lines = [l.strip(" *->·-#") for l in (convo or "").splitlines() if l.strip()]
    msgs = [l for l in lines if len(l) >= 2 and not l.startswith("---")][:8]
    return " / ".join(msgs) if msgs else "(읽은 내용 없음)"


def save_note(summary: str, convo: str) -> bool:
    """Notes에 *조용히* 저장 — activate/show 없음(띄우기는 테스터 일)."""
    html = (f'<div><b>{_osa(DIGEST)}</b></div><div>요약: {_osa(summary)}</div>'
            f'<div>──────────</div><div>원문: {_osa(convo[:600])}</div>')
    r = subprocess.run(["osascript", "-e",
        f'tell application "Notes" to make new note with properties {{body:"{html}"}}'],
        capture_output=True, text=True, timeout=20)
    return r.returncode == 0


def main() -> int:
    from rubi import read
    # VISIO가 이미 카톡 대화를 열어둠 → *보이는 대화*를 읽는다(네비 안 함).
    # 읽기 = Claude 클라우드 비전(sensitive=False) — 사용자: 프라이버시 무관·비용 우선, 정확도 위해 클라우드.
    # ※VISIO의 검증 읽기와는 *다른 subprocess·다른 호출*이라 독립성 유지(VISIO는 노트 안 믿고 제 손으로 재확인).
    rd = read.read_content("KakaoTalk", sensitive=False, force_vision=True,
                           instruction="이 카톡 대화 메시지를 위→아래 순서로 옮겨라. 보이는 것만.")
    convo = rd.get("text") or ""
    print(f"[SUT] 보이는 대화 읽음 method={rd.get('method')} len={len(convo)}")
    summary = summarize(convo)
    ok = save_note(summary, convo)
    print(f"[SUT] Notes 조용히 저장 {'OK' if ok else 'FAIL'}: {summary[:70]}")
    return 0 if ok and convo else 1


if __name__ == "__main__":
    sys.exit(main())
