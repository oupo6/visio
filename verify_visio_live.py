#!/usr/bin/env python3
"""라이브 앱 경로 측정 — VISIO가 *진짜 Notes 앱*을 통한 기능을 end-to-end로 검증한다.

value/adversarial 벤치는 상태를 *프로그램으로 깔아놓고*(임시파일·클립보드·defaults) 판정자만 시험했다.
여기선 한 단계 더 — 전체 라이브 파이프라인:
  VISIO가 *실제 알림을 쏘고*(triggers) → *빌드된 SUT가 진짜 Apple Notes 앱에 기록* →
  VISIO가 *그 실제 노트를 AppleScript로 직접 읽어*(probes.notes_contains = authoritative 오라클) 판정.
  = '자극 → 실제 GUI 앱 구동 → 실제 결과 지각 → 판정'.

측정: 같은 케이스를 **정상 SUT** vs **버그 SUT**(노트는 만들지만 *실제 본문 누락* + "저장완료 ✅" 거짓보고)로
돌려, VISIO 판정이 PASS→FAIL로 뒤집히는지 = *라이브 회귀(소리없는 실패)*를 실환경에서 잡는가.
(rubi visio 의 실제 오케스트레이터 run_test_plan 을 mode="live" 로 호출 — 손으로 흉내낸 게 아님.)

★라이브가 드러낸 함정(프로그램 벤치는 못 겪음): notes_contains 가 'Recently Deleted'(휴지통)까지 읽어
  삭제된 노트가 거짓PASS를 유발 → probes.py 에서 container 로 휴지통 제외하도록 *코어 하드닝*함.
  여기선 추가로 런마다 *고유 노트명*을 써서 실앱 상태 잔재(중복명) 자체를 원천 차단한다.
"""
import os
import subprocess

from rubi import visio
from rubi.visio import TestCase, TestPlan, run_test_plan

BODY = "문 앞에 택배가 도착했습니다. 부재시 경비실에 맡겨주세요."
NOTES_SPEC = {
    "task_type": "automation", "app": "Notes", "risk": "low",
    "requires_confirmation": False, "channel": "",
    "audit_axes": ["intent_sufficiency", "evidence_adequacy"],
    "postconditions": ["Notes에 알림을 요약한 노트가 보인다"],
    "source": "handauthored",
}


def cleanup_note(title: str):
    """이 측정이 만든 *활성* 노트만 안전 삭제(휴지통 제외, 한 개씩 재조회 — -1728 회피)."""
    esc = title.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'tell application "Notes"\n'
        '  repeat 20 times\n'
        '    set tgt to missing value\n'
        f'    repeat with n in (notes whose name is "{esc}")\n'
        '      set cn to ""\n'
        '      try\n'
        '        set cn to (name of (container of n))\n'
        '      end try\n'
        '      if cn is not "Recently Deleted" and cn is not "최근 삭제된 항목" then\n'
        '        set tgt to n\n'
        '        exit repeat\n'
        '      end if\n'
        '    end repeat\n'
        '    if tgt is missing value then exit repeat\n'
        '    delete tgt\n'
        '  end repeat\n'
        'end tell\n')
    subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)


def build_plan(sut_entry, title):
    case = TestCase(
        id="parcel_live", title="라이브: 택배 알림 요약→실제 Notes 저장",
        goal="맥 알림('택배 도착')을 요약해 Apple Notes에 저장한다",
        rationale="실제 Notes 앱에 본문이 진짜 저장되는가(자기보고 무시, 노트 직접 읽음)",
        spec=dict(NOTES_SPEC),
        expected=f"Notes '{title}'에 알림 본문이 실제로 들어가 있다",
        preconditions=[], must_confirm=False, origin="handauthored",
        stimulus={"kind": "notification", "params": {"title": "택배 도착", "body": BODY}},
        fixture="native:notification",
        # authoritative 오라클: VLM 화면판정 안 부르고 *실제 노트 plaintext* 를 읽어 본문 대조
        oracle={"authoritative": True, "kind": "notes_contains",
                "params": {"title": title, "from_injected": "body"}})
    return TestPlan("맥 알림을 요약해 Notes에 저장", "notify_live", [case],
                    "claude-opus-4-8", "claude-sonnet-4-6", visio._now(),
                    fixture_requests=[], sut_entry=sut_entry)


# 런마다 고유 노트명 → 휴지통/중복명 잔재 원천 차단
BASE = f"📥 VISIO라이브 {os.getpid()}"
RUNS = [
    ("정상 SUT",                   ".venv/bin/python sut/notify_to_notes.py",       "pass", f"{BASE}-ok"),
    ("버그 SUT(본문누락+거짓보고)", ".venv/bin/python sut/notify_to_notes_wrong.py", "fail", f"{BASE}-bug"),
]


def main():
    print("=" * 76)
    print("라이브 앱 경로 측정 — 진짜 알림 → 실제 Notes 앱 구동 → VISIO가 실제 노트 읽어 판정")
    print(f"(run_test_plan mode=live · 오라클=notes_contains · 본문='{BODY[:22]}…')")
    print("=" * 76)
    rows = []
    titles = [t for *_, t in RUNS]
    try:
        for label, sut, expected, title in RUNS:
            cleanup_note(title)
            os.environ["VISIO_DIGEST_TITLE"] = title          # SUT 서브프로세스가 이 고유명에 기록
            rep = run_test_plan(build_plan(sut, title), mode="live",
                                out_dir="visio_out/notify_live", local_judge="off", verbose=False)
            r = rep.results[0]
            verdict = "pass" if r.achieved else "fail"
            ev = (r.verdict.get("oracle_evidence") or [{}])[0].get("evidence", "") or r.note
            rows.append((label, expected, verdict, r.status, getattr(r, "trust", ""), ev))
    finally:
        os.environ.pop("VISIO_DIGEST_TITLE", None)
        for t in titles:
            cleanup_note(t)

    print(f"\n{'런':<28}{'기대':<7}{'VISIO':<8}{'상태':<10}판정")
    print("-" * 76)
    fp = fn = 0
    for label, exp, verdict, status, trust, ev in rows:
        if exp == "fail" and verdict == "pass":
            mark = "★거짓PASS(놓침!)"; fp += 1
        elif exp == "pass" and verdict == "fail":
            mark = "거짓FAIL"; fn += 1
        else:
            mark = "✓정확"
        print(f"{label:<28}{exp:<7}{verdict:<8}{status:<10}{mark}")
        print(f"   └ 신뢰닻={trust}  오라클: {str(ev)[:76]}")
    print("-" * 76)
    caught = len(rows) == 2 and rows[0][2] == "pass" and rows[1][2] == "fail"
    print("라이브 회귀(소리없는 실패) 적발:",
          "✅ 예 — 실제 Notes를 읽어 정상=PASS / 버그=FAIL 로 잡음" if caught else "❌ 미흡(위 표 확인)")
    print(f"거짓PASS {fp} · 거짓FAIL {fn}")
    print("=" * 76)


if __name__ == "__main__":
    main()
