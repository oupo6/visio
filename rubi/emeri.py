"""EMERI — 사피·루비가 *함께 쓰고 읽는* 공유 절차기억(procedural memory).

별도 두뇌가 아니다(보석 이름이지만 사람은 사피·루비 둘). EMERI는 *기억 저장소*다.
루프가 *자기 경험에서 자동으로* 교훈을 쌓는다 — 사람이 직접 박지 않는다.

★기억의 *형태*가 핵심: 린ear 스크립트(activate&&click&&type…)로 저장하면 *시작 상태에 의존*해
  깨진다(예: '이미 송도역을 띄워둔' 상태에서만 통하는 가짜 루틴). 그래서 EMERI는
  **상황→행동 규칙**으로 저장한다: { when:'화면이 이런 상태일 때', then:'이 행동을 하라',
  works:통함/금지 }. 두뇌가 매 순간 *현재 화면*을 그 when 들과 맞춰 알맞은 행동을 떠올린다.

★검증 게이트: RUBI 오라클이 결과를 판정한 *뒤에만* 기록 → works/fails 가 항상 결과로 검증됨
  (두뇌의 '틀린 확신'이 쌓여 기억이 나빠지는 것 방지). 단, 규칙 품질은 RUBI 정확도만큼만 좋다.

읽기: 다음 런 preflight 에서 현재 목표와 관련된 규칙을 토큰 겹침으로 골라 주입(LLM 호출 0).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata

from .provider import _cli_json

_MAX_ENTRIES = 120          # 파일 무한 성장 방지(오래된 것부터 버림)
_RECALL_MIN_OVERLAP = 2     # 현재 목표와 최소 토큰 겹침


def _path(routines_dir: str) -> str:
    return os.path.join(routines_dir, "lessons.json")


def load(routines_dir: str) -> list[dict]:
    p = _path(routines_dir)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


_STOP = {"그리고", "에서", "으로", "까지", "하라", "하고", "해라", "에게", "에는", "있으면",
         "그대로", "비어", "써라", "알아", "읽어", "보고", "현재", "적당한", "이미", "한다",
         "the", "and", "for", "with", "이나", "또는", "통해", "위해", "이런", "그때", "있을"}


def _tokens(s: str) -> set[str]:
    s = unicodedata.normalize("NFC", s or "").lower()
    toks = re.findall(r"[0-9a-z가-힣]+", s)
    return {t for t in toks if len(t) >= 2 and t not in _STOP}


def _key(goal: str) -> str:
    return " ".join(sorted(_tokens(goal)))[:160]


def save_lesson(routines_dir: str, goal: str, when: str, then: str,
                works: bool, verified: bool = True, app: str = "",
                screen_state: str = "", confidence: float = 0.7,
                fail_reason: str = "", alternative: str = "", task_key: str = "",
                axis: str = "", contract_clause: str = "") -> bool:
    """상황→행동 규칙 하나를 저장. works=True(통함)/False(실패·금지). 중복/무한성장 방지.

    ★when/then 으로 저장하므로 *시작 상태에 의존하지 않는다* — 조건(when)이 명시돼야 적용된다."""
    when = (when or "").strip()
    then = (then or "").strip()
    if not when or not then:
        return False
    kind = "works" if works else "fails"
    ls = load(routines_dir)
    wt, tt = _tokens(when), _tokens(then)
    for e in ls:                      # 같은 종류·거의 같은 규칙이면 스킵
        if e.get("kind") == kind and _tokens(e.get("when", "")) == wt and _tokens(e.get("then", "")) == tt:
            return False
    ls.append({
        "goal": goal[:160],
        "key": _key(goal),
        "task_key": task_key[:120],
        "axis": axis[:80],
        "contract_clause": contract_clause[:180],
        "app": app[:80],
        "screen_state": (screen_state or when)[:200],
        "when": when[:200],
        "then": then[:200],
        "kind": kind,
        "verified": bool(verified),
        "confidence": max(0.0, min(float(confidence or 0.0), 1.0)),
        "fail_reason": fail_reason[:180],
        "alternative": alternative[:180],
    })
    if len(ls) > _MAX_ENTRIES:
        ls = ls[-_MAX_ENTRIES:]
    os.makedirs(routines_dir, exist_ok=True)
    with open(_path(routines_dir), "w", encoding="utf-8") as f:
        json.dump(ls, f, ensure_ascii=False, indent=2)
    return True


def recall(routines_dir: str, goal: str, max_items: int = 6, task_key: str = "") -> list[dict]:
    """현재 목표와 관련된 규칙을 반환.

    1순위는 TaskSpec key(message_send:kakaotalk 같은 구조화된 작업 유형)다.
    토큰 겹침은 보조로만 사용한다.
    """
    ls = load(routines_dir)
    if not ls:
        return []
    gtok = _tokens(goal)
    if not gtok:
        return []
    scored = []
    for e in ls:
        if task_key and e.get("task_key") == task_key:
            score = 100.0 + (0.5 if e.get("kind") == "fails" else 0.0) + float(e.get("confidence") or 0)
            scored.append((score, e))
            continue
        etok = _tokens(e.get("goal", "")) | _tokens(e.get("key", "")) | _tokens(e.get("when", ""))
        overlap = len(gtok & etok)
        if overlap >= _RECALL_MIN_OVERLAP:
            score = overlap + (0.5 if e.get("kind") == "fails" else 0.0) + float(e.get("confidence") or 0)
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:max_items]]


def recall_text(routines_dir: str, goal: str, max_items: int = 6, task_key: str = "") -> str:
    """preflight 주입용 한 덩이 텍스트(상황→행동). 관련 규칙 없으면 ''."""
    items = recall(routines_dir, goal, max_items, task_key=task_key)
    if not items:
        return ""
    lines = []
    for e in items:
        scope = ""
        if e.get("axis"):
            scope = f"[{e.get('axis')}" + (f" / {e.get('contract_clause')}" if e.get("contract_clause") else "") + "] "
        if e.get("kind") == "fails":
            alt = f" / 대신: {e.get('alternative')}" if e.get("alternative") else ""
            lines.append(f"- {scope}화면이 「{e.get('when','')}」일 때 → ✗하지마: {e.get('then','')}{alt}")
        else:
            lines.append(f"- {scope}화면이 「{e.get('when','')}」일 때 → ✓이렇게: {e.get('then','')}")
    return ("이 작업에서 *전에 배운 상황별 규칙*(현재 화면이 when 에 맞으면 그 행동을 따르라):\n"
            + "\n".join(lines))


def distill_rules(goal: str, trace: str, achieved: bool, diagnosis: str, model: str) -> list[dict]:
    """이번 실행의 '생각+행동' 기록에서 *재사용 가능한 상황→행동 규칙*을 뽑는다(텍스트만, 값쌈).

    린ear 스크립트가 아니라 {when, then, works} 단위. ★시작 상태 가정 금지 — 전제는 when 에 명시."""
    prompt = (
        "너는 절차기억 증류가다. 어떤 워커(컴퓨터 제어 비서)가 아래 목표를 수행했다. "
        "워커의 '생각+행동' 기록에서 *다음에 재사용할 상황별 규칙*을 뽑아라.\n"
        "★반드시 *상황→행동* 단위로(린ear 스크립트 'A하고 B하고 C하고…' 금지 — 시작 상태에 의존해 깨진다).\n"
        "각 규칙 = {axis:'state_robustness|intent_sufficiency|evidence_adequacy 중 관련 축', "
        "contract_clause:'관련된 RUBI 계약 조항/위반 조항', when:'화면이 이런 상태/단서일 때'(관찰 가능하게 구체적으로), "
        "then:'그때 이 행동을 하라', works: true=이게 통함 / false=이건 실패라 금지, "
        "fail_reason:'실패라면 왜 실패했는가', alternative:'실패라면 다음에 대신 할 전략'}.\n"
        "★★★오직 *실제로 일어난 행동*과 *기록으로 확인되는 결과*에서만 규칙을 뽑아라. 워커가 *시도하지 않은* 행동을 "
        "'통할 것'이라 추측해 works:true 로 적는 것은 *금지*(그건 기억 오염이다 — 너의 추측을 검증된 사실처럼 박지 마라). "
        "어떤 행동이 기록상 *실제로 원하는 효과를 냈을 때만* works:true. 시도했으나 막혔/엉뚱했으면 works:false. "
        "★위 '진단/지시'는 RUBI의 *추측*일 뿐이니 그걸 베껴 규칙으로 만들지 마라.\n"
        "★★★★기록 기준 — *외부세계의 영구사실*만 적어라(앱 UI의 실제 동작·사이트 특성·사용자 선호처럼, 워커의 도구가 "
        "완벽해져도 여전히 참인 것). 판별법: '워커의 클릭·지각·앱실행이 100% 정확했어도 이 사실이 여전히 참인가?' "
        "참이면 기록, 아니면 *절대 기록 금지*.\n"
        "★기록 금지 — 아래는 *우리 도구/시스템 버그의 증상*이지 교훈이 아니다(코드로 고칠 일이지 기억할 일이 아니며, "
        "도구가 고쳐지면 *틀린 교훈*이 되어 미래 런을 망친다):\n"
        "  ❌ 클릭/더블클릭/키가 '안 먹혔다·하이라이트만 됐다·창이 안 열렸다', 그리고 그 *우회책*(예 '클릭 안 되니 Enter로'·'전송버튼 대신 Return으로') — 거의 다 우리 클릭/격리/포커스 버그의 증상이다.\n"
        "  ❌ 화면이 검게 나왔다·창이 안 보였다·앱을 못 찾았다·activate/open이 전면화 안 됐다·포커스가 엉뚱했다 — 전부 도구 영역(지각/실행) 문제다.\n"
        "  ❌ then 에 절대 좌표를 박는 것(예 'smart_click (340,230)') — 창 크기·해상도가 바뀌면 깨진다. 좌표 말고 *무엇을*(라벨·묘사)로 적어라.\n"
        "  ❌ 차단된 셸(mdfind·ls|grep 등)이나 그 우회 — 도구 제약이지 세상 사실이 아니다.\n"
        "✅ 진짜 기록할 것의 예: '이 앱은 파란 길찾기 버튼을 누르면 경로패널이 아니라 장소팝업이 뜬다'(앱의 *실제 동작*), "
        "'이 딥링크 URL로 결과를 바로 연다'(지름길), '전송 전 수신자를 사용자에게 확인'(*선호*). 즉 *세상에 대한 사실*만.\n"
        "★대부분의 *정상 성공* 런에는 새로 기록할 외부세계 사실이 없다 — 그러면 주저 말고 빈 배열.\n"
        "★★시작 상태를 가정하지 마라: '이미 어떤 화면(검색 결과·장소 카드 등)이 떠 있었다'면 그 전제를 *반드시 when 에 명시*하라.\n"
        "★when 은 *과제 설명*이 아니라 *관찰 가능한 화면 상태*여야 한다(예 '…임무가 주어졌을 때' 같은 건 금지).\n"
        "실제로 배운 핵심만 0~4개. **배울 게 없으면(행동 없음·전부 추측) 빈 배열** {\"rules\":[]}. JSON 객체 하나만.\n\n"
        f"## 목표\n{goal}\n"
        f"## 최종 달성 여부(RUBI 판정)\n{achieved}\n"
        f"## 진단/지시(RUBI 추측 — 베끼기 금지)\n{diagnosis or '(없음)'}\n"
        f"## 워커의 실제 생각+행동 기록\n{trace}\n\n"
        '{"rules":[{"axis":"state_robustness","contract_clause":"recipient_verified","when":"...","then":"...","works":true,"fail_reason":"","alternative":""}]}'
    )
    data = _cli_json(prompt, model)
    rules = data.get("rules") if isinstance(data, dict) else None
    return rules if isinstance(rules, list) else []
