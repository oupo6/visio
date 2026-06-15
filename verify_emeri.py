#!/usr/bin/env python3
"""P0 — EMERI(기억) 절제/배선 측정. "기억이 *다음 테스트를 실제로 낫게 하는가*"를 한 번도 안 쟀다.

두 가지를 측정한다(LLM 0회 — 전부 결정론):
  A. 회상 품질 — 과거 교훈(특히 *약점=fails*)을 관련 작업에서 정확히 떠올리나? 토큰겹침 미끼에 안 속나?
  B. 학습 루프 배선 — recall(read)이 *테스트 설계*에 연결돼 있나? 그리고 real run이 교훈을 *쌓나*(write)?
     (B-write 가 핵심: 회상이 아무리 좋아도 real run 이 교훈을 안 쌓으면 회상할 게 없다 = 루프가 열림.)
"""
import os
import tempfile

from rubi import emeri
from rubi.visio import TestCase, TestPlan, run_test_plan
from rubi import visio

NOTES_TASK = "automation:notes"


def seed_memory(d):
    """라운드1에서 '배운' 것처럼 교훈을 심는다 — 같은작업 약점/성공 + 다른작업 미끼들."""
    # L1 = 타깃 약점(fails): Notes 작업은 '빈 본문'에서 약했다
    emeri.save_lesson(d, goal="맥 알림을 Notes에 저장", when="알림 본문이 비어 있을 때",
                      then="빈 노트를 그대로 만든다", works=False, task_key=NOTES_TASK,
                      axis="state_robustness", fail_reason="빈 본문 graceful 처리 안 함",
                      alternative="'(본문 없음)'으로 표기", confidence=0.9)
    # L2 = 같은작업 성공(works)
    emeri.save_lesson(d, goal="맥 알림을 Notes에 저장", when="알림 본문이 있을 때",
                      then="단일 누적 노트에 append", works=True, task_key=NOTES_TASK,
                      axis="intent_sufficiency", confidence=0.8)
    # L3 = 다른작업 미끼(task_key 다름)
    emeri.save_lesson(d, goal="카톡으로 메시지 보내기", when="입력창에 초안이 있을 때",
                      then="확인 없이 전송", works=False, task_key="message_send:kakaotalk", confidence=0.8)
    # L4 = 토큰 미끼(‘알림/저장’ 토큰 겹치나 작업은 무관)
    emeri.save_lesson(d, goal="알림 소리 저장 설정 변경", when="설정 화면일 때",
                      then="소리 토글 저장", works=True, task_key="settings:sound", confidence=0.7)


def part_a():
    print("=" * 74)
    print("A. 회상 품질 (결정론) — 과거 약점/성공을 관련 작업에서 정확히 떠올리나")
    print("=" * 74)
    d = tempfile.mkdtemp(prefix="emeri_A_")
    seed_memory(d)
    q = "맥 알림을 Apple Notes에 요약 저장"
    checks = []

    # A1 task_key 정밀도: automation:notes 만 떠오르고 미끼(L3/L4) 배제
    r1 = emeri.recall(d, q, task_key=NOTES_TASK)
    keys1 = [e.get("task_key") for e in r1]
    a1 = bool(r1) and all(k == NOTES_TASK for k in keys1)
    checks.append(("A1 task_key 정밀(미끼 배제)", a1, f"떠오른 task_key={keys1}"))

    # A2 약점 우선: 같은작업 중 fails(L1)가 works(L2)보다 먼저
    a2 = len(r1) >= 2 and r1[0].get("kind") == "fails" and "비어" in r1[0].get("when", "")
    checks.append(("A2 약점(fails) 최우선 회상", a2, f"1순위 kind={r1[0].get('kind') if r1 else '-'} when={r1[0].get('when','') if r1 else '-'}"))

    # A3 토큰-only(task_key 없이): 관련 떠오르나 + 미끼 오염 정도(정직 특성화)
    r3 = emeri.recall(d, "알림 저장", task_key="")
    notes_n = sum(1 for e in r3 if e.get("task_key") == NOTES_TASK)
    distract_n = sum(1 for e in r3 if e.get("task_key") != NOTES_TASK)
    a3 = notes_n >= 1
    checks.append(("A3 토큰-only 회상(관련 ≥1)", a3, f"관련 {notes_n} · 미끼 {distract_n} (토큰겹침 보조경로 특성)"))

    # A4 무관 질의 → 헛회상 0
    r4 = emeri.recall(d, "오늘 날씨 우산 챙길까", task_key="")
    a4 = len(r4) == 0
    checks.append(("A4 무관 질의→헛회상 0", a4, f"떠오른 수={len(r4)}"))

    for name, ok, ev in checks:
        print(f"  {'✓' if ok else '✗'} {name:<26} — {ev}")
    return all(ok for _, ok, _ in checks)


