#!/usr/bin/env python3
"""A — 닫힌 자율 학습루프 *end-to-end* 확인. (손-심은 교훈이 아니라 *자동 기록된* 교훈으로.)

라운드1: '<<RESET>>'에서 진짜 실패하는 SUT를 run_test_plan으로 돌린다 → VISIO가 FAIL 판정 →
        `_learn_from_case`가 그 약점을 *자동 기록*(사람 개입 0).
검증①: 자동 기록된 교훈이 '<<RESET>>'를 담을 만큼 *구체적*인가? (자동교훈 ≠ 손-심은 완벽교훈일 수 있음)
라운드2: 같은 기능을 *그 자동 기억으로* 다시 설계(`recall_for_design`+`_llm_cases`) vs *콜드*.
검증②: 자동 기억이 다음 설계를 '<<RESET>>' 케이스로 끌고 가나? (콜드는 절대 못 떠올림)

= 진짜 실패 → 자동 기록 → 자동 회상 → 다음 설계가 잡음. 통과 시 닫힌 학습루프 *전체*가 수치로 증명.
"""
import json
import os
import tempfile

from rubi import emeri, visio, taskspec
from rubi.visio import TestCase, TestPlan, run_test_plan, _llm_cases, DEFAULT_JUDGE

FEATURE = "메모 본문을 파일로 저장하는 기능"
BODY = "예산 승인 회의록 <<RESET>> 개발자 2명 채용 출시 6월 30일"   # <<RESET>> 이후가 손실될 본문
K = 2


def targets_weakness(case: dict) -> bool:
    blob = (str(case.get("goal", "")) + " " + str(case.get("title", "")) + " "
            + json.dumps(case.get("stimulus") or {}, ensure_ascii=False))
    return ("reset" in blob.lower()) or ("<<" in blob)


def round1_learn(routines_dir, out_path):
    """진짜 실패 런 → 자동 학습. 반환: (판정, 자동기록 교훈 수)."""
    os.environ["VISIO_NOTE_OUT"] = out_path
    case = TestCase(
        id="reset_case", title="본문에 <<RESET>> 토큰 포함",
        goal=FEATURE, rationale="제어 토큰 오인으로 데이터 손실 검사",
        spec={"task_type": "automation", "app": "", "risk": "low", "requires_confirmation": False,
              "channel": "", "audit_axes": ["intent_sufficiency"], "postconditions": [], "source": "handauthored"},
        expected="본문 전체가 저장된다(<<RESET>> 이후 포함)", preconditions=[], must_confirm=False,
        origin="handauthored",
        stimulus={"kind": "file", "params": {"dir": os.path.dirname(out_path), "name": "in.txt", "content": BODY}},
        fixture="native:file",
        oracle={"authoritative": True, "kind": "file_contains",
                "params": {"path": out_path, "from_injected": "content"}})
    plan = TestPlan(FEATURE, "emeri_e2e", [case], "claude-opus-4-8", "claude-sonnet-4-6",
                    visio._now(), fixture_requests=[], sut_entry=".venv/bin/python sut/reset_bug.py")
    before = len(emeri.load(routines_dir))
    rep = run_test_plan(plan, mode="rehearse", out_dir=tempfile.mkdtemp(prefix="e2e_out_"),
                        routines_dir=routines_dir, local_judge="off", verbose=False)
    os.environ.pop("VISIO_NOTE_OUT", None)
    return rep.results[0].status, len(emeri.load(routines_dir)) - before


def main():
    d = tempfile.mkdtemp(prefix="emeri_e2e_")          # 빈 기억으로 시작
    work = tempfile.mkdtemp(prefix="emeri_e2e_work_")
    print("=" * 78)
    print("A. 닫힌 자율 학습루프 end-to-end — 진짜 실패→자동기록→자동회상→다음설계가 잡나")
    print("=" * 78)

    # 라운드1 — 진짜 실패 + 자동 학습
    status, learned = round1_learn(d, os.path.join(work, "out.txt"))
    lessons = emeri.load(d)
    auto_txt = json.dumps(lessons, ensure_ascii=False)
    specific = "<<RESET>>" in auto_txt
    print(f"\n[라운드1] 진짜 실패 런(run_test_plan, SUT=reset_bug)")
    print(f"  · VISIO 판정: {status}   (기대 fail — <<RESET>> 이후 손실을 오라클이 잡아야)")
    print(f"  · 자동 기록된 교훈: {learned}개")
    print(f"  {'✓' if specific else '✗'} 검증① 자동교훈이 '<<RESET>>'를 담음(구체성): {specific}")
    if lessons:
        e = lessons[-1]
        print(f"       └ 기록된 when: 「{e.get('when','')[:60]}」 kind={e.get('kind')}")

    # 라운드2 — 그 자동 기억으로 다시 설계 vs 콜드
    weak, verified = emeri.recall_for_design(d, FEATURE, task_key=taskspec.classify_goal(FEATURE, None).key())
    print(f"\n[라운드2] 자동 기억으로 재설계 vs 콜드 (K={K})")
    print(f"  설계가 받는 약점 회상(자동): {'있음' if weak else '없음(회상 실패)'}")
    cold = sum(int(any(targets_weakness(c) for c in (_llm_cases(FEATURE, '', '', DEFAULT_JUDGE, 6) or []))) for _ in range(K))
    mem = sum(int(any(targets_weakness(c) for c in (_llm_cases(FEATURE, weak, verified, DEFAULT_JUDGE, 6) or []))) for _ in range(K))
    print(f"  · 콜드(기억없음)  '<<RESET>>' 타깃: {cold}/{K}")
    print(f"  · 자동기억으로    '<<RESET>>' 타깃: {mem}/{K}")

    print("\n" + "=" * 78)
    ok = (status == "fail") and specific and (mem > cold)
    if ok:
        print("⇒ ✅ 닫힌 자율 학습루프 *전체* 증명: 진짜 실패→자동기록(구체적)→자동회상→다음설계가 약점을 잡음.")
        print(f"   (사람 개입 0. 콜드 {cold}/{K} → 자동기억 {mem}/{K}.)")
    else:
        print("⇒ ⚠️ 체인 일부 미흡(정직 보고):")
        print(f"   라운드1 fail={status=='fail'} · 자동교훈 구체성={specific} · 설계 steering(mem>cold)={mem>cold}")
    print("=" * 78)


if __name__ == "__main__":
    main()
