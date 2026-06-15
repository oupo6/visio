#!/usr/bin/env python3
"""품질 렌즈 견고화 *검증* — 정확성처럼 품질도 두들겨 본다.
검사 4종: ①변별력(나쁜 산출물 < 좋은 산출물) ②지어내기 가드(좋은데 문제 날조 안 함)
③일관성(같은 입력 N회 동일) ④결정론 교차검증 flag(압축0·run_on 모순 잡힘).
순수 텍스트(맥 조작 0) — `assess_quality` 직접 호출. LLM 텍스트 호출만(이미지 0)."""
from rubi import verify_runner as V

# 공통 원본(여러 날짜·메시지 + 노이즈[쿠팡 광고·인사·악보])
SOURCE = (
    "[2026-05-21] 오전 11:38 모바일에서 확인해주세요. 쿠팡(링크 카드)\n"
    "오후 2:35 배터리 키기(빨간색 버튼), 브레이크 밟고 시동걸기\n오후 2:37 TV먼저 켜기\n"
    "[2026-06-08] 오후 2:00 VISIO 자동전송 테스트 2026-06-08\n오후 4:45 안녕\n"
    "오후 7:38 도미시도시라라솔미파솔솔라라시도파미도도시도미레도도도시도솔미레파파미파미도도\n"
    "[2026-06-10] 오후 1:21 안녕하세요")

# 좋은 산출물 — 날짜별 그룹·압축·노이즈 제거·행동항목 강조
GOOD = ("📥 요약\n"
        "[5/21] 차량: 배터리 키기(빨간 버튼)→브레이크+시동, TV 먼저 켜기\n"
        "[6/8] VISIO 자동전송 테스트 · [음악 메모 1건]\n"
        "[6/10] 인사")

# 나쁜 산출물 — 한 줄 덩어리·압축0(원문≈동일)·노이즈 그대로(현재 SUT 스타일)
BAD = ("📥 카톡 요약 요약: [오전 11:38] 모바일에서 확인해주세요. / 쿠팡 / 2026-05-21 / "
       "[오후 2:35] 배터리 키기(빨간색 버튼), 브레이크 밟고 시동걸기 / [오후 2:37] TV먼저 켜기 / "
       "2026-06-08 / [오후 2:00] VISIO 자동전송 테스트 2026-06-08 / [오후 4:45] 안녕 / "
       "[오후 7:38] 도미시도시라라솔미파솔솔라라시도파미도도시도미레도도도시도솔미레파파미파미도도 / "
       "2026-06-10 / [오후 1:21] 안녕하세요")

# 단순·적절 — 고칠 게 없는 깔끔한 산출물(지어내기 가드용). 원본 없음.
SIMPLE = "회의 메모\n- 14:00 팀 미팅 (안건: 출시 일정)"

RANK = {"good": 2, "fair": 1, "poor": 0, "?": -1}
N = 2   # 일관성용 반복


def run(name, produced, source):
    res = [V.assess_quality(produced, source=source) for _ in range(N)]
    us = [r["usability"] for r in res]
    print(f"[{name}] usability={us}  flags={res[0]['flags']}  suggestions={len(res[0]['suggestions'])}개"
          f"  signals={res[0]['signals']}")
    return res


print("=" * 64)
print("품질 렌즈 검증 — 변별력 / 지어내기 가드 / 일관성 / 결정론 flag")
print("=" * 64)
g = run("GOOD  ", GOOD, SOURCE)
b = run("BAD   ", BAD, SOURCE)
s = run("SIMPLE", SIMPLE, "")

print("\n" + "-" * 64)
# ① 변별력: BAD 가 GOOD 보다 나쁘게 평가돼야
disc = RANK[b[0]["usability"]] < RANK[g[0]["usability"]]
print(f"① 변별력 (BAD < GOOD):           {'✅' if disc else '❌'}  "
      f"(GOOD={g[0]['usability']} > BAD={b[0]['usability']})")
# ② 지어내기 가드: SIMPLE 은 good 이고 major 개선점 날조 없어야(<=1)
invent = RANK[s[0]["usability"]] < 1 or len(s[0]["suggestions"]) > 1
print(f"② 지어내기 가드 (SIMPLE 깨끗):     {'❌ 날조' if invent else '✅'}  "
      f"(SIMPLE={s[0]['usability']}, 제안 {len(s[0]['suggestions'])}개)")
# ③ 일관성 — 두 수준으로 *투명하게*:
#   strict   = 정확한 라벨(good/fair/poor) 동일?  (good↔fair 미세 흔들림은 자문·저영향)
#   decision = poor-vs-not(=개선 필요냐, *행동 갈리는* 경계) 동일?  ← 의미 있는 기준
strict = all(len(set(r["usability"] for r in grp)) == 1 for grp in (g, b, s))
cons = all(len({r["usability"] == "poor" for r in grp}) == 1 for grp in (g, b, s))
print(f"③ 일관성 strict(라벨 정확):       {'✅' if strict else '⚠️ good↔fair 미세 흔들림(자문)'}")
print(f"③ 일관성 decision(poor-vs-not):  {'✅' if cons else '❌'}  ← 행동 갈리는 경계")
# ④ 결정론 교차검증: BAD(압축0·run_on)에 flag 떠야(LLM이 혹시 good 줄 때 안전망)
#    — BAD 가 poor 면 flag 안 떠도 정상(모순 없음). flag 메커니즘 자체는 GOOD-오판 시 작동.
det_ok = (b[0]["usability"] != "good") or bool(b[0]["flags"])
print(f"④ 결정론 flag (BAD 모순 방지):     {'✅' if det_ok else '❌ good인데 flag 없음'}")

passed = sum([disc, not invent, cons, det_ok])
print("-" * 64)
print(f"품질 렌즈 검증: {passed}/4 통과")
print("=" * 64)
