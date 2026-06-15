#!/usr/bin/env python3
"""설계 두뇌 v1 — *코드-이해 레버* 측정. (코어 건드리기 전 자체 벤치로 가치부터.)

설계의 새 힘 = UNDERSTAND: 기능 *구현(코드/산출물)*을 읽어 *이 기능 고유의 위험표면*을 뽑고,
그걸 근거로 케이스 설계. (기억=과거경험과 별개의 비-자명 위험원 = 현재 구조.)

측정: `sut/reset_bug.py` 는 코드에 `content.split("<<RESET>>")[0]`(토큰 이후 폐기) 버그가 *보인다*.
  · 설명만 보는 설계 → '<<RESET>>'를 절대 못 떠올림(임의 토큰)
  · 코드를 읽는 설계 → 그 split 을 짚어 '<<RESET>>' 케이스를 *스스로* 설계
→ 코드-이해가 *코드-특정 비-자명 위험*을 잡는지 수치로. (정직: 차이 없으면 레버 무가치.)
"""
import json
import tempfile

from rubi.provider import _cli_json

FEATURE = "텍스트 본문을 파일로 저장하는 기능"
CODE = open("sut/reset_bug.py", encoding="utf-8").read()
MODEL = "claude-opus-4-8"
K = 3


def design(feature: str, code: str) -> list:
    """UNDERSTAND(위험표면) → HYPOTHESIZE(케이스). code='' 면 설명만(기존 방식)."""
    code_block = f"\n## 구현 코드(실제 결함을 *코드 근거로* 찾아라)\n```python\n{code[:1800]}\n```\n" if code else ""
    prompt = (
        f"너는 테스트 설계자다. 아래 기능이 *어떤 입력/상태에서 깨질지* 가장 잘 드러낼 케이스를 설계하라.\n"
        f"## 기능\n{feature}\n{code_block}\n"
        + ("★코드가 있으면: 그 코드의 *구체적 결함 가능성*(특정 토큰·분기·미처리 입력)을 먼저 짚고, "
           "그걸 직접 건드리는 케이스를 반드시 포함하라.\n" if code else "")
        + '최대 6개. ONLY JSON: {"cases":[{"title":"...","stimulus":{"params":{"content":"<이 케이스가 넣을 실제 입력>"}}}]}')
    data = _cli_json(prompt, MODEL)
    return (data.get("cases") if isinstance(data, dict) else None) or []


def targets_weakness(case: dict) -> bool:
    blob = json.dumps(case, ensure_ascii=False)
    return ("reset" in blob.lower()) or ("<<" in blob)


def run(label, code):
    hits, samples = 0, []
    for _ in range(K):
        cases = design(FEATURE, code)
        hits += int(any(targets_weakness(c) for c in cases))
        samples.append([(c.get("title") or "?")[:30] for c in cases])
    return hits, samples


def main():
    print("=" * 78)
    print("설계 두뇌 v1 — 코드-이해 레버 측정 (설명만 vs 코드읽음, K=%d)" % K)
    print(f"코드-특정 버그: reset_bug.py 의 split('<<RESET>>')[0] → 토큰 이후 폐기  ·  {MODEL}")
    print("=" * 78)
    desc_h, desc_s = run("설명만", "")
    code_h, code_s = run("코드읽음", CODE)
    print(f"\n{'설계 입력':<14}{'위험 적발':<12}생성 케이스 샘플")
    print("-" * 78)
    for label, h, s in [("설명만(기존)", desc_h, desc_s), ("코드읽음(v1)", code_h, code_s)]:
        print(f"{label:<14}{h}/{K:<10}{s[0]}")
        for extra in s[1:]:
            print(f"{'':<26}{extra}")
    print("-" * 78)
    d = code_h - desc_h
    print(f"⇒ 코드-특정 위험 적발: 설명만 {desc_h}/{K} → 코드읽음 {code_h}/{K}  (Δ={d:+d})")
    if d > 0:
        print("  ✅ 코드-이해가 *설명만으론 못 보는 코드-특정 비-자명 위험*을 설계로 끌어냄 = 설계 v1 레버 유효.")
    else:
        print("  ◐/⚠️ 코드-이해가 설계를 유의하게 개선 못 함(정직 발견) — 레버 재고 필요.")
    print("=" * 78)


if __name__ == "__main__":
    main()
