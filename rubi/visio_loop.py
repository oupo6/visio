"""닫힌 루프 — 빌드 → VISIO 리뷰 → 에이전트(fixer)가 directive로 SUT 고침 → 재확인.

독립성: fixer = build_model ≠ judge = judge_model. **fixer는 판정자 내부·오라클·contract를 못 본다** —
오직 worker-facing diagnosis+directive 만 본다(= teach-to-the-test 방지: 검사를 흉내내 통과 못 함).
정지: 모든 케이스 accepted(오라클 있으면 oracle-confirmed). 가드: 반복 캡 + 무변경(정체/게이밍) 탐지.

핵심 위험 = '루프가 못 믿을 판정자를 증폭'(Goodhart). 그래서 ① 신뢰 바닥(probes 오라클)이 먼저 깔려야 하고
(완료), ② fixer는 오라클을 못 봐서 *실제 동작*을 고칠 수밖에 없고, ③ 무변경/캡으로 폭주를 막는다.
"""
from __future__ import annotations

import copy
import os
import re
import secrets

from . import provider, visio

_FIX_SYS = (
    "너는 이 맥 자동화 기능(SUT)의 *개발자*다. 독립 테스터(VISIO)가 아래 문제를 보고했다. 코드를 고쳐라.\n"
    "★너는 테스터의 내부 판정기준·검증도구(오라클)를 *볼 수 없다* — 오직 진단(diagnosis)과 지시(directive)만 "
    "보고 *근본 원인*을 고쳐라. 검사만 통과하려는 꼼수(기대 문자열 하드코딩 등) 금지 — *실제 동작*을 바르게 만들어라.\n"
    "★I/O 계약 유지: 알림 payload는 환경변수 VISIO_INJECTED(JSON) 또는 stdin 으로 들어온다. 진입점·인자를 "
    "바꾸지 마라(테스터가 같은 방식으로 다시 실행한다).\n"
    "수정한 *전체 파일*을 출력하라. ONLY JSON: {\"code\": \"<완성된 파이썬 파일 전체>\"} "
    "(개행은 \\n, 따옴표는 \\\" 로 이스케이프)."
)


def _read(p: str) -> str:
    with open(p, encoding="utf-8") as f:
        return f.read()


def _write(p: str, s: str) -> None:
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def _accepted(r) -> bool:
    """루프 수용 기준: 통과(+오라클 있으면 veto 거쳐 status=pass 면 oracle-confirmed 보장)."""
    return r.status == "pass"


def _fix_sut(feature: str, old_code: str, issues: list, model: str, attempts: int = 3) -> str:
    issue_block = "\n".join(
        f"- [{i['id']}] 기대: {i['expected']}\n  진단: {i['diagnosis']}\n  지시: {i['directive']}"
        for i in issues)
    prompt = (f"{_FIX_SYS}\n\n## 기능\n{feature}\n\n## 현재 코드\n```python\n{old_code}\n```\n\n"
              f"## 독립 테스터(VISIO)가 보고한 문제\n{issue_block}\n")
    for _ in range(attempts):                                  # 빈응답(클라우드 플레이키) 재시도
        try:
            data = provider.complete_json(prompt, model)
        except Exception:
            continue
        code = (data or {}).get("code") or ""
        m = re.search(r"```(?:python)?\s*(.*?)```", code, re.S)   # 코드펜스 잔여 제거
        if m:
            code = m.group(1)
        code = code.strip()
        if code:
            return code
    return ""   # 빈응답(재시도 소진)


def _vary_params(params: dict | None, nonce: str) -> dict:
    """자극의 문자열 콘텐츠 필드에 nonce를 섞어 *매 iter 유일화* — 하드코딩 게이밍 차단.
    fixer가 기대 텍스트를 박아 통과하려 해도, 다음 iter엔 다른 내용이라 오라클(from_injected)이 잡는다."""
    p = copy.deepcopy(params or {})

    def tag(s):
        return f"{s} [{nonce}]" if isinstance(s, str) and s.strip() else s

    for k in ("body", "text", "content", "title"):
        if k in p:
            p[k] = tag(p[k])
    if isinstance(p.get("items"), list):
        for it in p["items"]:
            if isinstance(it, dict):
                for k in ("body", "text", "title"):
                    if k in it:
                        it[k] = tag(it[k])
    return p


