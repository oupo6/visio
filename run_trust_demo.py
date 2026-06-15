#!/usr/bin/env python3
"""신뢰 바닥(③) 증명: 결정론 오라클이 LLM 판정의 *닻/거부권*이 되는지.
  RUN1 좋은 SUT(v2)   → pass + 신뢰닻 'oracle-confirmed' (초록불이 실제 상태로 뒷받침)
  RUN2 거짓 SUT(wrong) → 노트는 있으나 주입내용 누락 → 오라클 거부 → fail 'oracle-vetoed'
오라클: probes.notes_contains(주입한 알림 본문이 노트에 실제 들어갔나). LLM은 속을 수 있어도 닻은 못 속임.
"""
import subprocess

from rubi import visio
from rubi.visio import TestCase, TestPlan, run_test_plan, print_summary


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


NOTES_SPEC = {"task_type": "automation", "app": "Notes", "risk": "low",
              "requires_confirmation": False, "audit_axes": ["intent_sufficiency", "evidence_adequacy"],
              "source": "handauthored"}

BODY = "문 앞에 택배가 도착했습니다. 부재시 경비실에 맡겨주세요."

case = TestCase(
    id="parcel_trust",
    title="신뢰닻: 택배 알림이 *실제로* 노트에 저장됐나",
    goal="맥 알림('택배 도착')을 요약해 '📥 알림 정리' 노트에 저장한다",
    rationale="결정론 오라클로 주입 내용이 실제 노트에 들어갔는지 확인(거짓 pass 차단)",
    spec=dict(NOTES_SPEC),
    expected="'📥 알림 정리' 노트에 택배 알림 본문이 들어가 있다",
    preconditions=[], must_confirm=False, origin="handauthored",
    stimulus={"kind": "notification", "params": {"title": "택배 도착", "body": BODY}},
    fixture="native:notification",
    oracle={"kind": "notes_contains", "params": {"title": "📥 알림 정리", "from_injected": "body"}})

plan = TestPlan("맥 알림을 요약해 Notes에 저장하는 기능", "notify_trust", [case],
                "claude-opus-4-8", "claude-sonnet-4-6", visio._now(),
                fixture_requests=[], sut_entry="")

print("정리:", cleanup_notes() or "ok")

print("\n########## RUN 1 — 좋은 SUT (v2, 실제 내용 저장) ##########")
plan.sut_entry = ".venv/bin/python sut/notify_to_notes.py"
rep1 = run_test_plan(plan, mode="rehearse", out_dir="visio_out/trust_demo", local_judge="off", verbose=True)
print_summary(rep1)

print("\n정리:", cleanup_notes() or "ok")

print("\n########## RUN 2 — 거짓 SUT (제목만, 본문 누락) · *약한 판정자 gemma4* ##########")
print("   (약한 판정자가 그럴듯한 노트에 속을 수 있음 → 오라클이 거부권으로 잡는지)")
plan.sut_entry = ".venv/bin/python sut/notify_to_notes_wrong.py"
rep2 = run_test_plan(plan, mode="rehearse", out_dir="visio_out/trust_demo", local_judge="on", verbose=True)
print_summary(rep2)

print("\n최종 정리:", cleanup_notes() or "ok")
r1, r2 = rep1.results[0], rep2.results[0]
print(f"\n>>> RUN1 좋은SUT(cloud):   {r1.status} / 신뢰닻 {r1.trust}")
print(f">>> RUN2 거짓SUT(gemma4):  {r2.status} / 신뢰닻 {r2.trust}")
print(f"    RUN2 판정자(gemma4) 자체 achieved={r2.verdict.get('achieved')} → "
      f"{'★오라클이 거짓pass를 거부(veto)!' if r2.verdict.get('achieved') and r2.status=='fail' else '판정자도 잡음 → 오라클이 fail 확인'}")
