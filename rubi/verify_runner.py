"""RUBI 결과 검증관 — 에이전트의 자기보고('done')를 믿지 않고, 실제 최종 화면을
외부 오라클로 검증한다. "성공했다는 주장 ≠ 실제 성공"을 잡는 핵심 안전장치.

오라클 문제의 핵심: 'done'은 에이전트가 자기 숙제를 자기가 채점한 것(순환).
RUBI 가 외부 닻(실제 화면)에 비춰 진짜 달성됐는지 독립 판정한다.
"""

from __future__ import annotations

import glob
import os
import re

from .provider import _cli_json
from . import oracles

VERIFY_MODEL = "claude-sonnet-4-6"   # 비전 검증(가성비)


def _toks(s: str) -> set:
    return {t for t in re.findall(r"[0-9A-Za-z가-힣]+", (s or "").lower()) if len(t) >= 2}


def _latest_shot(shots_dir: str) -> str | None:
    shots = sorted(glob.glob(os.path.join(shots_dir, "step_*.png")))
    return shots[-1] if shots else None


def verify_outcome(goal: str, screenshot_path: str, model: str,
                   trace: str | None = None, tried: list[str] | None = None,
                   task_contract: str | None = None, provider: str | None = None) -> dict:
    trace_block = f"## 워커가 이번에 실제로 한 일(trace)\n{trace}\n\n" if trace else ""
    tried_block = ""
    if tried:
        tried_block = (
            "## 이미 시도했다가 실패한 접근들 (★절대 반복 금지)\n"
            + "\n".join(f"- {t}" for t in tried)
            + "\n→ 위 접근들은 안 통했다. 같은 방법의 변형 말고 *근본적으로 다른 전략*을 제시하라.\n\n")
    deterministic_items = oracles.collect(goal, screenshot_path, trace)
    deterministic = oracles.format_evidence(deterministic_items)
    contract_block = f"## RUBI 런타임 3렌즈 계약\n{task_contract}\n\n" if task_contract else (
        "## RUBI 런타임 3렌즈 계약\n"
        "명시 계약 없음. 그래도 intent_sufficiency 와 evidence_adequacy 를 최소 축으로 검사하라.\n\n")
    prompt = (
        "너는 외부 검증관이자 진단가다. 어떤 에이전트(워커)가 작업을 '완료(done)'했다고 주장했다. "
        "자기보고는 절대 믿지 말고, 아래 최종 화면 스크린샷(과 워커가 한 일)을 보고 목표가 '실제로' 달성됐는지 독립 판정하라.\n"
        "RUBI의 핵심은 자유 비평이 아니라 **3개 CUA 런타임 렌즈의 계약 조항 검사**다(달성/correctness):\n"
        "  - state_robustness: 시작/화면 상태를 잘못 가정하지 않았는가.\n"
        "  - intent_sufficiency: 사용자가 원한 최종 산출물을 충분히 만들었는가(존재·정확).\n"
        "  - evidence_adequacy: 성공을 뒷받침하는 외부 증거가 충분한가.\n"
        "계약에 있는 audit_axes 만 검사하라. 각 axis 는 pass/fail/uncertain 중 하나로 판정하고, "
        "실패하면 어떤 조항을 어겼는지 violated_clause 에 적어라.\n"
        "또한 **일반 비기능 품질 축**(quality_axes)을 평가하라 — 도메인 무관, *관련 있을 때만*(목표/사용맥락이 "
        "함의하지 않으면 na). 기능이 '됐나'를 넘어 '잘 됐나'를 본다:\n"
        "  - usability: 산출물이 사람이 *실제로 쓰기 좋은가*(가독성·정리·일관성).\n"
        "  - efficiency_cost: 불필요한 자원/시간/반복 낭비는 없는가(예: 항목마다 산출물 양산·무한 증식·과도한 호출).\n"
        "  - robustness: 경계·대량·빈/깨진 입력·예상 못한 상태에서 견디는가.\n"
        "  - safety_privacy: 비가역/위험 행위나 민감정보 노출 위험은 없는가.\n"
        "★*관련 있는*(na 아닌) 품질 축이 fail 이면 achieved=false 다 — 기능은 됐어도 '잘 된 것'이 아니다. "
        "단 목표가 함의하지 않는 품질을 *지어내지* 마라(단발·일회성·소규모엔 해당 축 na).\n"
        "중요: 계약이 '전면/최대화'를 요구하지 않는 한, 창이 작거나 다른 창이 함께 보여도 "
        "요청한 결과가 의도한 앱/문서 안에 사람 눈으로 읽을 수 있게 보이면 그것만으로 실패 처리하지 마라. "
        "반대로 OCR 텍스트가 터미널 로그/명령 인자에서만 나온 경우는 성공 증거로 삼지 마라. "
        "스크린샷에서 실제 대상 앱의 콘텐츠 영역에 결과가 보이는지와 trace 를 함께 구분하라.\n"
        "★고도(altitude): 글자단위 전사·사소한 표기 차이나 무의미·조밀 문자열은 결함이 아니다 — *의미·결과*가 맞는지로 판정하라. "
        "증거로 신뢰성있게 확인 *못 하는* 축은 추측 말고 verdict=uncertain 으로 둬라(거짓 PASS/FAIL 금지).\n"
        "★미달성이면 두 형식으로 답하라(청중이 다르다):\n"
        "  - diagnosis: '사람'이 읽을 진단 — 왜 막혔는지 간단·명확히.\n"
        "  - directive: '워커(AI)'에게 줄 다음 지시 — 너는 워커의 프롬프트 엔지니어다. *간결한 명령형*, 토큰 낭비 없이. "
        "이미 실패한 접근은 반복하지 말고, 막힌 지점을 근거로 *다른 전략*을 제시하라. "
        "효율 사다리(위에서부터: 셸/URL 직접 → AppleScript(osascript) → GUI는 키보드(Tab/⌘단축키/타이핑) → "
        "마지막에야 마우스 좌표 클릭 비전)를 기억하라 — 마우스 좌표 클릭이 가장 무거우니 최후. 한 티어가 실패했으면 같은 티어 변형 말고 "
        "다른 티어/전략(예: 셸 실패→AppleScript, AppleScript 실패→GUI 정공법, 또는 중간단계 분해·정보부터 확보)을 지시하라.\n\n"
        f"## 목표\n{goal}\n\n"
        f"{contract_block}"
        f"{trace_block}{tried_block}"
        f"## 싼 결정적 관찰 신호\n{deterministic}\n\n"
        f"## 최종 화면\n스크린샷: `{os.path.abspath(screenshot_path)}` — 읽어서 확인하라.\n\n"
        '판정하라. JSON 스키마로만:\n'
        '{"achieved":bool,'
        '"axis_results":[{"axis":"state_robustness|intent_sufficiency|evidence_adequacy",'
        '"verdict":"pass|fail|uncertain","violated_clause":str,"evidence":str}],'
        '"quality_axes":[{"axis":"usability|efficiency_cost|robustness|safety_privacy",'
        '"verdict":"pass|fail|na","note":str}],'
        '"evidence":str,"rationale":str,"diagnosis":str,"directive":str}'
    )
    data = _cli_json(prompt, model, image_path=screenshot_path, provider=provider)
    data["oracle_evidence"] = oracles.to_dicts(deterministic_items)
    if "axis_results" not in data:
        data["axis_results"] = []
    if "quality_axes" not in data:
        data["quality_axes"] = []
    return data