def part_b():
    print("\n" + "=" * 74)
    print("B. 학습 루프 배선 — read(설계에 주입) / write(real run이 교훈 쌓나)")
    print("=" * 74)
    d = tempfile.mkdtemp(prefix="emeri_B_")
    seed_memory(d)

    # B-read: 회상이 *테스트 설계*에 흘러가나 — generate_test_plan(visio.py:157)이 recall_text 주입
    prior = emeri.recall_text(d, "맥 알림을 Notes에 저장", task_key=NOTES_TASK)
    b_read = bool(prior) and "비어" in prior   # 과거 '빈 본문 약점'이 설계에 들어갈 수 있나
    print(f"  {'✓' if b_read else '✗'} B-read: 회상이 설계에 주입됨 (generate_test_plan→_llm_cases(prior))")
    print(f"       └ 설계가 받는 교훈: {prior.splitlines()[1][:60] if b_read else '(없음)'} …")

    # B-write: real run(run_test_plan)이 끝난 뒤 교훈이 *쌓이나* — 핵심 측정
    before = len(emeri.load(d))
    work = tempfile.mkdtemp(prefix="emeri_bw_")
    os.environ["VISIO_NOTE_IN"] = os.path.join(work, "in.txt")
    os.environ["VISIO_NOTE_OUT"] = os.path.join(work, "out.txt")
    case = TestCase(
        id="bwrite", title="파일 저장 기능", goal="텍스트를 파일로 저장",
        spec={"task_type": "automation", "app": "", "risk": "low", "requires_confirmation": False,
              "channel": "", "audit_axes": ["intent_sufficiency"], "postconditions": [], "source": "handauthored"},
        expected="출력 파일에 본문이 저장된다", preconditions=[], must_confirm=False, origin="handauthored",
        stimulus={"kind": "file", "params": {"dir": work, "name": "in.txt", "content": "EMERI B-write 본문 ABC"}},
        fixture="native:file",
        oracle={"authoritative": True, "kind": "file_contains",
                "params": {"path": os.path.join(work, "out.txt"), "from_injected": "content"}})
    plan = TestPlan("텍스트를 파일로 저장", "emeri_bwrite", [case],
                    "claude-opus-4-8", "claude-sonnet-4-6", visio._now(),
                    fixture_requests=[], sut_entry=".venv/bin/python sut/note_saver.py")
    verdict = "?"
    try:
        rep = run_test_plan(plan, mode="rehearse", out_dir=tempfile.mkdtemp(prefix="emeri_out_"),
                            routines_dir=d, local_judge="off", verbose=False)
        verdict = rep.results[0].status
    except Exception as e:
        verdict = f"error({type(e).__name__})"
    finally:
        os.environ.pop("VISIO_NOTE_IN", None)
        os.environ.pop("VISIO_NOTE_OUT", None)
    after = len(emeri.load(d))
    learned = after - before
    b_write = learned > 0
    print(f"  {'✓' if b_write else '✗'} B-write: real run 후 교훈 축적  (런 판정={verdict}, 교훈 {before}→{after}, Δ={learned})")
    if not b_write:
        print("       └ ★real run(run_test_plan)이 distill_rules/save_lesson 을 *안 부름* → 경험에서 학습 0.")
    return b_read, b_write


if __name__ == "__main__":
    a_ok = part_a()
    b_read, b_write = part_b()
    print("\n" + "=" * 74)
    print("⇒ P0 결론")
    print(f"   · 회상 품질(A): {'견고' if a_ok else '문제 있음(위 ✗ 확인)'}")
    print(f"   · read 배선:  {'✓ 회상→테스트 설계 연결됨' if b_read else '✗'}")
    print(f"   · write 배선: {'✓ real run이 학습함' if b_write else '✗ real run이 교훈을 안 쌓음 = 학습 루프 *열림*'}")
    if not b_write:
        print("   ⇒ EMERI는 *떠올릴 수는* 있고 그게 *설계에 들어가게도* 돼 있으나,")
        print("      real run이 교훈을 *쌓지 않아* 실제로는 떠올릴 경험이 안 생긴다.")
        print("      = '저번에 약했지→더 본다'가 작동하려면 P4: run_test_plan에 distill_rules/save_lesson 배선 필요.")
    print("=" * 74)
