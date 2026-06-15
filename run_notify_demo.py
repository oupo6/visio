#!/usr/bin/env python3
"""VISIO end-to-end 데모: '알림→요약→Notes' SUT를 VISIO가 *결정론 자극*으로 검증.

워커 즉흥 0 — VISIO가 알림을 직접 쏘고(triggers), 빌드된 SUT를 트리거하고, 독립 판정.
계획은 (속도 위해) 손으로 박은 2케이스. 시나리오 *생성*은 별도로 이미 검증함.
"""
from rubi import visio
from rubi.visio import TestCase, TestPlan, run_test_plan, print_summary, print_regression

NOTES_SPEC = {
    "task_type": "automation", "app": "Notes", "risk": "low",
    "requires_confirmation": False, "channel": "",
    "audit_axes": ["intent_sufficiency", "evidence_adequacy"],
    "postconditions": ["Notes에 알림을 요약한 새 노트가 보인다"],
    "source": "handauthored",
}

cases = [
    TestCase(
        id="happy_parcel",
        title="정상: 택배 알림 요약→Notes 저장",
        goal="맥 알림('택배 도착')을 요약해서 Apple Notes에 새 노트로 저장한다",
        rationale="해피패스 — 알림 본문이 요약돼 Notes에 실제 저장되는가",
        spec=dict(NOTES_SPEC),
        expected="Notes에 'VISIO 알림요약' 노트가 있고, 택배 알림 본문이 요약돼 보인다",
        preconditions=[], must_confirm=False, origin="handauthored",
        stimulus={"kind": "notification",
                  "params": {"title": "택배 도착",
                             "body": "문 앞에 택배가 도착했습니다. 부재시 경비실에 맡겨주세요."}},
        fixture="native:notification"),
    TestCase(
        id="empty_body",
        title="엣지: 본문 없는 알림 graceful 처리",
        goal="본문이 빈 알림도 크래시 없이 Notes에 처리한다",
        rationale="경계 — 빈 본문에서 요약기가 죽거나 빈 노트를 만들지 않는가",
        spec=dict(NOTES_SPEC),
        expected="본문 없는 알림도 노트가 생성되고 '(본문 없음)'으로 graceful 처리된다",
        preconditions=[], must_confirm=False, origin="handauthored",
        stimulus={"kind": "notification",
                  "params": {"title": "빈 알림 테스트", "body": ""}},
        fixture="native:notification"),
]

plan = TestPlan(
    "맥 알림을 요약해 Notes에 저장하는 기능", "notify_to_notes", cases,
    "claude-opus-4-8", "claude-sonnet-4-6", visio._now(),
    fixture_requests=[], sut_entry=".venv/bin/python sut/notify_to_notes.py")

print("▶ SUT:", plan.sut_entry)
rep = run_test_plan(plan, mode="rehearse", out_dir="visio_out/notify_demo",
                    local_judge="off", verbose=True)   # off=클라우드 판정(빠름)
print_summary(rep)
print_regression(rep)