def verify_faithfulness(produced: str, source_read: str, source_ocr: str = "", *,
                        model: str = VERIFY_MODEL, provider: str | None = None,
                        quality: bool = True, floor: float = 0.2) -> dict:
    """일반 *산출물 vs 원본* 충실도 판정 — '요약·추출·전사' 류 기능의 재사용 판정자.
    카톡 e2e에서 검증된 견고화: 결정론 OCR(source_ocr)로 *환각* 주장을 교차검증해 강등
    (원본에 실재하면 환각 아님 — 안전). ★누락은 OCR-부재로 깎지 않는다(OCR은 Claude비전보다
    약해 링크카드/이미지를 놓침 → 깎으면 *진짜 누락이 통과=거짓PASS*); 누락은 판정자 severity로만 보수 처리.
    + 의미고도(전사노이즈 무시) + 판정불가(unverifiable=False 아님) + (선택)품질(사용성) 렌즈.

    produced/source_read/source_ocr 은 *텍스트*다(읽기·readback은 호출자 책임 = 독립성).
    source_ocr 가 비면 OCR 교차검증·바닥 veto는 생략(판정은 그대로 동작).
    반환: {achieved, grounded, veto, judge_ok, major_issues[], downgraded[], unverifiable[],
           (quality 시) usability, suggestions[]}.
    독립 원본(source_read)이 비면 achieved=None + abstain (ground-truth 없이 verdict 금지).
    """
    if len((source_read or "").strip()) < 20:   # 독립 ground-truth 없음 → 판정 불가(거짓 verdict 금지)
        return {"achieved": None, "grounded": 0.0, "veto": False, "judge_ok": False,
                "abstain": "독립 원본 읽기가 비어/부족 — ground-truth 없이 판정 불가",
                "major_issues": [], "downgraded": [], "unverifiable": []}
    ocr_tk = _toks(source_ocr)
    grounded = (len(_toks(produced) & ocr_tk) / max(1, len(_toks(produced)))) if ocr_tk else 0.0
    # ★정확성 + 품질을 *한 호출에 동시*(병렬) 실행 — 독립 작업이라 왕복 1회 시간. verdict는 분리 유지.
    import concurrent.futures
    _correct_prompt = (
        f"원본(독립적으로 정확히 읽음):\n{source_read[:1200]}\n\n산출물(검증 대상):\n{produced[:1200]}\n\n"
        "산출물이 원본을 *충실히* 반영하나? 원칙:\n"
        "- 요약/추출이면 압축·일부 생략 허용. *의미·핵심*이 보존되면 OK.\n"
        "- 글자단위 전사 차이(무의미·조밀 문자열)는 결함이 *아니다* — *뜻*이 바뀐 것만 결함.\n"
        "- 참조 원본도 불완전할 수 있다. 신뢰성있게 대조 *못 하는* 부분은 결함이 아니라 unverifiable 로 분류.\n"
        'ONLY JSON: {"issues":[{"type":"omission|distortion|hallucination",'
        '"evidence":"<산출물의 문제 구체 문구>","severity":"major|minor"}],'
        '"unverifiable":["<신뢰성있게 대조 못한 부분>"]}')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as _ex:
        _fc = _ex.submit(_cli_json, _correct_prompt, model, None, provider)        # 정확성 렌즈
        _fq = (_ex.submit(assess_quality, produced, source=source_read,            # 품질 렌즈(동시)
                          model=model, provider=provider) if quality else None)
        jv = _fc.result()
        _q = _fq.result() if _fq else None
    judge_ok = isinstance(jv, dict) and ("issues" in jv)
    issues = (jv or {}).get("issues") or []
    unverifiable = (jv or {}).get("unverifiable") or []

    def _in_ocr(ev: str) -> bool:
        t = _toks(ev)
        return bool(t) and (len(t & ocr_tk) / max(1, len(t))) >= 0.4   # 그 내용이 원본 OCR에 있나

    # ★비대칭(거짓PASS≈0 원칙): *환각* 주장만 OCR로 강등(원본에 실재하면 환각 아님 — 안전).
    #   *누락*은 OCR-부재로 강등하지 않는다 — OCR은 불완전(링크카드/이미지를 Claude는 읽어도 OCR은 놓침)이라
    #   "OCR이 못 봤으니 누락 깎자"는 *진짜 누락을 통과(거짓PASS)*. 누락은 판정자 severity로만 보수 처리.
    downgraded, surviving = [], []
    for it in issues:
        if ocr_tk and it.get("type") == "hallucination" and _in_ocr(it.get("evidence", "")):
            downgraded.append({**it, "why": "OCR이 원본실재 확인→환각아님"}); continue
        surviving.append(it)

    major = [it for it in surviving if it.get("severity") == "major"]
    vetoed = bool(ocr_tk) and grounded < floor                   # 결정론 환각 바닥
    achieved = bool(judge_ok and not major and not vetoed)       # 판정자 불량이면 안전하게 실패(거짓PASS 금지)
    out = {"achieved": achieved, "grounded": round(grounded, 2), "veto": vetoed, "judge_ok": judge_ok,
           "major_issues": [{"type": it.get("type"), "evidence": (it.get("evidence") or "")[:60]} for it in major],
           "downgraded": [{"type": it.get("type"), "evidence": (it.get("evidence") or "")[:40],
                           "why": it.get("why")} for it in downgraded],
           "unverifiable": [str(u)[:60] for u in unverifiable]}
    if _q is not None:   # 품질(사용성) = 정확성과 *별개*(자문) — 위에서 *병렬로* 이미 실행됨. achieved 안 막음.
        out["usability"], out["suggestions"], out["quality_flags"] = _q["usability"], _q["suggestions"], _q["flags"]
    return out


