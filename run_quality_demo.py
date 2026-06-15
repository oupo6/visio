#!/usr/bin/env python3
"""VISIO 품질(비기능) 회귀 증명: 같은 '사용성' 케이스를
  RUN1 v1(알림마다 새 노트=흩어짐) → fail(intent_sufficiency: 사용성)
  RUN2 v2(단일 누적 노트)          → pass + 회귀 'fixed'
판정자 core는 알림을 모름 — 일반 품질축(가독성·정리·비용)으로 판정. 각 런 사이 노트 정리.
"""
import subprocess

from rubi import visio
from rubi.visio import TestCase, TestPlan, run_test_plan, print_summary, print_regression


def cleanup_notes():
    """내 테스트 노트만(이름 정확 일치) Recently Deleted 로. 사용자 다른 노트 안 건드림."""
    script = (
        'tell application "Notes"\n'
        '  repeat with nm in {"VISIO 알림요약", "📥 알림 정리"}\n'
        '    repeat 60 times\n'
        '      set m to (notes whose name is (contents of nm))\n'
        '      if (count of m) is 0 then exit repeat\n'
        '      delete item 1 of m\n'
        '    end repeat\n'
        '  end repeat\n'
        'end tell\n')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=40)
    return "ok" if r.returncode == 0 else (r.stderr or "")[:120]


NOTES_SPEC = {
    "task_type": "automation", "app": "Notes", "risk": "low",
    "requires_confirmation": False, "channel": "",
    "audit_axes": ["intent_sufficiency", "evidence_adequacy"],
    "postconditions": ["요약이 사용자가 보기 좋게 정리돼 저장된다"],
    "source": "handauthored",
}

case = TestCase(
    id="burst_usability",
    title="사용성: 알림 다발이 와도 한 곳에 정리되나",
    goal=("맥 알림이 여러 번 올 때 요약을 *한 곳에 정리해* Notes에 저장한다 — 사용자가 한눈에 보게. "
          "알림마다 새 노트로 흩어지면 가독성·관리성이 떨어져 사용성 실패다."),
    rationale="비기능(사용성/가독성/비용) — 알림마다 새 노트면 사람이 쓰기 불편·노트 무한증식",
    spec=dict(NOTES_SPEC),
    expected="여러 알림이 와도 흩어진 개별 노트로 쌓이지 않고, 한 개의 누적 정리 노트에 모여 한눈에 보인다",
    preconditions=[], must_confirm=False, origin="handauthored",
    stimulus={"kind": "notification", "params": {"items": [
        {"title": "택배 도착", "body": "문 앞에 택배가 도착했습니다."},
        {"title": "회의 알림", "body": "3시 팀 회의가 곧 시작됩니다."},
        {"title": "메시지", "body": "엄마: 저녁 먹었니?"},
        {"title": "캘린더", "body": "내일 치과 예약 10시."},
    ], "interval": 0.3}},
    fixture="native:notification")

plan = TestPlan("맥 알림을 요약해 Notes에 저장하는 기능", "notify_to_notes", [case],
                "claude-opus-4-8", "claude-sonnet-4-6", visio._now(),
                fixture_requests=[], sut_entry="")

print("정리(기존 테스트 노트):", cleanup_notes())

print("\n########## RUN 1 — v1 (naive: 알림마다 새 노트) ##########")
plan.sut_entry = ".venv/bin/python sut/notify_to_notes_v1.py"
rep1 = run_test_plan(plan, mode="rehearse", out_dir="visio_out/quality_demo",
                     local_judge="off", verbose=True)
print_summary(rep1)

print("\n정리(v1이 만든 흩어진 노트):", cleanup_notes())

print("\n########## RUN 2 — v2 (단일 누적 노트) ##########")
plan.sut_entry = ".venv/bin/python sut/notify_to_notes.py"
rep2 = run_test_plan(plan, mode="rehearse", out_dir="visio_out/quality_demo",
                     local_judge="off", verbose=True)
print_summary(rep2)
print_regression(rep2)

print("\n최종 정리:", cleanup_notes())
print("\n>>> 기대: RUN1 ❌fail(사용성) → RUN2 ✅pass → 회귀 'fixed'")
