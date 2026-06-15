#!/usr/bin/env python3
"""닫힌 루프(①) 증명: 깨진 SUT(v1=알림마다 새 노트) → VISIO 리뷰 → fixer 자동 수정 → 재확인 → 수렴.

독립성: fixer=sonnet ≠ judge=opus, fixer는 오라클/판정자 내부 못 봄(directive만).
신뢰 바닥: oracle=notes_contains(주입 본문이 '📥 알림 정리' 노트에 실제 들어갔나) — 수렴은 oracle-confirmed.
"""
import shutil
import subprocess

from rubi import visio, visio_loop
from rubi.visio import TestCase, TestPlan

WORK_SUT = "sut/notify_to_notes_loop.py"


def cleanup_notes():
    script = ('tell application "Notes"\n'
              '  repeat with nm in {"VISIO 알림요약", "📥 알림 정리"}\n'
              '    repeat 60 times\n'
              '      set m to (notes whose name is (contents of nm))\n'
              '      if (count of m) is 0 then exit repeat\n'
              '      delete item 1 of m\n'
              '    end repeat\n'
              '  end repeat\n'
              'end tell\n')
    subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=40)


# 깨진 v1(흩어짐)을 편집 대상 작업파일로 복사 — 루프가 이걸 고친다
shutil.copy("sut/notify_to_notes_v1.py", WORK_SUT)
print(f"시작 SUT = v1 복사본({WORK_SUT}) — 알림마다 새 노트(흩어짐, 사용성 결함)")

NOTES_SPEC = {"task_type": "automation", "app": "Notes", "risk": "low",
              "requires_confirmation": False, "audit_axes": ["intent_sufficiency", "evidence_adequacy"],
              "source": "handauthored"}

case = TestCase(
    id="digest_usability",
    title="여러 알림을 '📥 알림 정리' 한 노트에 누적",
    goal=("맥 알림이 여러 번 올 때, 각 요약을 '📥 알림 정리'라는 *한 개의 노트*에 누적 저장한다. "
          "알림마다 새 노트로 흩어지면 사용성 실패다."),
    rationale="비기능(사용성/가독성) — 한 곳 누적 vs 노트 양산",
    spec=dict(NOTES_SPEC),
    expected="'📥 알림 정리' 한 노트에 모든 알림 요약(원문 포함)이 모여 한눈에 보인다",
    preconditions=[], must_confirm=False, origin="handauthored",
    stimulus={"kind": "notification", "params": {"items": [
        {"title": "택배 도착", "body": "문 앞에 택배가 도착했습니다. 부재시 경비실에 맡겨주세요."},
        {"title": "회의 알림", "body": "3시 팀 회의가 회의실 B에서 시작됩니다."},
        {"title": "메시지", "body": "엄마: 저녁에 집에 오니?"},
    ], "interval": 0.3}},
    fixture="native:notification",
    oracle={"kind": "notes_contains", "params": {"title": "📥 알림 정리", "from_injected": "body"}})

plan = TestPlan("맥 알림을 요약해 '📥 알림 정리' 노트에 누적하는 기능", "notify_loop", [case],
                "claude-opus-4-8", "claude-sonnet-4-6", visio._now(), sut_entry="")

res = visio_loop.run_closed_loop(plan, WORK_SUT, fixer_model="claude-sonnet-4-6",
                                 max_iters=5, out_dir="visio_out/loop_demo",
                                 local_judge="off", pre_iter=cleanup_notes, verbose=True)

cleanup_notes()
print("\n" + "#" * 64)
print(f"# 닫힌 루프 결과: ok={res['ok']}  iters={res['iters']}  ({res.get('reason','수렴')})")
for h in res["history"]:
    print(f"   iter{h['iter']}: pass {h['pass']}/{h['total']}  accepted={h['accepted']}  신뢰닻={h['trust']}")
print("#" * 64)
print(f">>> 기대: 깨진 v1 → VISIO 사용성 fail → fixer 자동수정 → oracle-confirmed pass 수렴")