def _quality_signals(produced: str, source: str = "") -> dict:
    """결정론 품질 신호(객관·재현) — LLM 품질 의견의 앵커/교차검증용."""
    p = produced or ""
    lines = [l for l in p.splitlines() if l.strip()]
    sig = {"chars": len(p), "lines": len(lines),
           "longest_line": max((len(l) for l in lines), default=0),
           "run_on": len(lines) <= 2 and len(p) > 200}      # 한 줄 덩어리 → 스캔 불가
    if source:
        sig["compression_ratio"] = round(len(p) / max(1, len(source)), 2)  # ~1.0 = 압축 0
    return sig


def assess_quality(produced: str, *, source: str = "", model: str = VERIFY_MODEL,
                   provider: str | None = None) -> dict:
    """산출물의 *사용성/품질* 평가 — 정확성과 별개(자문). LLM 의견 + 결정론 신호로 그라운딩·교차검증.
    품질은 주관적이라 완전 결정론 바닥은 없지만, 측정가능 축(압축비·구조)으로 LLM을 닻 내리고
    *강한 객관 신호가 LLM '좋음'과 모순되면 flag*(품질 신뢰바닥, 부분적).
    반환: {usability, suggestions[], signals{}, flags[]}."""
    sig = _quality_signals(produced, source)
    sig_txt = ", ".join(f"{k}={v}" for k, v in sig.items())
    qv = _cli_json(
        f"산출물:\n{produced[:1200]}\n\n결정론 측정(객관): {sig_txt}\n\n"
        "이 산출물의 *사용성(품질)*만 평가하라(정확성 말고 — 사람이 실제로 쓰기 좋은가). "
        "측정치를 참고하되 **문제가 없으면 지어내지 마라**(좋으면 usability=good, suggestions=[]). "
        "고려: 압축효과(요약인데 원문과 길이 비슷=나쁨)·날짜/항목 그룹핑·스캔 용이성·노이즈(광고/인사/무의미).\n"
        'ONLY JSON: {"usability":"good|fair|poor","suggestions":["<개선점>"]}',
        model, provider=provider)
    usability = (qv or {}).get("usability", "?")
    suggestions = [str(s)[:80] for s in ((qv or {}).get("suggestions") or [])]
    flags = []   # 결정론 교차검증: 강한 객관 신호 ↔ LLM '좋음' 모순 시(품질 신뢰바닥)
    cr = sig.get("compression_ratio")
    if cr is not None and cr > 0.9 and usability == "good":
        flags.append(f"압축비 {cr}>0.9(압축 0)인데 usability=good — 모순")
    if sig.get("run_on") and usability == "good":
        flags.append("한 줄 덩어리(run_on)인데 usability=good — 스캔성 모순")
    return {"usability": usability, "suggestions": suggestions, "signals": sig, "flags": flags}


def run_verify(goal: str, shots_dir: str = "sapphi_out", shot: str | None = None,
               model: str = VERIFY_MODEL) -> dict:
    target = shot or _latest_shot(shots_dir)
    if not target or not os.path.exists(target):
        raise RuntimeError(f"검증할 스크린샷이 없습니다 (shots_dir={shots_dir}). "
                           f"먼저 sapphi 를 실행해 step_*.png 를 만드세요.")
    print(f"[RUBI verify] 목표: {goal}")
    print(f"[RUBI verify] 최종 화면: {target}")
    data = verify_outcome(goal, target, model)
    achieved = bool(data.get("achieved"))
    icon = "✅ 실제 달성" if achieved else "❌ 미달성 (거짓 done 가능성)"
    print(f"[RUBI verify] 판정: {icon}")
    print(f"[RUBI verify] 근거: {data.get('rationale','')}")
    if not achieved:
        if data.get("diagnosis"):
            print(f"[RUBI verify] 진단(사람): {data.get('diagnosis')}")
        if data.get("directive"):
            print(f"[RUBI verify] 지시(워커): {data.get('directive')}")
    return {"achieved": achieved, **data, "shot": target}
