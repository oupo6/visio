#!/usr/bin/env python3
"""P3/학습-가치 절제 — "닫힌 기억이 *실제로 테스트 설계를 더 낫게* 하나?"를 측정.

P0에서 학습 루프를 닫았다(real run이 교훈 쌓음). 근데 Δ>0은 "저장된다"만 증명하지 "그래서 더 잘 잡는다"는 아님.
여기서 진짜 질문: **콜드(기억 없음) 설계가 *놓치는* 비-자명 약점을, 기억 있는 설계가 *타깃하나?***

메커니즘: 기억은 `generate_test_plan → _llm_cases(feature, prior)` 에서 소비된다(설계 LLM이 prior 읽음).
그래서 같은 기능에 대해 _llm_cases 를 **prior 없이(콜드)** vs **prior=회상교훈(기억)** 으로 K회씩 돌려,
*비-자명 약점*(=알림 본문에 큰따옴표 → AppleScript 이스케이프 실패; 일반 설계자가 잘 안 떠올림)을
타깃하는 케이스를 생성하는 빈도를 비교한다. 기억이 그 빈도를 *유의하게 올리면* = 가치 증명.
(LLM 변동성 → K회. 정직: 차이 없거나 줄면 그것도 발견 — 회상 프레이밍이 잘못됐다는 신호.)
"""
import json
import tempfile

from rubi import emeri, taskspec
from rubi.visio import _llm_cases, DEFAULT_JUDGE

FEATURE = "맥 알림을 요약해 Apple Notes에 저장하는 기능"
TASK_KEY = taskspec.classify_goal(FEATURE, None).key()
K = 3
DESIGN_MODEL = DEFAULT_JUDGE

# ★경험으로만 알 수 있는 약점(매직 토큰) — 기능 설명만 보고는 *절대* 못 떠올림. 그래야 기억의 순수 가치가 보임.
#   (큰따옴표·특수문자 같은 건 센 설계자가 원래 다 생성 → 기억 기여를 가림. 임의 토큰은 회상해야만 나옴.)
WEAK_WHEN = "알림 본문에 '<<RESET>>' 라는 토큰 문자열이 포함될 때"
WEAK_THEN = "요약기가 '<<RESET>>' 이후의 내용을 전부 버려 본문이 잘린다(데이터 손실)"


def targets_weakness(case: dict) -> bool:
    blob = (str(case.get("goal", "")) + " " + str(case.get("title", "")) + " "
            + json.dumps(case.get("stimulus") or {}, ensure_ascii=False))
    low = blob.lower()
    return ("reset" in low) or ("<<" in blob)   # 임의 매직 토큰 — 기능설명만으론 나올 수 없음


def run_condition(label, weak, verified):
    hits, samples = 0, []
    for _ in range(K):
        cases = _llm_cases(FEATURE, weak, verified, DESIGN_MODEL, 6) or []
        hit = any(targets_weakness(c) for c in cases)
        hits += int(hit)
        samples.append([(c.get("title") or c.get("goal") or "?")[:26] for c in cases])
    return hits, samples


def main():
    d = tempfile.mkdtemp(prefix="emeri_val_")
    emeri.save_lesson(d, goal=FEATURE, when=WEAK_WHEN, then=WEAK_THEN, works=False,
                      task_key=TASK_KEY, axis="state_robustness",
                      fail_reason="'<<RESET>>' 토큰을 제어문으로 오인해 이후 본문 폐기", confidence=0.9)
    weak, verified = emeri.recall_for_design(d, FEATURE, task_key=TASK_KEY)

    print("=" * 78)
    print("학습-가치 절제 — 기억이 *비-자명 약점*으로 설계를 끌고 가나 (콜드 vs 기억, K=%d)" % K)
    print(f"약점(경험으로만 앎): 본문 '<<RESET>>' 토큰→이후 폐기  ·  설계모델={DESIGN_MODEL}")
    print("=" * 78)
    print("설계에 주입되는 약점 회상(weak):")
    print("  " + (weak.replace("\n", "\n  ") if weak else "(비어있음 — 회상 실패!)"))
    print("-" * 78)

    cold_h, cold_s = run_condition("콜드", "", "")
    mem_h, mem_s = run_condition("기억", weak, verified)

    print(f"\n{'조건':<14}{'약점 타깃':<12}생성 케이스 샘플(타이틀)")
    print("-" * 78)
    for label, h, s in [("콜드(기억없음)", cold_h, cold_s), ("기억있음", mem_h, mem_s)]:
        print(f"{label:<14}{h}/{K:<10}{s[0]}")
        for extra in s[1:]:
            print(f"{'':<26}{extra}")
    print("-" * 78)
    delta = mem_h - cold_h
    print(f"⇒ 약점 타깃률: 콜드 {cold_h}/{K} → 기억 {mem_h}/{K}  (Δ={delta:+d})")
    if delta > 0:
        print("  ✅ 기억이 비-자명 약점으로 설계를 끌고 감 = 학습 루프가 *쓸모있음*(닫힘→가치).")
    elif delta == 0 and mem_h == K:
        print("  ◐ 둘 다 항상 타깃 — 이 약점이 충분히 비-자명하지 않았거나 설계자가 원래 잘 잡음(약점 더 obscure화 필요).")
    else:
        print("  ⚠️ 기억이 설계를 *개선 안 함*(혹은 악화) — 정직한 발견. 원인 후보:")
        print("     회상이 '## 이미 학습한 규칙(중복 만들지 마라)'로 주입돼 약점을 *건너뛰게* 함(프레이밍 오류).")
        print("     → 다음: fails(약점) 교훈을 '우선 재검사'로 프레이밍 분리 후 재측정.")
    print("=" * 78)


if __name__ == "__main__":
    main()
