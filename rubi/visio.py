"""VISIO — 에이전트가 만든 맥 기능을 *실제 환경*에서 독립 검증하는 테스트 하네스.

역할: RUBI=테스트 두뇌(계획·판정), SAPPHI=손(관찰·실행, 안전 게이트), EMERI=기억(회귀).

`run_autoloop` 과의 차이(핵심):
  - autoloop = *하나의 목표 달성* 루프(실패하면 피드백 재주입 후 재시도).
  - visio   = *하나의 기능을 여러 테스트케이스로 검증*. 케이스마다 관찰→실행→판정 *단발*(판정 directive를
              SUT에 재주입하지 않음 — 그래야 독립적). 결과를 회귀 키로 저장해 다음 런과 비교.

전부 기존 부품 재사용(taskspec/verify_runner/oracles/records/emeri/perception_verify + sapphi.agent/perceive/
state_snapshot). 안전 게이트는 sapphi.agent 의 mode=rehearse + Action.commit 을 그대로 쓴다(신규 게이트 0).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime

DEFAULT_JUDGE = "claude-opus-4-8"      # 판정자 — *만든 모델과 달라야* 함(독립성)
DEFAULT_BUILD = "claude-sonnet-4-6"    # SUT(피검 기능)를 구동하는 손의 모델

# 강제 rehearse(=--mode live 여도 자동 커밋 불가) 대상 = *비가역/금전*만.
# 가역(file_artifact_create_or_edit=노트 작성 등)은 제외 — 안 그러면 가역 작업도 커밋 직전 멈춰버린다.
_IRREVERSIBLE = {"purchase_or_payment", "account_or_security_change", "message_send", "data_mutation"}

# 민감(개인) 데이터가 화면에 나오는 케이스 → 판정 스샷을 클라우드로 보내지 말고 *로컬 Gemma4* 로 판정.
_SENSITIVE = {
    "카카오", "카톡", "kakao", "클립보드", "clipboard", "연락처", "contacts", "주소록", "캘린더",
    "calendar", "일정", "메시지", "message", "문자", "메일", "mail", "이메일", "email", "비밀번호",
    "password", "은행", "bank", "증권", "toss", "토스", "주식", "stock", "계좌", "account", "카드", "card",
    "건강", "health", "사진", "photo",
}


def _is_sensitive(case: "TestCase") -> bool:
    spec = case.spec or {}
    blob = " ".join([case.goal or "", case.title or "", spec.get("app", "") or "",
                     spec.get("task_type", "") or ""]).lower()
    return any(k in blob for k in _SENSITIVE)


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 형태
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TestCase:
    id: str                              # 안정 슬러그 = 회귀 키
    title: str
    goal: str                            # SUT(손)에게 줄 자연어 목표
    rationale: str = ""                  # 왜 이 케이스(엣지케이스 가설)
    spec: dict = field(default_factory=dict)   # taskspec.classify_goal(goal) asdict
    expected: str = ""                   # 관찰 가능한 성공 기준
    preconditions: list = field(default_factory=list)
    must_confirm: bool = False           # 비가역/금전 → live 여도 강제 rehearse
    origin: str = "llm"                  # llm | heuristic | fixed
    stimulus: dict = field(default_factory=dict)   # 주입할 자극 {kind, params} (신호발생기 입력)
    fixture: str = "none"                # native:<kind> | agent:<path> | none
    oracle: dict = field(default_factory=dict)   # 결정론 출력-검증 {kind, params} (probes — 거짓 pass 거부권)


@dataclass
class TestPlan:
    feature: str
    feature_key: str
    cases: list                          # list[TestCase]
    judge_model: str = DEFAULT_JUDGE
    build_model: str = DEFAULT_BUILD
    created_at: str = ""
    fixture_requests: list = field(default_factory=list)  # VISIO가 못 만드는 자극 → 에이전트 픽스처 요청서
    sut_entry: str = ""                  # 빌드된 기능 트리거법(스크립트/명령) — 없으면 워커 폴백

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class CaseResult:
    case: TestCase
    status: str                          # pass | fail | blocked_by_gate | error
    achieved: bool = False
    confidence: float = 0.0
    gate: str = "completed"              # completed | stopped_at_commit | blocked | error
    verdict: dict = field(default_factory=dict)
    perception_check: dict = field(default_factory=dict)
    screenshot_path: str = ""
    judge: str = "cloud"                 # 'cloud:<model>' | 'local:<model>' — 누가 판정했나(감사·프라이버시)
    note: str = ""
    oracle: dict = field(default_factory=dict)   # probes 결과 {ok, achieved, evidence}
    trust: str = "vlm-only"              # oracle-confirmed | oracle-vetoed | vlm-fail·oracle-ok | vlm-only


@dataclass
class VisioReport:
    plan: TestPlan
    results: list                        # list[CaseResult]
    passed: int = 0
    failed: int = 0
    gated: int = 0
    errored: int = 0
    blocked: int = 0                                  # 픽스처 미작성으로 보류된 케이스(에이전트 작성 필요)
    regression: dict = field(default_factory=dict)
    judge_calls: dict = field(default_factory=dict)   # {local, cloud, escalated} — 토큰은 cloud만
    report_path: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _slug(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "_", (s or "").strip().lower())
    return re.sub(r"_+", "_", s).strip("_")[:48] or "feature"


def _tokens(s: str) -> set:
    return {t for t in re.findall(r"[0-9A-Za-z가-힣]+", (s or "").lower()) if len(t) >= 2}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ─────────────────────────────────────────────────────────────────────────────
# 1) 테스트 계획 생성 (RUBI 두뇌)
# ─────────────────────────────────────────────────────────────────────────────
_PLAN_SYS = (
    "너는 *맥 기능 인수테스트* 설계자(독립 QA)다. 에이전트가 만든 기능을 *실제 맥*에서 검증할 테스트케이스를 "
    "*독립적으로* 설계한다. 두 렌즈로 분기하라: "
    "①**예외/오류 사냥**(경계·빈/거대·깨진 입력·예상못한 상태[앱닫힘/이미열림/권한없음]·규모[다발/0개]·방해금지) "
    "②**사용자 의도 기반**(사용자가 *실제로 원할* 사용 패턴 — 예: 종류별 분류·중복 처리·요약 품질). "
    "각 케이스는: 관찰가능 성공기준(expected) + 전제조건(preconditions) + **자극(stimulus)** 을 가진다. "
    "stimulus = 이 케이스를 *유발하려 주입할 입력/이벤트* = {kind, params}. "
    "테스터가 *직접 만들 수 있는* kind: 'notification'(알림; params {title,body} 또는 버스트 {items:[...],interval} 또는 {count}), "
    "'file'(파일 생성; {dir,name,content}), 'clipboard'({text}). "
    "직접 *못 만드는* 자극(특정 앱 내부 이벤트·하드웨어 등)은 kind를 그 이름(예 'app_internal')으로 쓰고 params에 설명(→에이전트가 픽스처 작성). "
    "자극 없이 *기존 상태 관찰만* 하는 케이스는 stimulus를 {} 로. "
    "돈/전송/삭제 비가역은 '커밋 직전까지'만 검증한다고 가정. "
    "ONLY JSON: {\"cases\":[{\"id\":<영문슬러그>,\"title\":<한글>,\"goal\":<손에게 줄 한국어 목표>,"
    "\"rationale\":<왜 이 케이스>,\"expected\":<관찰가능 성공기준>,\"preconditions\":[<문자열>],"
    "\"stimulus\":{\"kind\":<문자열|\"\">,\"params\":{}}}]}"
)


def generate_test_plan(feature: str, judge_model: str = DEFAULT_JUDGE,
                       build_model: str = DEFAULT_BUILD, n: int = 6,
                       offline: bool = False, routines_dir: str = "rubi_routines",
                       artifact: str = "") -> TestPlan:
    """artifact = (선택) 기능 구현 코드/경로 — 주면 *코드-이해*로 위험표면을 뽑아 설계에 반영(white-box 설계)."""
    from . import taskspec, emeri

    feature_key = _slug(feature)
    spec0 = taskspec.classify_goal(feature, judge_model if not offline else None)
    cases_raw: list = []
    if not offline:
        try:
            weak, verified = emeri.recall_for_design(routines_dir, feature, task_key=spec0.key())
        except Exception:
            weak, verified = "", ""
        risk = _risk_surface(feature, artifact, judge_model) if artifact else ""
        cases_raw = _llm_cases(feature, weak, verified, risk, judge_model, n)
    if not cases_raw:
        cases_raw = _fallback_cases(feature, spec0)

    from sapphi import triggers

    cases: list = []
    fixture_requests: list = []
    seen_ids = set()
    for i, c in enumerate(cases_raw[:n]):
        goal = (c.get("goal") or feature).strip()
        cspec = taskspec.classify_goal(goal, judge_model if not offline else None)
        cid = _slug(c.get("id") or c.get("title") or f"{feature_key}_{i}")
        while cid in seen_ids:
            cid += f"_{i}"
        seen_ids.add(cid)
        must = bool(cspec.requires_confirmation or cspec.risk in {"high", "critical"}
                    or cspec.task_type in _IRREVERSIBLE)
        # 테스트 가능성 체크: 이 케이스 자극을 VISIO가 직접 만들 수 있나(triggers) → native, 아니면 픽스처 요청
        stim = c.get("stimulus") or {}
        kind = str(stim.get("kind") or "").strip()
        if not kind:
            fixture = "none"
        elif triggers.can_produce(kind):
            fixture = f"native:{kind}"
        else:
            fixture = "agent:pending"
            fixture_requests.append({"case_id": cid, "kind": kind, "params": stim.get("params") or {},
                                     "why": f"VISIO가 직접 못 만드는 자극('{kind}') — 에이전트가 픽스처(주입기) 작성 필요"})
        cases.append(TestCase(
            id=cid, title=c.get("title") or goal[:40], goal=goal,
            rationale=c.get("rationale", ""), spec=asdict(cspec),
            expected=c.get("expected", ""), preconditions=list(c.get("preconditions") or []),
            must_confirm=must, origin=c.get("origin", "llm" if not offline else "fixed"),
            stimulus=stim, fixture=fixture))
    return TestPlan(feature, feature_key, cases, judge_model, build_model, _now(),
                    fixture_requests=fixture_requests, sut_entry="")


def _risk_surface(feature: str, artifact: str, model: str) -> str:
    """설계 UNDERSTAND 단계 — 기능 *구현(코드/산출물)*을 읽어 *이 기능 고유의 위험표면*을 뽑는다.
    과거경험(EMERI)과 *독립적인* 비-자명 위험원 = 현재 구조. (측정: 코드-특정 버그를 설명만보다 잘 잡음.)
    ★설계는 white-box(코드 봐도 됨)지만 *판정(verdict)은 여전히 오라클이 실제 상태로* 함 → 독립성 유지."""
    from .provider import _cli_json
    code = artifact or ""
    try:
        if code and os.path.exists(os.path.expanduser(code)):
            code = open(os.path.expanduser(code), encoding="utf-8").read()
    except OSError:
        pass
    if not code.strip():
        return ""
    data = _cli_json(
        f"기능: {feature}\n\n구현:\n```\n{code[:2000]}\n```\n\n"
        "이 구현의 *고유 위험표면* — 어떤 입력/상태에서 깨질지 *코드 근거로* 짚어라"
        "(특정 토큰·분기·미처리 입력 등 코드에서만 보이는 것). "
        'ONLY JSON: {"risks":["<구체 위험: 어떤 입력에서 어떻게 깨지나>"]}', model)
    risks = (data.get("risks") if isinstance(data, dict) else None) or []
    return "\n".join(f"- {str(r)}" for r in risks if str(r).strip())


def _llm_cases(feature: str, weak: str, verified: str, risk: str, model: str, n: int) -> list:
    """기능설명(+코드위험+과거학습)으로 엣지 케이스 설계.
    risk=코드도출 위험(반드시 타깃) / weak=과거 약점(반드시 포함) / verified=검증됨(중복 최소)."""
    from .provider import _cli_json
    blocks = ""
    if risk:                       # 코드에서 도출한 위험 — 직접 건드리는 케이스
        blocks += f"\n## ★코드에서 도출한 위험표면 — 이걸 직접 건드리는 케이스를 포함하라\n{risk}\n"
    if weak:                       # 약점은 강조 — 회귀 확인 위해 반드시 타깃
        blocks += f"\n## ★반드시 포함할 케이스 — 과거에 깨진 약점(회귀 확인)\n{weak}\n"
    if verified:
        blocks += f"\n## 이미 검증된 동작(중복 최소화)\n{verified}\n"
    prompt = (f"{_PLAN_SYS}\n\n## 검증할 기능\n{feature}\n{blocks}\n"
              f"최대 {n}개. 가장 잘 *깨질* 케이스 위주로 — 단, 위 '★' 항목이 있으면 *반드시* 포함하라.")
    try:
        data = _cli_json(prompt, model)
        cases = data.get("cases") if isinstance(data, dict) else None
        return cases or []
    except Exception:
        return []


def _fallback_cases(feature: str, spec0) -> list:
    """LLM 없이도 도는 최소 케이스 — 해피패스 1개(+가능하면 빈/엣지 1개)."""
    return [{
        "id": "happy_path", "title": "정상 동작", "goal": feature,
        "rationale": "기본 happy path — 기능이 의도대로 동작하는가",
        "expected": "요청한 결과가 대상 앱/화면에 실제로 보인다",
        "preconditions": [], "origin": "fixed",
    }]


def _expected_objects(case: TestCase) -> list:
    """판정 교차검증에 쓸 '성공 증거 객체'. expected + spec.variables 값에서 추출."""
    objs: list = []
    for raw in re.split(r"[,/·]| 및 | and ", case.expected or ""):
        t = raw.strip()
        if len(t) >= 2:
            objs.append(t)
    for v in (case.spec.get("variables") or {}).values():
        if isinstance(v, str) and 2 <= len(v) <= 40:
            objs.append(v)
    # 중복 제거, 최대 6
    out, seen = [], set()
    for o in objs:
        if o not in seen:
            seen.add(o)
            out.append(o)
    return out[:6]


# ─────────────────────────────────────────────────────────────────────────────
# 저장/로드 (재현·회귀)
# ─────────────────────────────────────────────────────────────────────────────
def save_plan(plan: TestPlan, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"visio_plan_{plan.feature_key}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def load_plan(path: str, judge_model: str | None = None, build_model: str | None = None) -> TestPlan:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    cases = [TestCase(**c) for c in d.get("cases", [])]
    return TestPlan(d["feature"], d["feature_key"], cases,
                    judge_model or d.get("judge_model", DEFAULT_JUDGE),
                    build_model or d.get("build_model", DEFAULT_BUILD),
                    d.get("created_at", ""),
                    fixture_requests=d.get("fixture_requests", []),
                    sut_entry=d.get("sut_entry", ""))


# ─────────────────────────────────────────────────────────────────────────────
# 2) 실행 (관찰 → 실행 → 판정 → 기록)
# ─────────────────────────────────────────────────────────────────────────────
def _deny_all(_action) -> bool:
    return False


_APP_CACHE: dict = {}
_INFER_SKIP = {
    "앱을", "앱", "어플", "열어", "열다", "연다", "켜다", "켜기", "실행", "실행해", "저장", "요약",
    "메시지", "대화", "내용", "오늘", "매일", "자정", "기능", "에서", "으로", "에게", "한다", "하기",
    "보내", "보내기", "확인", "기록", "추가", "만들", "생성", "open", "app", "the", "and", "from", "into",
}


def _infer_app(goal: str) -> str | None:
    """classify가 app을 못 잡았을 때, goal에서 *설치된 앱*을 추론(perceive._find_app_bundle).
    판정·실행이 엉뚱한 창을 보지 않게 isolate 대상을 정한다."""
    from sapphi import perceive
    toks = [t for t in re.findall(r"[A-Za-z가-힣]{2,}", goal or "") if t not in _INFER_SKIP]
    for t in toks[:5]:
        if t in _APP_CACHE:
            if _APP_CACHE[t]:
                return _APP_CACHE[t]
            continue
        name = None
        try:
            r = perceive._find_app_bundle(t)
            if r and r[0]:
                name = os.path.splitext(os.path.basename(r[0]))[0]
        except Exception:
            name = None
        _APP_CACHE[t] = name
        if name:
            return name
    return None


def run_test_plan(plan: TestPlan, mode: str = "rehearse", out_dir: str = "visio_out",
                  routines_dir: str = "rubi_routines", confirm_fn=None,
                  local_vlm: bool = False, local_judge: str = "auto",
                  verbose: bool = True) -> VisioReport:
    """local_judge: off=항상 클라우드 / auto=민감 케이스만 로컬 Gemma4(없으면 클라우드 폴백) / on=항상 로컬."""
    from . import taskspec, verify_runner, records, provider
    from sapphi import agent, triggers

    _local_ok = None  # (ok, why) 캐시 — 매 케이스 available() 재호출 방지

    def _judge_route(case) -> tuple:
        """판정 시작점 결정 → (model, provider, label, escalate_ok).
        off=전부 클라우드 / on=전부 로컬(escalate X) / auto(티어드)=로컬 우선,
        *비민감*이 불확실하면 클라우드 escalate(escalate_ok=True), *민감*은 로컬 고정(프라이버시)."""
        nonlocal _local_ok
        sensitive = _is_sensitive(case)
        if local_judge == "off":
            return plan.judge_model, None, f"cloud:{plan.judge_model}", False
        if _local_ok is None:
            _local_ok = provider.available("local")
        ok, why = _local_ok
        if ok:
            lm = os.environ.get("SAPPHI_LOCAL_MODEL", "gemma4")
            escalate_ok = (local_judge == "auto") and not sensitive   # 민감은 escalate 금지
            return lm, "local", f"local:{lm}", escalate_ok
        # 로컬 불가 → 클라우드 폴백
        if verbose:
            tag = "민감 케이스" if sensitive else "로컬 우선"
            print(f"    ⚠️ {tag} 로컬 판정 불가({why}) → 클라우드 폴백")
        return plan.judge_model, None, f"cloud:{plan.judge_model}(로컬폴백)", False

    if plan.judge_model == plan.build_model:
        print(f"  ⚠️ 독립성 경고: judge_model == build_model ({plan.judge_model}). "
              f"만든 놈이 자기 채점 — --judge-model 로 다른 모델 권장.")
    confirm_fn = confirm_fn or _deny_all
    os.makedirs(out_dir, exist_ok=True)
    results: list = []
    judge_counts = {"local": 0, "cloud": 0, "escalated": 0}   # 토큰 계측(클라우드 콜만 토큰 먹음)

    for case in plan.cases:
        cspec = taskspec.TaskSpec(**case.spec) if case.spec else taskspec.classify_goal(case.goal)
        isolate = cspec.app or _infer_app(case.goal)
        eff_mode = "rehearse" if case.must_confirm else mode
        if verbose:
            tag = "🔒강제rehearse" if (case.must_confirm and mode != "rehearse") else eff_mode
            print(f"\n▶ [{case.id}] {case.title}  ({tag}, app={isolate or '-'})")

        # (A) 관찰 — 실제 상태(AX 우선), 읽기전용
        obs = _observe_state(isolate, out_dir, case)
        if not obs["ok"]:
            results.append(CaseResult(case, "error", gate="error",
                                      note=f"전제조건 불충족(환경문제): {obs['missing']}"))
            if verbose:
                print(f"    ⚠️ 전제조건 불충족 → error: {obs['missing']}")
            continue

        # (SETUP) 자극 주입 — VISIO가 *직접*(triggers) 또는 *에이전트 픽스처*로. 워커 즉흥 ❌.
        injected: dict = {}
        fx = case.fixture or "none"
        params = (case.stimulus or {}).get("params") or {}
        if fx.startswith("native:"):
            pr = triggers.produce(fx.split(":", 1)[1], params)
            if not pr.get("ok"):
                results.append(CaseResult(case, "error", gate="error", note=f"자극 주입 실패: {pr.get('detail')}"))
                if verbose:
                    print(f"    ⚠️ 자극 주입 실패 → error: {pr.get('detail')}")
                continue
            injected = pr.get("injected") or {}
            if verbose:
                print(f"    ⚡ 자극 주입({fx}): {pr.get('detail')}")
        elif fx.startswith("agent:"):
            path = fx.split(":", 1)[1]
            if path in ("", "pending") or not os.path.exists(path):
                results.append(CaseResult(case, "blocked_pending_fixture", gate="blocked",
                                          note=f"자극('{(case.stimulus or {}).get('kind')}')은 VISIO가 못 만듦 — 에이전트 픽스처 필요(`visio fixtures`)"))
                if verbose:
                    print(f"    ⏸ blocked: 픽스처 미작성 — `visio fixtures`로 요청서 받아 작성 필요")
                continue
            injected = _run_agent_fixture(path, params)

        # (B) SUT 실행 — sut_entry 있으면 *빌드된 기능* 실행, 없으면 워커 폴백(전환기).
        sut_trace = ""
        if plan.sut_entry:
            sut = _run_sut(plan.sut_entry, injected)
            result, gate, sut_trace = None, "completed", sut.get("output", "")
            if verbose:
                print(f"    ▷ SUT 실행: {plan.sut_entry}")
        else:
            goal = case.goal + "\n\n[작업 분류/검증 스펙]\n" + taskspec.spec_text(
                cspec, taskspec.find_template(routines_dir, cspec))
            try:
                result = agent.run(goal, mode=eff_mode, model=plan.build_model, out_dir=out_dir,
                                   isolate_app=isolate, confirm_fn=confirm_fn, one_step=True,
                                   verbose=False, max_steps=8)
            except Exception as e:
                results.append(CaseResult(case, "error", gate="error",
                                          note=f"실행 예외: {type(e).__name__}: {str(e)[:120]}"))
                if verbose:
                    print(f"    ❌ 실행 예외: {type(e).__name__}")
                continue
            gate = "stopped_at_commit" if result.stopped_at_commit else "completed"

        # (C) 판정 — 독립(자기보고 무시): 실제 최종화면 + 오라클 + perception 교차검증
        verify_app = (getattr(result, "final_isolate_app", None) if result else None) or isolate
        shot, _scope = _scope_shot(out_dir, verify_app)
        shot = shot or verify_runner._latest_shot(out_dir) or ""
        trace = (sut_trace + "\n" if sut_trace else "") + _trace_text(result)
        if injected:   # 판정자가 *내가 쏜 자극*(ground truth)과 출력을 대조하도록 주입
            trace = (f"## 주입한 테스트 자극(ground truth — 출력이 이걸 반영해야 함)\n"
                     f"{json.dumps(injected, ensure_ascii=False)[:1000]}\n\n") + trace

        # ★(C-direct) 결과를 *직접 읽을 수 있는* 기능(클립보드/파일 등)은 오라클이 판정자 — VLM 화면판정을 *안 부름*.
        #   Notes 본문을 plaintext로 직접 읽어 판정한 것과 같은 원리(보이는 결과가 없어 VLM은 부적격).
        #   ★decisive와 다름: VLM 판단을 *뒤집는* 게 아니라 *애초에 부르지 않음*. ov=None(확인불가)이면
        #   거짓pass 안 만들고 판정불가(error). 거짓PASS≈0 유지 — 오라클이 성공계약 전체를 덮을 때만 authoritative.
        if bool((case.oracle or {}).get("authoritative")):
            from sapphi import probes
            oracle_res = probes.probe((case.oracle or {}).get("kind"),
                                      (case.oracle or {}).get("params"), injected)
            ov = oracle_res.get("achieved")
            judge_label = f"oracle:{(case.oracle or {}).get('kind')}"
            verdict = {"achieved": ov, "axis_results": [], "quality_axes": [], "directive": "",
                       "rationale": f"결정론 오라클이 결과를 직접 읽어 판정(VLM 화면판정 미사용) — {oracle_res.get('evidence','')}",
                       "diagnosis": ("" if ov else f"오라클: {oracle_res.get('evidence','')}"),
                       "oracle_evidence": [oracle_res]}
            if ov is None:                                   # 결정론으로도 확인 불가 → 판정불가(거짓pass 금지)
                achieved, conf, trust, status = False, 0.4, "oracle-authoritative(판정불가)", "error"
            else:
                achieved = bool(ov)
                trust = "oracle-authoritative(pass)" if ov else "oracle-authoritative(fail)"
                conf = 0.9   # 결정론 오라클 판정 = 높은 확신(VLM용 계산식 대신). 잔여 0.1 = 오라클 완전성 책임.
                status, gate = _case_status(case, achieved, gate, result)
            records.save(routines_dir, records.VerificationRecord(
                goal=case.goal, task_key=f"visio:{plan.feature_key}:{case.id}",
                achieved=achieved, rationale=verdict.get("rationale", ""),
                diagnosis=verdict.get("diagnosis", ""), directive="",
                screenshot_path=shot, trace_path=getattr(result, "trace_path", "") or "",
                oracle_evidence=[oracle_res], axis_results=[], confidence=conf))
            results.append(CaseResult(case, status, achieved, round(conf, 2), gate,
                                      verdict, {}, shot, judge=judge_label, oracle=oracle_res, trust=trust))
            _learn_from_case(routines_dir, plan, case, cspec, achieved, status, verdict, conf)
            if verbose:
                icon = {"pass": "✅", "fail": "❌", "blocked_by_gate": "🛑", "error": "⚠️"}.get(status, "·")
                print(f"    🎯 {judge_label} 결과 직접읽기 판정(VLM 생략): {icon} {status} (신뢰 {conf:.2f}, 신뢰닻 {trust})")
            continue

        judge_model, judge_provider, judge_label, escalate_ok = _judge_route(case)
        if verbose and judge_provider == "local":
            extra = " (불확실시 클라우드 escalate)" if escalate_ok else " · 스샷 클라우드 미전송"
            print(f"    🔒 온디바이스 판정({judge_label}){extra}")
        contract = taskspec.contract_text(cspec)
        try:
            verdict = verify_runner.verify_outcome(case.goal, shot, judge_model, trace=trace,
                                                   task_contract=contract, provider=judge_provider)
        except Exception as e:
            results.append(CaseResult(case, "error", gate=gate, screenshot_path=shot,
                                      judge=judge_label, note=f"판정 예외: {type(e).__name__}: {str(e)[:100]}"))
            continue
        judge_counts["local" if judge_provider == "local" else "cloud"] += 1
        achieved = bool(verdict.get("achieved"))
        # 티어드: 로컬 판정이 *불확실*하면 클라우드 opus로 escalate(비민감만). 쉬운 판정은 로컬=토큰 0.
        if judge_provider == "local" and escalate_ok:
            lconf = records.confidence_from_verdict(verdict, achieved)
            esc, why = _should_escalate(verdict, lconf)
            if esc:
                if verbose:
                    print(f"    ↑ 로컬 불확실({why}) → 클라우드 escalate")
                try:
                    verdict = verify_runner.verify_outcome(case.goal, shot, plan.judge_model,
                                                           trace=trace, task_contract=contract,
                                                           provider=None)
                    achieved = bool(verdict.get("achieved"))
                    judge_label = f"local→cloud:{plan.judge_model}({why})"
                    judge_counts["cloud"] += 1
                    judge_counts["escalated"] += 1
                except Exception:
                    pass   # escalation 실패 시 로컬 판정 유지(graceful)

        # (C2) 판정자 환각 차단 — 성공 증거 객체를 독립 재검(OCR + 선택 로컬VLM)
        pcheck = _perception_crosscheck(shot, case, verdict, local_vlm)
        exp_objs = _expected_objects(case)
        refuted = [o for o in exp_objs if o in (pcheck.get("flags") or [])]
        if achieved and refuted:
            achieved = False
            verdict["diagnosis"] = (verdict.get("diagnosis", "") +
                                    f" [VISIO: 판정자가 pass라 했으나 perception 재검이 부정: {refuted}]").strip()

        # (C2.5) 품질 축 게이트 — *관련 있는*(na 아닌) 비기능 품질 축이 fail이면 '됐어도 잘 안 된 것' → 강등.
        # 결정론 백스톱(판정자가 깜빡 achieved=true 줘도 품질 fail은 항상 반영). 도메인 무지(일반 축).
        q_fails = [q.get("axis") for q in (verdict.get("quality_axes") or []) if q.get("verdict") == "fail"]
        if achieved and q_fails:
            achieved = False
            verdict["diagnosis"] = (verdict.get("diagnosis", "")
                + f" [VISIO 품질 축 미달: {q_fails}]").strip()

        # (C3) 결정론 오라클 게이트 — LLM 판정 말고 *바깥 ground-truth*로 확인(거부권=거짓 pass 차단).
        # triggers(입력 자극)의 출력측 짝. '초록불'이 의미 있으려면 속일 수 없는 닻이 필요.
        oracle_res, trust = {}, "vlm-only"
        if case.oracle:
            from sapphi import probes
            oracle_res = probes.probe((case.oracle or {}).get("kind"),
                                      (case.oracle or {}).get("params"), injected)
            ov = oracle_res.get("achieved")
            if ov is None:                                # 닻 못 내림 → VLM에 위임
                trust = "vlm-only(오라클 확인불가)"
            elif achieved and ov is False:                # 거짓 pass → 결정론 거부(veto)
                achieved = False
                trust = "oracle-vetoed(거짓pass차단)"
                verdict["diagnosis"] = (verdict.get("diagnosis", "")
                    + f" [VISIO 오라클 거부: 결정론 확인 실패 — {oracle_res.get('evidence','')}]").strip()
            elif achieved and ov is True:                 # pass가 실제 상태로 뒷받침
                trust = "oracle-confirmed(pass)"
            elif (not achieved) and ov is True:           # VLM은 fail이나 사실은 됨(품질 등으로 fail?)
                trust = "oracle-says-ok(VLM품질fail?)"
            else:                                         # 둘 다 fail → fail이 결정론적으로 실재
                trust = "oracle-confirmed(fail)"
            if verbose:
                print(f"    🔎 오라클({(case.oracle or {}).get('kind')}): achieved={ov} → 신뢰닻={trust}")

        conf = records.confidence_from_verdict(verdict, achieved)
        status, gate = _case_status(case, achieved, gate, result)

        # (D) 기록 — 회귀 키
        records.save(routines_dir, records.VerificationRecord(
            goal=case.goal, task_key=f"visio:{plan.feature_key}:{case.id}",
            achieved=achieved, rationale=verdict.get("rationale", ""),
            diagnosis=verdict.get("diagnosis", ""), directive=verdict.get("directive", ""),
            screenshot_path=shot, trace_path=getattr(result, "trace_path", "") or "",
            oracle_evidence=verdict.get("oracle_evidence") or [],
            axis_results=verdict.get("axis_results") or [], confidence=conf))
        results.append(CaseResult(case, status, achieved, round(conf, 2), gate,
                                  verdict, pcheck, shot, judge=judge_label,
                                  oracle=oracle_res, trust=trust))
        _learn_from_case(routines_dir, plan, case, cspec, achieved, status, verdict, conf)
        if verbose:
            icon = {"pass": "✅", "fail": "❌", "blocked_by_gate": "🛑", "error": "⚠️"}.get(status, "·")
            print(f"    {icon} {status} (신뢰 {conf:.2f}, 게이트 {gate}, 판정 {judge_label}, 신뢰닻 {trust})"
                  + (f" · 품질미달 {q_fails}" if q_fails else "")
                  + (f" · 환각재검부정 {refuted}" if refuted else ""))

    # (E) 집계 + 회귀 + 리포트
    rep = VisioReport(plan, results,
                      passed=sum(1 for r in results if r.status == "pass"),
                      failed=sum(1 for r in results if r.status == "fail"),
                      gated=sum(1 for r in results if r.status == "blocked_by_gate"),
                      errored=sum(1 for r in results if r.status == "error"),
                      blocked=sum(1 for r in results if r.status == "blocked_pending_fixture"))
    rep.judge_calls = judge_counts
    rep.regression = _regression_diff(routines_dir, plan, results)
    rep.report_path = _write_report(rep, out_dir)
    return rep


def _learn_from_case(routines_dir: str, plan: TestPlan, case: TestCase, cspec,
                     achieved: bool, status: str, verdict: dict, conf: float) -> int:
    """판정(pass/fail) 직후 EMERI에 *테스트 교훈*을 적재 — 다음 설계가 recall로 받아
    '저번에 이 기능 이 부분 약했지→더 본다'를 실현한다(학습 루프의 *write* 쪽 = 그동안 빠졌던 곳).

    결정론(LLM 0회·비용 없음): 케이스 결과를 작업유형(task_key)별 절차기억으로 저장.
    ★RUBI 오라클이 판정한 *뒤에만*(status가 pass/fail일 때만) — error/blocked는 학습 안 함(기억 오염 방지)."""
    from . import emeri
    if status not in ("pass", "fail"):
        return 0
    diag = (verdict.get("diagnosis") or "").strip()
    failed_axis = (next((a.get("axis") for a in (verdict.get("axis_results") or [])
                         if a.get("verdict") == "fail"), "")
                   or next((q.get("axis") for q in (verdict.get("quality_axes") or [])
                            if q.get("verdict") == "fail"), ""))
    when = f"'{plan.feature}' 류({getattr(cspec, 'task_type', '')}) 기능 테스트 시 — 케이스 «{case.title}»"
    if achieved:
        then = "이 시나리오는 통과 — 유효한 검사로 다음 설계에도 포함하라"
    else:
        then = f"이 시나리오에서 실패(약점) — 다음 테스트 때 우선 검사하라: {diag or case.rationale or case.expected}"
    ok = emeri.save_lesson(routines_dir, goal=case.goal, when=when[:200], then=then[:200],
                           works=achieved, task_key=cspec.key(), axis=failed_axis,
                           fail_reason=("" if achieved else diag[:180]),
                           confidence=max(0.0, min(float(conf or 0.0), 1.0)))
    return 1 if ok else 0


def _observe_state(isolate: str | None, out_dir: str, case: TestCase) -> dict:
    """실제 상태를 AX 우선으로 읽고 전제조건을 확인(읽기전용). 불충족=환경문제."""
    from sapphi import perceive, state_snapshot
    try:
        if isolate:
            perceive.open_app(isolate)
            perceive.focus_app(isolate)
        shot = os.path.join(out_dir, "observe.png")
        perceive.screenshot(shot, app=isolate)
        snap = state_snapshot.capture(shot, out_dir, 0, 0, isolate_app=isolate,
                                      expected_front=perceive.frontmost_app())
        text = snap.prompt_text()
    except Exception as e:
        return {"ok": True, "snapshot_text": f"(관찰 실패: {type(e).__name__})", "missing": []}
    missing = []
    for p in case.preconditions or []:
        toks = _tokens(p)
        if toks and not (toks & _tokens(text)):
            missing.append(p)
    # 전제조건이 *상태 변화*를 요구하는 경우가 많아(예: "앱이 닫혀있음"), 불일치를 hard-fail 하지 않고
    # 경고만 — 진짜 막힘은 실행/판정 단계가 잡는다. (missing 은 리포트에 남김)
    return {"ok": True, "snapshot_text": text[:1500], "missing": missing}


def _scope_shot(out_dir: str, app: str | None) -> tuple:
    """검증 스샷을 대상 앱 창으로 격리(autoloop._verification_scope_shot 미러 — 결합 회피)."""
    from sapphi import perceive
    import time as _t
    target = app or perceive.frontmost_app()
    if not target:
        return None, "전체 화면"
    path = os.path.join(out_dir, "verify_scope.png")
    try:
        perceive.focus_app(target)
        _t.sleep(0.4)
        perceive.screenshot(path, app=target)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path, f"앱 창 격리: {target}"
    except Exception:
        return None, "전체 화면"
    return None, "전체 화면"


def _trace_text(result) -> str:
    lines = []
    for s in getattr(result, "steps", []) or []:
        try:
            a = s.action.short() if getattr(s, "action", None) else "?"
            r = s.result.summary() if getattr(s, "result", None) else (
                "실행됨" if getattr(s, "executed", False) else f"게이트:{getattr(s, 'gate', '')}")
            lines.append(f"- {a} → {r}")
        except Exception:
            continue
    return "\n".join(lines) or "(행동 없음)"


def _run_agent_fixture(path: str, params: dict) -> dict:
    """에이전트가 작성한 픽스처(주입기)를 *VISIO의 입력*으로 실행. stdout JSON=injected."""
    import subprocess
    try:
        arg = json.dumps(params, ensure_ascii=False)
        cmd = ["python", path, "--params", arg] if path.endswith(".py") else [path, arg]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = (r.stdout or "").strip()
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {"raw": out[:500], "params": params}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}", "params": params}


def _run_sut(entry: str, injected: dict) -> dict:
    """빌드된 SUT(기능)를 트리거. entry=실행 명령(스크립트/CLI).
    injected(자극 ground truth)를 VISIO_INJECTED(env, JSON) + stdin 으로 SUT에 전달
    — '알림 훅이 payload를 기능에 넘김' 모사(보호된 알림 DB 직접읽기 회피)."""
    import subprocess
    try:
        payload = json.dumps(injected or {}, ensure_ascii=False)
        env = {**os.environ, "VISIO_INJECTED": payload}
        r = subprocess.run(entry, shell=True, capture_output=True, text=True,
                           timeout=60, env=env, input=payload)
        return {"ok": r.returncode == 0, "output": ((r.stdout or "") + (r.stderr or "")).strip()[:1000]}
    except Exception as e:
        return {"ok": False, "output": f"{type(e).__name__}: {str(e)[:120]}"}


def _perception_crosscheck(shot: str, case: TestCase, verdict: dict, local_vlm: bool) -> dict:
    """성공 증거 객체를 독립 재검(OCR 교차 + 선택 로컬 VLM)으로 — 판정자 환각 차단."""
    from . import perception_verify
    exp = _expected_objects(case)
    if not exp or not shot:
        return {"objects": [], "verified": [], "flags": [], "caption_trust": 0.5}
    ocr_text = ""
    for e in verdict.get("oracle_evidence") or []:
        if e.get("kind") == "ocr" and e.get("status") in ("success", "empty"):
            ocr_text = e.get("summary", "")
            break
    try:
        return perception_verify.verify_keyframe(shot, verdict.get("rationale", ""), exp,
                                                 ocr_text, recheck=local_vlm)
    except Exception as e:
        return {"objects": [], "verified": [], "flags": [], "caption_trust": 0.5,
                "error": f"{type(e).__name__}: {str(e)[:100]}"}


def _case_status(case: TestCase, achieved: bool, gate: str, result) -> tuple:
    # 비가역/금전 케이스: '커밋 직전까지 가서 멈춤' = 올바른 동작 = pass(달성할 postcondition).
    if case.must_confirm and gate == "stopped_at_commit":
        return "pass", gate
    # 가역인데 커밋에서 멈춤 = 예상 밖(잘못된 비가역 분류/엉뚱 커밋 시도) → fail.
    if gate == "stopped_at_commit" and not case.must_confirm:
        return "fail", gate
    if getattr(result, "error", None):
        return "fail", gate
    return ("pass" if achieved else "fail"), gate


_ESCALATE_CONF = 0.7   # 로컬 판정 신뢰가 이 아래면 클라우드로 escalate


def _should_escalate(verdict: dict, conf: float) -> tuple:
    """로컬 판정이 *불확실*한가 → 클라우드 재판정 필요. (esc, 이유)."""
    axes = verdict.get("axis_results") or []
    if any(a.get("verdict") == "uncertain" for a in axes):
        return True, "축 uncertain"
    if conf < _ESCALATE_CONF:
        return True, f"신뢰{conf:.2f}"
    # 달성 주장인데 결정적 오라클(목표 텍스트 겹침) 근거가 0 → 환각 의심 → 재확인
    oe = verdict.get("oracle_evidence") or []
    ov = next((e for e in oe if e.get("kind") == "goal_text_overlap"), None)
    if verdict.get("achieved") and ov and ov.get("status") == "empty":
        return True, "근거없는pass"
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# 3) 회귀 diff + 리포트
# ─────────────────────────────────────────────────────────────────────────────
def _regression_diff(routines_dir: str, plan: TestPlan, results: list) -> dict:
    from . import records
    rows = records.load(routines_dir)
    prefix = f"visio:{plan.feature_key}:"
    # 케이스별 *직전*(이번 런 제외) achieved 모음
    history: dict = {}
    for row in rows:
        tk = row.get("task_key", "")
        if tk.startswith(prefix):
            history.setdefault(tk[len(prefix):], []).append(bool(row.get("achieved")))
    out = {"new_fails": [], "fixed": [], "still_failing": [], "flaky": []}
    for r in results:
        cid = r.case.id
        hist = history.get(cid, [])
        # 이번 런 record가 이미 저장돼 hist 맨끝에 있음 → *직전*은 hist[-2]. 첫 런이면 prior 없음=None.
        prev = hist[-2] if len(hist) >= 2 else None
        cur = r.status == "pass"
        if prev is None:
            continue
        if prev and not cur:
            out["new_fails"].append(cid)
        elif not prev and cur:
            out["fixed"].append(cid)
        elif not prev and not cur:
            out["still_failing"].append(cid)
        if len(hist) >= 3 and len(set(hist[-3:])) > 1:
            out["flaky"].append(cid)
    return out


_ICON = {"pass": "✅", "fail": "❌", "blocked_by_gate": "🛑", "error": "⚠️", "blocked_pending_fixture": "⏸"}
_QMARK = {"pass": "✓", "fail": "✗", "na": "·"}


def _fmt_quality(qaxes) -> str:
    """비기능 품질 축을 'usability✗ efficiency_cost✓' 식으로(na 제외). 없으면 빈 문자열."""
    out = [f"{q.get('axis')}{_QMARK.get(q.get('verdict'), '?')}"
           for q in (qaxes or []) if q.get("verdict") in ("pass", "fail")]
    return " ".join(out)


def _write_report(rep: VisioReport, out_dir: str) -> str:
    p = rep.plan
    lines = [
        f"# VISIO 리포트 — {p.feature}",
        "",
        f"- 생성: {_now()}",
        f"- 판정 모델(judge): `{p.judge_model}`  ·  실행 모델(build): `{p.build_model}`"
        + ("  ⚠️동일=독립성 약함" if p.judge_model == p.build_model else "  ✓독립"),
        f"- 결과: ✅ {rep.passed}  ❌ {rep.failed}  🛑 {rep.gated}  ⚠️ {rep.errored}  ⏸ {rep.blocked}(픽스처대기)  (총 {len(rep.results)})",
        f"- 판정 콜: 로컬 {(rep.judge_calls or {}).get('local',0)} · 클라우드 "
        f"{(rep.judge_calls or {}).get('cloud',0)} (escalate {(rep.judge_calls or {}).get('escalated',0)}) "
        f"— 클라우드 토큰은 클라우드 콜에만 발생",
        "",
    ]
    reg = rep.regression or {}
    if any(reg.get(k) for k in ("new_fails", "fixed", "flaky", "still_failing")):
        lines += ["## 회귀(regression)",
                  f"- 🔴 새로 깨짐(new_fails): {reg.get('new_fails') or '-'}",
                  f"- 🟢 고쳐짐(fixed): {reg.get('fixed') or '-'}",
                  f"- ⚪ 계속 실패: {reg.get('still_failing') or '-'}",
                  f"- 🟡 불안정(flaky): {reg.get('flaky') or '-'}", ""]
    lines += ["## 케이스", "", "| 케이스 | 판정 | 신뢰 | 신뢰닻(oracle) | 게이트 | 판정자 |",
              "|---|---|---|---|---|---|"]
    for r in rep.results:
        lines.append(f"| {r.case.id} · {r.case.title} | {_ICON.get(r.status,'·')} {r.status} "
                     f"| {r.confidence:.2f} | {getattr(r,'trust','vlm-only')} | {r.gate} | {r.judge} |")
    lines.append("")
    if any(_fmt_quality(r.verdict.get("quality_axes")) for r in rep.results):
        lines += ["## 비기능 품질 축(quality)", "", "| 케이스 | 품질 평가(na 제외) |", "|---|---|"]
        for r in rep.results:
            q = _fmt_quality(r.verdict.get("quality_axes"))
            if q:
                lines.append(f"| {r.case.id} | {q} |")
        lines.append("")
    for r in rep.results:
        if r.status in ("fail", "error"):
            lines += [f"### ❌ {r.case.id} — {r.case.title}",
                      f"- 가설: {r.case.rationale}",
                      f"- 기대: {r.case.expected}",
                      f"- 진단: {r.verdict.get('diagnosis') or r.note}",
                      f"- 다음 지시(워커용): {r.verdict.get('directive','')}",
                      (f"- 📐 품질 축: {_fmt_quality(r.verdict.get('quality_axes'))}"
                       if _fmt_quality(r.verdict.get("quality_axes")) else ""),
                      f"- 증거 스샷: `{r.screenshot_path}`",
                      (f"- 🚫 perception 환각 flag: {r.perception_check.get('flags')}"
                       if r.perception_check.get("flags") else ""), ""]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"visio_report_{p.feature_key}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join([ln for ln in lines if ln is not None]))
    return path


# ─────────────────────────────────────────────────────────────────────────────
# print 헬퍼 (CLI)
# ─────────────────────────────────────────────────────────────────────────────
def print_plan(plan: TestPlan) -> None:
    print(f"\n📋 VISIO 테스트 계획 — {plan.feature}")
    print(f"   judge={plan.judge_model}  build={plan.build_model}  케이스 {len(plan.cases)}개")
    for c in plan.cases:
        lock = " 🔒커밋게이트" if c.must_confirm else ""
        print(f"  • [{c.id}] {c.title}{lock}")
        print(f"      목표: {c.goal}")
        if c.expected:
            print(f"      기대: {c.expected}")
        kind = (c.stimulus or {}).get("kind")
        if kind:
            mark = "⚡직접" if c.fixture.startswith("native") else "🧩픽스처요청"
            print(f"      자극: {kind} ({mark})")
    if plan.fixture_requests:
        print(f"\n  🧩 에이전트가 만들 픽스처(VISIO가 직접 못 만드는 자극) {len(plan.fixture_requests)}개 — `visio fixtures`로 요청서:")
        for fr in plan.fixture_requests:
            print(f"     · [{fr['case_id']}] kind={fr['kind']}  params={fr.get('params')}")


def print_fixture_requests(plan: TestPlan) -> None:
    """VISIO가 *직접 못 만드는 자극* = 에이전트에게 보내는 픽스처(주입기) 요청서."""
    print(f"\n🧩 VISIO 픽스처 요청서 — {plan.feature}")
    if not plan.fixture_requests:
        print("   (직접 못 만드는 자극 없음 — 모든 자극을 VISIO가 native 도구로 생성 가능)")
    else:
        print("   아래 자극은 VISIO가 직접 못 만듦 → 에이전트가 *파라미터화 주입기*를 작성:")
        for fr in plan.fixture_requests:
            print(f"   · case={fr['case_id']}  kind={fr['kind']}  params={fr.get('params')}")
            print(f"       why: {fr.get('why')}")
            print(f"       → 작성 위치 예: visio_fixtures/{plan.feature_key}/inject_{fr['kind']}.py "
                  f"(`--params '<json>'` 받아 주입 후 stdout에 injected JSON)")
    if not plan.sut_entry:
        print("\n   ▷ SUT 진입점(sut_entry) 미설정 — 현재 워커가 기능을 *즉흥 실행*(전환기). "
              "빌드된 기능이 있으면 plan json의 sut_entry에 실행명령 지정.")


def print_summary(rep: VisioReport) -> None:
    print(f"\n📊 VISIO 결과 — {rep.plan.feature}")
    print(f"   ✅ {rep.passed}  ❌ {rep.failed}  🛑 {rep.gated}  ⚠️ {rep.errored}  ⏸ {rep.blocked}  (총 {len(rep.results)})")
    for r in rep.results:
        q = _fmt_quality(r.verdict.get("quality_axes"))
        print(f"   {_ICON.get(r.status,'·')} [{r.case.id}] {r.status}  신뢰 {r.confidence:.2f}  "
              f"신뢰닻 {getattr(r,'trust','vlm-only')}  게이트 {r.gate}"
              + (f"  품질[{q}]" if q else ""))
    if any((rep.regression or {}).get(k) for k in ("new_fails", "fixed", "flaky")):
        print(f"   회귀: 새깨짐 {rep.regression.get('new_fails')}  고쳐짐 {rep.regression.get('fixed')}  "
              f"불안정 {rep.regression.get('flaky')}")
    jc = rep.judge_calls or {}
    if jc.get("local") or jc.get("cloud"):
        print(f"   판정 콜: 로컬 {jc.get('local',0)} · 클라우드 {jc.get('cloud',0)} "
              f"(escalate {jc.get('escalated',0)})  ← 클라우드 토큰은 {jc.get('cloud',0)}콜만, 로컬은 토큰 0")
    print(f"   📄 리포트: {rep.report_path}")


def print_regression(rep: VisioReport) -> None:
    reg = rep.regression or {}
    print(f"\n🔁 VISIO 회귀 — {rep.plan.feature}")
    print(f"   🔴 새로 깨짐: {reg.get('new_fails') or '없음'}")
    print(f"   🟢 고쳐짐: {reg.get('fixed') or '없음'}")
    print(f"   🟡 불안정: {reg.get('flaky') or '없음'}")
    print(f"   📄 리포트: {rep.report_path}")
