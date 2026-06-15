"""TaskSpec / SkillTemplate memory.

새 목표를 1회성 자연어로만 두지 않고, 작업 유형과 변수로 분해한다.
예: "엄마한테 오늘 저녁메뉴 뭐냐고 카톡으로 물어봐"
  → task_type=message_send, channel=kakaotalk, variables={recipient:엄마, message:...}

이 spec 은 실행 전 워커에게 주입되고, 성공 뒤에는 SkillTemplate 으로 저장되어
다음 비슷한 요청에서 재사용된다.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict

from .provider import _cli_json


@dataclass
class TaskSpec:
    task_type: str
    channel: str = ""
    app: str = ""
    variables: dict | None = None
    risk: str = "medium"
    requires_confirmation: bool = False
    postconditions: list[str] | None = None
    evidence_collectors: list[str] | None = None
    audit_axes: list[str] | None = None
    lens_contracts: dict | None = None
    confidence: float = 0.5
    source: str = "fallback"

    def key(self) -> str:
        return f"{self.task_type}:{self.channel or self.app or '*'}"

    def prompt_block(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass
class SkillTemplate:
    key: str
    task_type: str
    channel: str = ""
    app: str = ""
    variables: list[str] | None = None
    recipe: list[str] | None = None
    postconditions: list[str] | None = None
    evidence_collectors: list[str] | None = None
    audit_axes: list[str] | None = None
    lens_contracts: dict | None = None
    risk: str = "medium"
    examples: list[str] | None = None
    verified_count: int = 0

    def prompt_block(self, spec: TaskSpec | None = None) -> str:
        data = asdict(self)
        if spec:
            data["current_variables"] = spec.variables or {}
        return json.dumps(data, ensure_ascii=False, indent=2)


def _path(routines_dir: str) -> str:
    return os.path.join(routines_dir, "skill_templates.json")


def load_templates(routines_dir: str) -> list[dict]:
    p = _path(routines_dir)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_template(routines_dir: str, spec: TaskSpec, goal: str) -> bool:
    """성공한 TaskSpec 을 일반화된 SkillTemplate 으로 저장/갱신한다."""
    if spec.task_type == "generic":
        return False
    tmpl = _template_from_spec(spec, goal)
    rows = load_templates(routines_dir)
    changed = False
    for row in rows:
        if row.get("key") == tmpl.key:
            row["verified_count"] = int(row.get("verified_count") or 0) + 1
            ex = row.setdefault("examples", [])
            if goal not in ex:
                ex.append(goal[:160])
            # 새 postcondition/evidence 가 더 구체적이면 합친다.
            row["postconditions"] = _merge_list(row.get("postconditions"), tmpl.postconditions)
            row["evidence_collectors"] = _merge_list(row.get("evidence_collectors"), tmpl.evidence_collectors)
            row["audit_axes"] = _merge_list(row.get("audit_axes"), tmpl.audit_axes)
            row["lens_contracts"] = _merge_contracts(row.get("lens_contracts"), tmpl.lens_contracts)
            changed = True
            break
    if not changed:
        rows.append(asdict(tmpl))
        changed = True
    if changed:
        os.makedirs(routines_dir, exist_ok=True)
        with open(_path(routines_dir), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    return changed


def _napp(s) -> str:
    return re.sub(r"[\s_\-]+", "", str(s or "").lower())


def find_template(routines_dir: str, spec: TaskSpec) -> SkillTemplate | None:
    rows = load_templates(routines_dir)
    if not rows:
        return None
    # 1순위: 정확 키(task_type:app/channel) 매칭.
    for row in rows:
        if row.get("key") == spec.key():
            return SkillTemplate(**_clean_template(row))
    # 2순위: task_type 동일 + *앱 호환*. ★특정앱 템플릿(naver_maps)을 *다른/무관한 작업*(날씨 등)에
    #   재사용하면 엉뚱한 스킬을 끌어온다(실측 버그) → 템플릿이 *범용(app 없음)*이거나 *정확히 같은 앱*일 때만.
    for row in rows:
        if row.get("task_type") != spec.task_type:
            continue
        tmpl_app = _napp(row.get("app"))
        if tmpl_app and tmpl_app != _napp(spec.app):
            continue   # 특정앱 템플릿은 같은 앱 작업에만(범용 템플릿은 app="" 이라 통과)
        if row.get("channel") and spec.channel and row.get("channel") != spec.channel:
            continue
        return SkillTemplate(**_clean_template(row))
    return None


def classify_goal(goal: str, model: str | None = None, use_llm: bool = True,
                  prefer_fallback_confidence: float = 0.78) -> TaskSpec:
    """목표를 TaskSpec 으로 분류한다.

    fallback 이 먼저 빠르게 잡고, 애매하면 LLM 으로 보정한다. LLM 실패 시 fallback 유지.
    """
    fallback = _fallback_classify(goal)
    # 의미 있는 런타임 레이어로 만들기 위해, 명확한 유형은 LLM에게 다시 묻지 않는다.
    # "카톡으로 ... 보내" 같은 고신뢰 fallback 은 규칙 기반 TaskSpec 이 더 안정적이다.
    if fallback.confidence >= prefer_fallback_confidence:
        return fallback
    if not use_llm or not model:
        return fallback
    try:
        data = _llm_classify(goal, fallback, model)
        spec = _spec_from_dict(data)
        if spec.task_type:
            return spec
    except Exception:
        pass
    return fallback


def spec_text(spec: TaskSpec, template: SkillTemplate | None = None) -> str:
    lines = ["작업 분류(TaskSpec):", spec.prompt_block()]
    if template:
        lines += ["", "재사용할 검증된 SkillTemplate:", template.prompt_block(spec)]
        lines.append(
            "위 template 의 변수 자리({recipient}, {message}, {artifact} 등)는 current_variables 로 채워라. "
            "검증 조건(postconditions)을 완료 판단 전에 반드시 만족시켜라."
        )
    return "\n".join(lines)


def contract_text(spec: TaskSpec) -> str:
    """RUBI verifier 가 검사할 CUA 런타임 3렌즈 계약."""
    return json.dumps({
        "audit_axes": spec.audit_axes or [],
        "lens_contracts": spec.lens_contracts or {},
        "postconditions": spec.postconditions or [],
        "not_sufficient_global": _global_not_sufficient(spec),
    }, ensure_ascii=False, indent=2)


def _fallback_classify(goal: str) -> TaskSpec:
    g = goal.strip()
    low = g.lower()
    # ★안전 바닥: 비가역 파괴(삭제/제거/휴지통/wipe)는 *LLM 분류 없이도* data_mutation + 커밋확인으로 분류.
    #   커밋게이트가 LLM 콜이 일어났느냐에 의존하면 안 된다(안전 분류는 결정론이어야).
    if _looks_destructive(low):
        return TaskSpec(
            task_type="data_mutation",
            variables={"goal": g},
            risk="high",
            requires_confirmation=True,
            postconditions=[
                "destructive_action_is_confirmed_or_gated_before_commit",
                "only_the_intended_target_is_affected",
                "irreversible_step_is_reversible_or_verified_safe_before_commit",
            ],
            evidence_collectors=["filesystem", "trace"],
            audit_axes=["state_robustness", "intent_sufficiency", "evidence_adequacy"],
            lens_contracts=_contracts_for("data_mutation", ""),
            confidence=0.8,
            source="fallback",
        )
    if _looks_like_kakao_message(low):
        recipient = _extract_recipient(g)
        message = _extract_message(g, recipient)
        return TaskSpec(
            task_type="message_send",
            channel="kakaotalk",
            app="KakaoTalk",
            variables={"recipient": recipient, "message": message},
            risk="high",
            requires_confirmation=True,
            postconditions=[
                "recipient_verified",
                "message_text_matches_requested_content",
                "message_appears_as_outgoing_bubble",
                "input_field_empty_after_send",
            ],
            evidence_collectors=["ocr", "ax", "screenshot_before_after", "trace"],
            audit_axes=["state_robustness", "intent_sufficiency", "evidence_adequacy"],
            lens_contracts=_contracts_for("message_send", "kakaotalk"),
            confidence=0.82,
            source="fallback",
        )
    if _looks_like_navigation_route(g):
        destination = _extract_destination(g)
        return TaskSpec(
            task_type="navigation_route",
            channel="map",
            app="NaverMap",
            variables={"origin": "current_location", "destination": destination, "query": g},
            risk="low",
            requires_confirmation=False,
            postconditions=[
                "route_results_visible",
                "requested_destination_visible",
                "origin_uses_current_location_or_device_coordinates",
            ],
            evidence_collectors=["ocr", "ax", "screenshot_before_after", "trace", "device_signals"],
            audit_axes=["state_robustness", "intent_sufficiency", "evidence_adequacy"],
            lens_contracts=_contracts_for("navigation_route", "map"),
            confidence=0.84 if destination else 0.76,
            source="fallback",
        )
    if _looks_like_research_summary(low):
        return TaskSpec(
            task_type="research_and_summarize",
            channel="youtube" if "유튜브" in low or "youtube" in low else "",
            app="YouTube" if "유튜브" in low or "youtube" in low else "",
            variables={"query": g},
            risk="low",
            requires_confirmation=False,
            postconditions=[
                "multiple_relevant_sources_inspected",
                "actual_content_or_transcripts_observed",
                "written_summary_produced",
                "summary_mentions_common_themes_or_pros_cons",
            ],
            evidence_collectors=["ocr", "browser_dom", "trace"],
            audit_axes=["intent_sufficiency", "evidence_adequacy"],
            lens_contracts=_contracts_for("research_and_summarize", "youtube"),
            confidence=0.78,
            source="fallback",
        )
    if any(w in low for w in ("파일", "file", ".txt", ".md", ".csv")) and any(
        w in low for w in ("만들", "생성", "저장", "create", "write")
    ):
        artifact = _extract_artifact(g)
        return TaskSpec(
            task_type="file_artifact_create_or_edit",
            variables={"artifact": artifact},
            risk="medium",
            requires_confirmation=False,
            postconditions=["file_exists_or_changed", "file_name_matches_request"],
            evidence_collectors=["filesystem", "trace"],
            audit_axes=["state_robustness", "intent_sufficiency", "evidence_adequacy"],
            lens_contracts=_contracts_for("file_artifact_create_or_edit", ""),
            confidence=0.65,
            source="fallback",
        )
    if any(w in low for w in ("검색", "search", "찾아", "알아봐", "알아내")):
        return TaskSpec(
            task_type="search_and_report",
            variables={"query": g},
            risk="low",
            requires_confirmation=False,
            postconditions=["answer_is_visible_or_reported", "source_or_screen_evidence_exists"],
            evidence_collectors=["ocr", "browser_dom", "trace"],
            audit_axes=["intent_sufficiency", "evidence_adequacy"],
            lens_contracts=_contracts_for("search_and_report", ""),
            confidence=0.55,
            source="fallback",
        )
    return TaskSpec(
        task_type="generic",
        variables={"goal": g},
        risk="medium",
        requires_confirmation=False,
        postconditions=["goal_specific_success_evidence_exists"],
        evidence_collectors=["ocr", "trace"],
        audit_axes=["intent_sufficiency", "evidence_adequacy"],
        lens_contracts=_contracts_for("generic", ""),
        confidence=0.3,
        source="fallback",
    )


def _llm_classify(goal: str, fallback: TaskSpec, model: str) -> dict:
    prompt = (
        "너는 CUA 작업 분류기다. 사용자 목표를 *재사용 가능한* TaskSpec 으로 분해하라. "
        "변수는 절대 루틴에 박지 말고 variables 로 뽑아라. 예: recipient, message, artifact, query.\n"
        "★★lens_contracts·postconditions 도 *이 한 번의 목표값을 절대 박지 마라* — 이건 *같은 종류의 모든 작업*에 "
        "재사용되는 계약이다. 구체값(예 '송도달빛축제공원역'·'강남역'·특정 사람이름·특정 파일명) 대신 "
        "*역할어*로 써라: '요청한 목적지가 결과에 표시됨', 'the requested destination is shown', "
        "'요청한 수신자에게 보냄'. (구체값을 박으면 다음 작업에서 *엉뚱한 값을 검증*하는 오염이 된다 — 실측 버그.)\n"
        "task_type 후보: message_send, navigation_route, file_artifact_create_or_edit, browser_navigation, "
        "form_submit, search_and_report, data_mutation, settings_change, purchase_or_payment, "
        "account_or_security_change, generic.\n"
        "evidence_collectors 후보: ocr, ax, browser_dom, filesystem, screenshot_before_after, trace, clipboard.\n"
        "카톡/카카오톡 메시지 보내기/묻기는 message_send + channel=kakaotalk 이다. "
        "'여기서/현재 위치에서 X까지', '길찾기', '경로 알려줘'는 navigation_route + channel=map + app=NaverMap 이다. "
        "전송/삭제/결제/게시 같은 외부 커밋은 requires_confirmation=true.\n\n"
        f"## 사용자 목표\n{goal}\n\n"
        f"## fallback 분석\n{fallback.prompt_block()}\n\n"
        "JSON 하나만:\n"
        '{"task_type":str,"channel":str,"app":str,"variables":object,"risk":"low|medium|high|critical",'
        '"requires_confirmation":bool,"postconditions":[str],"evidence_collectors":[str],'
        '"audit_axes":["state_robustness|intent_sufficiency|evidence_adequacy"],'
        '"lens_contracts":object,"confidence":number}'
    )
    data = _cli_json(prompt, model)
    data["source"] = "llm"
    return data


def _generalize_text(text: str, variables: dict) -> str:
    """계약/postcondition 문장에서 *이번 작업의 구체값*을 역할어로 치환 → 다음 작업에 재사용해도 안 깨진다.
    예: variables={destination:'강남역'} 이면 '강남역이 표시됨' → '요청한 destination 이 표시됨'.
    (LLM이 placeholder 지침을 어겨 구체값을 박아도 잡는 *방어층* — 실측된 오염을 코드로 차단.)"""
    out = str(text or "")
    for k, v in (variables or {}).items():
        v = str(v or "").strip()
        if len(v) >= 2 and v in out:    # 너무 짧은 값(빈 origin 등)은 오치환 방지로 건너뜀
            out = out.replace(v, f"요청한 {k}")
    return out


def _generalize_contracts(contracts: dict, variables: dict) -> dict:
    if not isinstance(contracts, dict):
        return contracts or {}
    out = {}
    for axis, c in contracts.items():
        if isinstance(c, dict):
            out[axis] = {
                k: ([_generalize_text(s, variables) for s in v] if isinstance(v, list)
                    else _generalize_text(v, variables) if isinstance(v, str) else v)
                for k, v in c.items()
            }
        else:
            out[axis] = c
    return out


def _template_from_spec(spec: TaskSpec, goal: str) -> SkillTemplate:
    variables = sorted((spec.variables or {}).keys())
    if spec.task_type == "message_send" and spec.channel == "kakaotalk":
        recipe = [
            "activate KakaoTalk",
            "find_or_open_chat({recipient})",
            "verify_recipient({recipient})",
            "type_message({message})",
            "ask_user_confirmation_before_commit",
            "send_message",
            "verify_outgoing_bubble({message})",
        ]
    elif spec.task_type == "file_artifact_create_or_edit":
        recipe = [
            "resolve_target_path({artifact})",
            "create_or_edit_file",
            "verify_filesystem_diff",
        ]
    elif spec.task_type == "search_and_report":
        recipe = [
            "open_or_use_browser",
            "search({query})",
            "read_result_from_screen_or_dom",
            "report_answer_with_evidence",
        ]
    elif spec.task_type == "navigation_route":
        recipe = [
            "activate map app",
            "use current_location as origin",
            "enter_destination({destination})",
            "open_route_results",
            "verify_origin_destination_and_route_visible",
        ]
    else:
        recipe = ["execute_goal", "verify_postconditions"]
    channel = spec.channel or ""
    key = spec.key()
    return SkillTemplate(
        key=key,
        task_type=spec.task_type,
        channel=channel,
        app=spec.app,
        variables=variables,
        recipe=recipe,
        # ★저장 전 일반화: 이번 작업의 구체값을 역할어로 치환(다음 작업에 재사용해도 엉뚱한 값 검증 안 함).
        postconditions=[_generalize_text(p, spec.variables or {}) for p in (spec.postconditions or [])],
        evidence_collectors=spec.evidence_collectors or [],
        audit_axes=spec.audit_axes or [],
        lens_contracts=_generalize_contracts(spec.lens_contracts or {}, spec.variables or {}),
        risk=spec.risk,
        examples=[goal[:160]],
        verified_count=1,
    )


def _spec_from_dict(data: dict) -> TaskSpec:
    if not isinstance(data, dict):
        return TaskSpec("generic")
    task_type = str(data.get("task_type") or "generic")
    channel = str(data.get("channel") or "")
    axes = data.get("audit_axes") if isinstance(data.get("audit_axes"), list) else []
    contracts = data.get("lens_contracts") if isinstance(data.get("lens_contracts"), dict) else {}
    if not axes:
        axes = list(_contracts_for(task_type, channel).keys())
    if not contracts:
        contracts = _contracts_for(task_type, channel)
    return TaskSpec(
        task_type=task_type,
        channel=channel,
        app=str(data.get("app") or ""),
        variables=data.get("variables") if isinstance(data.get("variables"), dict) else {},
        risk=str(data.get("risk") or "medium"),
        requires_confirmation=bool(data.get("requires_confirmation")),
        postconditions=data.get("postconditions") if isinstance(data.get("postconditions"), list) else [],
        evidence_collectors=data.get("evidence_collectors") if isinstance(data.get("evidence_collectors"), list) else [],
        audit_axes=axes,
        lens_contracts=contracts,
        confidence=float(data.get("confidence") or 0.5),
        source=str(data.get("source") or "llm"),
    )


def _clean_template(row: dict) -> dict:
    return {
        "key": row.get("key", ""),
        "task_type": row.get("task_type", "generic"),
        "channel": row.get("channel", ""),
        "app": row.get("app", ""),
        "variables": row.get("variables") if isinstance(row.get("variables"), list) else [],
        "recipe": row.get("recipe") if isinstance(row.get("recipe"), list) else [],
        "postconditions": row.get("postconditions") if isinstance(row.get("postconditions"), list) else [],
        "evidence_collectors": row.get("evidence_collectors") if isinstance(row.get("evidence_collectors"), list) else [],
        "audit_axes": row.get("audit_axes") if isinstance(row.get("audit_axes"), list) else [],
        "lens_contracts": row.get("lens_contracts") if isinstance(row.get("lens_contracts"), dict) else {},
        "risk": row.get("risk", "medium"),
        "examples": row.get("examples") if isinstance(row.get("examples"), list) else [],
        "verified_count": int(row.get("verified_count") or 0),
    }


def _merge_list(a, b) -> list:
    out = []
    for x in (a or []) + (b or []):
        if x and x not in out:
            out.append(x)
    return out


def _merge_contracts(a, b) -> dict:
    out = dict(a or {})
    for axis, contract in (b or {}).items():
        cur = out.setdefault(axis, {})
        if isinstance(contract, dict):
            for k, v in contract.items():
                if isinstance(v, list):
                    cur[k] = _merge_list(cur.get(k), v)
                elif v and k not in cur:
                    cur[k] = v
    return out


def _contracts_for(task_type: str, channel: str) -> dict:
    if task_type == "message_send" and channel == "kakaotalk":
        return {
            "state_robustness": {
                "must_check": [
                    "recipient is verified from the current chat/search result before typing or sending",
                    "current open chat is not assumed to be the requested recipient",
                    "existing draft text is detected and handled before replacing or sending",
                    "ambiguous recipient names trigger ask instead of guessing",
                ],
                "not_sufficient": [
                    "KakaoTalk app is merely open",
                    "some chat window is open",
                    "text was typed without proving the recipient",
                ],
            },
            "intent_sufficiency": {
                "must_check": [
                    "requested message content is prepared for the requested recipient",
                    "the task is not complete until the message is sent or user confirmation blocks the commit",
                ],
                "not_sufficient": [
                    "opening a chat without sending or stopping at the commit gate",
                    "typing message text only into an input box",
                ],
            },
            "evidence_adequacy": {
                "must_check": [
                    "after send, requested text appears as an outgoing bubble or equivalent sent-message evidence",
                    "input field is empty or no unsent draft remains",
                    "commit action has before/after observation in trace",
                ],
                "not_sufficient": [
                    "message text visible only in the input field",
                    "worker self-reported done without post-send evidence",
                ],
            },
        }
    if task_type == "research_and_summarize":
        return {
            "intent_sufficiency": {
                "must_check": [
                    "multiple relevant review sources are inspected, not merely searched",
                    "actual review content, transcript, captions, or observable claims are gathered",
                    "a written summary is produced for the user",
                    "summary distinguishes observed evidence from inference",
                ],
                "not_sufficient": [
                    "only opening YouTube",
                    "only showing search results",
                    "opening one video without extracting review content",
                    "claiming done without a written summary",
                ],
            },
            "evidence_adequacy": {
                "must_check": [
                    "trace or screen evidence shows at least several distinct sources/results inspected",
                    "final answer includes common themes, pros/cons, or repeated observations",
                ],
                "not_sufficient": [
                    "a single visible video page",
                    "no trace of source inspection",
                    "no summary text",
                ],
            },
        }
    if task_type == "file_artifact_create_or_edit":
        return {
            "state_robustness": {
                "must_check": ["existing file/path state is handled instead of assumed absent"],
                "not_sufficient": ["only opening an editor"],
            },
            "intent_sufficiency": {
                "must_check": ["requested file artifact exists or requested edit is applied"],
                "not_sufficient": ["typing text without saving"],
            },
            "evidence_adequacy": {
                "must_check": ["filesystem evidence proves creation or modification"],
                "not_sufficient": ["editor window contains text but file existence is unverified"],
            },
        }
    if task_type == "navigation_route":
        return {
            "state_robustness": {
                "must_check": [
                    "origin is current location or device coordinates when the user says here/current location",
                    "requested destination is parsed and verified before route search",
                    "map app route UI is used instead of a generic web/search answer",
                    "icon-only controls may require screenshot/grounding when AX labels are insufficient",
                ],
                "not_sufficient": [
                    "asking the user for current location while device/location context is available",
                    "only opening a map app",
                    "only showing a destination search result without route mode",
                ],
            },
            "intent_sufficiency": {
                "must_check": [
                    "route results for the requested destination are visible",
                    "the result includes route options, travel time, or directions information",
                    "origin and destination are both represented in the route panel or equivalent UI",
                ],
                "not_sufficient": [
                    "a map pin or place page is visible but no route is calculated",
                    "destination text was searched but route results are absent",
                    "only reporting that a map should be used",
                ],
            },
            "evidence_adequacy": {
                "must_check": [
                    "screen or trace evidence shows the map route UI after route calculation",
                    "evidence includes the requested destination text or a clearly matching route endpoint",
                    "device/current-location use is recorded in trace or visible in UI",
                ],
                "not_sufficient": [
                    "worker self-reported done without route-screen evidence",
                    "OCR evidence comes only from terminal logs or prompt text",
                ],
            },
        }
    if task_type == "search_and_report":
        return {
            "intent_sufficiency": {
                "must_check": ["answer is reported to the user, not only searched"],
                "not_sufficient": ["search results page is open but no answer is provided"],
            },
            "evidence_adequacy": {
                "must_check": ["screen/DOM/source evidence supports the reported answer"],
                "not_sufficient": ["unsupported assertion without visible/source evidence"],
            },
        }
    return {
        "intent_sufficiency": {
            "must_check": ["final state satisfies the user's requested outcome"],
            "not_sufficient": ["only intermediate navigation or setup is complete"],
        },
        "evidence_adequacy": {
            "must_check": ["there is observable evidence for success"],
            "not_sufficient": ["worker self-reports done without evidence"],
        },
    }


def _global_not_sufficient(spec: TaskSpec) -> list[str]:
    items = ["worker self-report alone"]
    for contract in (spec.lens_contracts or {}).values():
        if isinstance(contract, dict):
            items.extend(contract.get("not_sufficient") or [])
    return _merge_list([], items)


def _looks_destructive(low: str) -> bool:
    """비가역 파괴(삭제/제거/덮어쓰기 등) 의도인가 → 결정론 커밋게이트 대상."""
    return any(w in low for w in (
        "삭제", "지워", "지운다", "지우고", "지울", "제거", "휴지통", "비운다", "버린다",
        "영구 삭제", "덮어쓰", "포맷",
        "delete", "remove", "erase", "trash", "wipe", "purge", "overwrite", "rm -",
    ))


def _looks_like_kakao_message(low: str) -> bool:
    return any(w in low for w in ("카톡", "카카오톡", "kakao", "kakaotalk")) and any(
        w in low for w in ("보내", "메시지", "물어", "전해", "말해")
    )


def _looks_like_research_summary(low: str) -> bool:
    return any(w in low for w in ("요약", "summary", "정리")) and any(
        w in low for w in ("리뷰", "review", "실사용", "후기", "유튜브", "youtube", "여러")
    )


def _looks_like_navigation_route(text: str) -> bool:
    low = (text or "").lower()
    origin_hint = any(k in text for k in ("여기서", "현재 위치", "내 위치", "지금 위치", "현위치")) \
        or any(k in low for k in ("from here", "current location", "my location"))
    route_hint = any(k in text for k in ("까지", "가는 법", "어케가", "어떻게 가", "경로", "길찾기", "네비", "내비")) \
        or any(k in low for k in ("route", "directions", "navigate", "navigation"))
    if origin_hint and route_hint:
        return True
    return any(k in text for k in ("길찾기", "경로 알려", "가는 길", "가는 법", "어케가", "어떻게 가")) \
        or any(k in low for k in ("directions to", "route to", "navigate to"))


def _extract_destination(goal: str) -> str:
    text = (goal or "").strip()
    patterns = [
        r"(?:여기서|현재\s*위치에서|내\s*위치에서|지금\s*위치에서|현위치에서)\s*(.+?)(?:까지|까지는|까지\s|$)",
        r"(.+?)(?:까지)\s*(?:어케가|어떻게\s*가|가는\s*법|가는\s*길|경로|길찾기|가려면|갈\s*수|$)",
        r"(.+?)\s*(?:길찾기|가는\s*법|가는\s*길|경로)\s*$",
        r"(?:길찾기|경로)\s*[:：]?\s*(.+)",
        r"(?:directions|route|navigate)\s+(?:to\s+)?(.+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            cleaned = _clean_destination(m.group(1))
            if cleaned:
                return cleaned
    return ""


def _clean_destination(s: str) -> str:
    out = str(s or "").strip()
    out = re.sub(r"^(?:여기서|현재\s*위치에서|내\s*위치에서|지금\s*위치에서|현위치에서)\s*", "", out)
    out = re.sub(r"(?:까지|어케가|어떻게\s*가|가는\s*법|가는\s*길|경로|길찾기|가려면|알려줘|찾아줘)\s*$", "", out)
    out = re.sub(r"\s+", " ", out)
    return out.strip(" .'\"")


def _extract_recipient(goal: str) -> str:
    m = re.search(r"(.+?)(?:에게|한테)\s+", goal)
    if m:
        return _clean_entity(m.group(1))
    m = re.search(r"카톡으로\s+(.+?)(?:에게|한테)", goal)
    if m:
        return _clean_entity(m.group(1))
    return ""


def _extract_message(goal: str, recipient: str = "") -> str:
    quoted = re.search(r"['\"]([^'\"]+)['\"]", goal)
    if quoted:
        return quoted.group(1).strip()
    text = goal
    if recipient:
        text = re.sub(re.escape(recipient) + r"\s*(?:에게|한테)\s*", "", text, count=1)
    # "카톡으로" 앞뒤의 명령어 꼬리를 제거한다.
    text = re.sub(r"카카오톡으로|카톡으로|kakaotalk으로", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:라고|이라고)?\s*(?:보내줘|보내|물어봐|전해줘|말해줘)\s*$", "", text)
    text = text.strip(" .'\"")
    return text


def _extract_artifact(goal: str) -> str:
    m = re.search(r"([0-9A-Za-z가-힣_.-]+\.(?:txt|md|csv|json|py|html|css|js))", goal)
    return m.group(1) if m else ""


def _clean_entity(s: str) -> str:
    s = re.sub(r"^(?:카톡으로|카카오톡으로)\s*", "", s.strip())
    return s.strip(" .'\"")