def run_closed_loop(plan, sut_path: str, *, fixer_model: str | None = None, max_iters: int = 3,
                    out_dir: str = "visio_out/loop", routines_dir: str = "rubi_routines",
                    local_judge: str = "off", pre_iter=None, randomize_stimulus: bool = False,
                    verbose: bool = True) -> dict:
    """plan 을 sut_path(편집 가능한 SUT 파일)에 대해 수렴할 때까지 빌드→리뷰→고침→재확인.
    randomize_stimulus: 매 iter 자극 콘텐츠에 nonce 주입 → fixer가 하드코딩으로 못 속임(anti-gaming)."""
    fixer_model = fixer_model or plan.build_model
    if fixer_model == plan.judge_model and verbose:
        print(f"  ⚠️ 독립성 경고: fixer == judge ({fixer_model}) — 다른 모델 권장")
    plan.sut_entry = f".venv/bin/python {sut_path}"
    orig_stim = {c.id: copy.deepcopy(c.stimulus) for c in plan.cases}   # 랜덤화 기준(매번 원본서 변형)
    run_token = secrets.token_hex(2)
    history: list = []
    rep = None
    for it in range(1, max_iters + 1):
        if randomize_stimulus:
            nonce = f"{run_token}-{it}"
            for c in plan.cases:
                base = orig_stim.get(c.id) or {}
                if base.get("kind"):
                    c.stimulus = {**base, "params": _vary_params(base.get("params"), nonce)}
            if verbose:
                print(f"  🎲 자극 랜덤화(nonce={nonce}) — 하드코딩 게이밍 차단")
        if pre_iter:
            pre_iter()
        if verbose:
            print(f"\n{'='*64}\n🔁 LOOP iter {it}/{max_iters} — SUT: {sut_path}\n{'='*64}")
        rep = visio.run_test_plan(plan, mode="rehearse", out_dir=f"{out_dir}/iter{it}",
                                  routines_dir=routines_dir, local_judge=local_judge, verbose=verbose)
        accepted = len(rep.results) > 0 and all(_accepted(r) for r in rep.results)
        cur_fail = sorted(r.case.id for r in rep.results if not _accepted(r))
        prev_fail = history[-1].get("failing") if history else None
        no_progress = bool(cur_fail) and cur_fail == prev_fail   # 같은 케이스 또 fail = 진전 없음
        history.append({"iter": it, "pass": sum(1 for r in rep.results if r.status == "pass"),
                        "total": len(rep.results), "accepted": accepted, "failing": cur_fail,
                        "no_progress": no_progress,
                        "trust": [getattr(r, "trust", "") for r in rep.results]})
        if no_progress and verbose:
            print(f"  ⚠️ 진전 없음 — 직전과 같은 케이스 {cur_fail} 가 또 실패(코드는 바뀜)")
        if accepted:
            if verbose:
                print(f"\n✅ LOOP 수렴 — iter {it}에서 전 케이스 통과(오라클 확인)")
            return {"ok": True, "iters": it, "report": rep, "history": history, "sut_path": sut_path}

        issues = [{"id": r.case.id, "expected": r.case.expected,
                   "diagnosis": r.verdict.get("diagnosis") or r.note,
                   "directive": r.verdict.get("directive") or ""}
                  for r in rep.results if not _accepted(r)]
        if verbose:
            print(f"\n🔧 fixer({fixer_model})가 {len(issues)}개 문제로 SUT 수정 "
                  f"(판정자 내부·오라클 안 봄, diagnosis/directive만)")
        old_code = _read(sut_path)
        try:
            new_code = _fix_sut(plan.feature, old_code, issues, fixer_model)
        except Exception as e:
            return {"ok": False, "reason": f"fixer 오류: {type(e).__name__}: {str(e)[:120]}",
                    "iters": it, "report": rep, "history": history, "sut_path": sut_path}
        if not new_code:
            if verbose:
                print("  ⛔ fixer 빈응답(재시도 소진) → 중단")
            return {"ok": False, "reason": "fixer 빈응답", "iters": it, "report": rep,
                    "history": history, "sut_path": sut_path}
        if new_code == old_code:
            if verbose:
                print("  ⛔ fixer가 코드를 안 바꿈 → 정체(게이밍/막힘) 중단")
            return {"ok": False, "reason": "무변경(정체)", "iters": it, "report": rep,
                    "history": history, "sut_path": sut_path}
        _write(sut_path, new_code)
        if verbose:
            print(f"  ✏️ SUT 수정됨({len(new_code)}자) → 재확인")
    return {"ok": False, "reason": f"반복 캡({max_iters}) 도달·미수렴", "iters": max_iters,
            "report": rep, "history": history, "sut_path": sut_path}
