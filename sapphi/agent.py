"""Sapphi 에이전트 루프 — perceive → decide → (gate) → act."""

from __future__ import annotations

import glob
import json
import os
import time
from dataclasses import dataclass, field, asdict

from . import perceive, brain, act, safety, inputlock, statuslight, state_snapshot
from .models import Action, ToolResult, CommitRecord

# ★클릭류: 실행 후 *반드시 화면을 다시 봐* 효과를 확인(헛클릭이 다음 동작으로 연쇄되는 참사 방지).
CLICK_ACTIONS = {"smart_click", "ax_click", "ocr_click", "ground_click", "click", "double_click", "right_click"}


@dataclass
class StepLog:
    action: Action
    gate: str           # allow | stop | confirm
    executed: bool
    note: str = ""
    result: ToolResult | None = None
    risk_level: str = ""
    risk_reason: str = ""


@dataclass
class RunResult:
    objective: str
    mode: str
    steps: list[StepLog] = field(default_factory=list)
    stopped_at_commit: bool = False     # 리허설이 비가역 직전 멈췄나
    pending_commit: Action | None = None  # 사람 승인 대기 중인 커밋 행동
    commits: list[CommitRecord] = field(default_factory=list)
    plan_preview: list[str] = field(default_factory=list)
    postcondition_checks: list[dict] = field(default_factory=list)
    observation_checks: list[dict] = field(default_factory=list)
    state_snapshots: list[dict] = field(default_factory=list)
    perception_stats: dict = field(default_factory=dict)
    clarification: str | None = None    # 'ask' — 사용자에게 되물은 질문
    done: bool = False
    error: str | None = None
    trace_path: str | None = None
    final_isolate_app: str | None = None   # 워커가 *실제로 일한* 앱(지시 앱이 아니라 스스로 고른 것) — RUBI 검증 범위용


def run(objective: str, mode: str = "rehearse", max_steps: int = 15,
        model: str = brain.DEFAULT_MODEL, out_dir: str = "sapphi_out",
        confirm_fn=None, ask_fn=None, verbose: bool = False,
        isolate_app: str | None = None, lock_input: bool = False,
        one_step: bool = False, verify_clicks: bool = True, preview_fn=None) -> RunResult:
    """목표를 수행한다.

    mode:
      plan     — 실제 제어 0. 현재 화면 기준 '다음 행동'만 산출(부작용 없음).
      rehearse — 가역 행동만 실행, 비가역 커밋 직전 정지(기본 안전값).
      live     — 전부 실행하되 비가역 행동은 confirm_fn 으로 사람 확인.
    confirm_fn(action)->bool : live 모드에서 커밋 승인 콜백(없으면 거부).
    ask_fn(question)->str    : 'ask'(되물음)에 대한 사용자 답변 콜백(채팅형). 없으면 되물음에서 종료.
    preview_fn(plan)->bool   : 첫 실행 전 계획 미리보기 승인 콜백(없으면 표시만/자동 진행).
    """
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, "step_*.png")):  # 묵은 스샷 제거(검증 오염 방지)
        try:
            os.remove(old)
        except OSError:
            pass
    try:
        os.remove(os.path.join(out_dir, "state_snapshots.json"))  # 이번 런의 상태 후보만 유지
    except OSError:
        pass
    res = RunResult(objective=objective, mode=mode)
    res.perception_stats = _new_perception_stats()
    history: list[Action] = []
    clarifications: list[tuple[str, str]] = []
    tool_outputs: list[tuple[str, str]] = []
    recent_sigs: list[str] = []
    pending_postconditions: list[dict] = []
    pending_observation_checks: list[dict] = []
    state_revision = 0                       # 실제 조작 성공 때마다 증가 — 스냅샷 stale 경계
    violation_counts: dict[str, int] = {}   # 시그니처 → post 위반 누적 횟수(누적 BLOCK_AT 회 차단)
    _BLOCK_AT = 2                            # 같은 행동이 이만큼 *반복* 위반하면 실행 차단
    refocus_fails = 0                        # 대상앱 전면화 연속 실패 횟수(2회면 best-effort 진행 — 무한보류 방지)
    expected_front: str | None = None  # ★frontmost 가드 기준선(스샷 시점의 맨앞 앱)
    # ★세션 재사용: 이 런의 모든 brain 호출이 한 claude 세션을 공유 → 유상태(이전 화면·행동 기억) + 캐시 재사용.
    #   매 스텝 거대한 시스템 프롬프트를 다시 안 보내 *지연·비용↓*. 끊기면 brain 이 새 세션으로 재수립.
    #   SAPPHI_SESSION=0 이면 비활성(기존 무상태 1회성 호출로 폴백).
    import uuid as _uuid
    from rubi.provider import provider_name
    _session_on = os.environ.get("SAPPHI_SESSION", "1") != "0" and provider_name() != "openai"
    brain_session: dict | None = (
        {"id": str(_uuid.uuid4()), "started": False} if _session_on else None
    )   # ★openai provider 는 claude 세션이 없어 delta(시스템지침 없음) 프롬프트가 깨진다 → 세션 비활성
    # ★격리 대상 앱이 있으면 런 시작에 *미리 띄운다* — 숨겨진(ESC로) 창이면 첫 캡처가 검정→'잠김' 환각.
    #   open 으로 창을 복구·전면화해 두면 첫 스샷부터 그 앱이 보이고, 클릭도 frontmost 라 정확히 꽂힌다.
    if isolate_app:
        perceive._restore_app_window(isolate_app)
        time.sleep(1.0)
    statuslight.spawn_overlay()        # 🔴조작중/🟢생각중 상태등(사용자에게 '만져도 되는가' 표시)
    device_signals = ""   # (VISIO 정리: Jarvis 기기신호(위치·배터리) 주입 제거 — 워커는 화면/AX로만 판단)

    next_shot: str | None = None     # zoom 요청 시 다음 관찰에 쓸 확대본
    # ★isolate_app 이면 클릭 OCR도 맨앞 창 안으로 격리(다른 창의 같은 글자 헛클릭 방지 — 채팅창 '도착' 등).
    _prev_iso_ocr = act._ISOLATE_OCR
    if isolate_app:
        act._ISOLATE_OCR = True
    # ★입력 잠금(옵션): 실행 내내 *사용자 입력 차단*(오염 방지). 우리 합성입력만 통과. 비상탈출=ESC, watchdog 자동해제.
    _lock = (inputlock.InputLock(max_seconds=max(120, max_steps * 45), verbose=verbose)
             if (lock_input and inputlock.available()) else None)
    if _lock is not None:
        _lock.__enter__()
        if verbose:
            print("      🔒 입력 잠금 ON" + ("" if _lock.engaged else " (실패-권한? 잠금없이 진행)")
                  + ("  ·비상탈출=ESC" if _lock.engaged else ""))
    try:
        for i in range(max_steps):   # max_steps = 최대 '스크린샷(묶음)' 수 — 묶음마다 행동 여러 개
            if _lock is not None and _lock.aborted:   # 사용자가 ESC로 비상탈출
                res.error = "사용자 ESC 비상탈출 — 중단"
                break
            statuslight.set_phase("thinking")   # 🟢 관찰+생각 — 사용자가 맥 써도 됨(frontmost 가드가 지킴)
            zoom_observation = False
            if next_shot:            # 직전 zoom → 확대본을 보고 판단(새 캡처 대신)
                shot = next_shot
                next_shot = None
                zoom_observation = True
            else:
                shot = os.path.join(out_dir, f"step_{i:02d}.png")
                perceive.screenshot(shot, app=isolate_app)   # isolate_app 지정 시 그 앱 창만(클러터 배제)
                expected_front = perceive.frontmost_app()    # ★가드 기준선: 이 화면의 맨앞 앱
            current_snapshot = None
            snapshot_text = "(확대 관찰이라 구조화 스냅샷 생략 — 확대 이미지는 원본 화면 좌표와 다를 수 있음)"
            if not zoom_observation:
                snap = state_snapshot.capture(
                    shot,
                    out_dir,
                    i,
                    state_revision,
                    isolate_app=isolate_app,
                    expected_front=expected_front,
                )
                res.state_snapshots.append(snap.to_dict())
                current_snapshot = snap
                snapshot_text = snap.prompt_text()
                if verbose:
                    axn = len(snap.ax_candidates)
                    txn = len(snap.text_items)
                    print(f"      🧭 상태 스냅샷 {snap.snapshot_id}: AX {axn} · OCR {txn}", flush=True)
            if verbose:
                print("      🧠 …화면 보고 생각 중", flush=True)
            if pending_observation_checks:
                for chk in pending_observation_checks:
                    diag = _effect_diagnosis(chk, shot)
                    res.observation_checks.append(diag)
                    tool_outputs.append(("관측 진단", _effect_prompt(diag)))
                    if verbose:
                        print(f"      🩺 관측 진단: {diag.get('verdict')} · 변화율 {diag.get('changed_ratio', 0):.3f}")
                pending_observation_checks = []
            postcondition_prompts = [_postcondition_prompt(p) for p in pending_postconditions]
            if verbose and postcondition_prompts:
                print(f"      🧷 postcondition {len(postcondition_prompts)}개 확인 중", flush=True)
            # 기본: '결과를 봐야 아는 지점까지' 묶음(스샷 절약). one_step: 한 동작만(헛클릭 연쇄 방지).
            plan = brain.next_plan(objective, history, shot, model, clarifications, tool_outputs,
                                   one_step=one_step,
                                   postcondition_checks=postcondition_prompts,
                                   state_snapshot=snapshot_text,
                                   device_signals=device_signals,
                                   session=brain_session)
            if pending_postconditions:
                # ★post_met(기계가독): 두뇌가 plan 첫 행동에 실어준 판정을 구조적으로 기록하고,
                #   위반이면 그 행동의 시그니처를 *재시도 금지*로 등록(모델 선의에만 의존하지 않는 집행).
                pm = plan[0].post_met if plan else None
                status = {True: "met", False: "violated"}.get(pm, "presented_to_brain")
                for p in pending_postconditions:
                    p["checked_on_screenshot"] = shot
                    p["status"] = status
                    if pm is False:
                        sig = p.get("signature", "")
                        violation_counts[sig] = violation_counts.get(sig, 0) + 1
                        p["violation_count"] = violation_counts[sig]
                        tool_outputs.append((p["after_action"],
                                             f"postcondition 위반(누적 {violation_counts[sig]}회): "
                                             f"'{p['expected'][:50]}' 미충족 — 같은 행동 말고 다른 접근"))
                if verbose and pm is False:
                    print(f"      🧷 postcondition 위반(누적 {pending_postconditions[0].get('violation_count')}회) — "
                          f"{pending_postconditions[0]['after_action'][:45]}")
                res.postcondition_checks.extend(pending_postconditions)
                pending_postconditions = []
            if not plan:
                break
            if verbose and plan[0].thought:
                print(f"      🧠 {plan[0].thought}")

            if not res.plan_preview:
                res.plan_preview = [_preview_line(n, a) for n, a in enumerate(plan, 1)]
                if verbose:
                    print("      📋 계획 미리보기")
                    for line in res.plan_preview:
                        print(f"        {line}")
                if preview_fn is not None and not preview_fn(plan):
                    res.error = "사용자가 계획을 취소"
                    res.steps.append(StepLog(Action(action="abort", thought=res.error),
                                             safety.ALLOW, False, res.error,
                                             ToolResult("skipped", "preview", res.error)))
                    break

            # 무한반복 차단: 같은 묶음(첫 행동 기준)이 3번 연속이면 중단.
            sig = plan[0].signature()
            recent_sigs.append(sig)
            if plan[0].action not in ("ask", "done", "abort") and recent_sigs[-3:] == [sig, sig, sig]:
                res.error = f"같은 행동 3회 반복(진전 없음): {plan[0].short()} — 방법이 안 먹힘"
                res.steps.append(StepLog(plan[0], safety.ALLOW, False, "무한반복 차단"))
                break

            stop_run = False   # done/abort/ask-종료/게이팅 정지 → 실행 자체 종료
            reobserve = False  # zoom → 확대본으로 재관찰
            statuslight.set_phase("acting")   # 🔴 지금부터 실제 조작 — 사용자 손대면 안 됨
            state_snapshot.set_active(current_snapshot)
            for action in plan:
                a = action.action
                if a == "zoom":   # 확대해서 다시 보기(나쁜 눈 보정) — 화면 부작용 없음
                    next_shot = perceive.zoom_crop(shot, action.x, action.y, out_dir, i)
                    if verbose:
                        print(f"      🔍 ({action.x},{action.y}) 확대해서 다시 봄")
                    res.steps.append(StepLog(action, safety.ALLOW, False, f"🔍확대 ({action.x},{action.y})",
                                             ToolResult("skipped", "zoom", next_shot or "")))
                    reobserve = True
                    break
                if a == "done":
                    res.done = True
                    res.steps.append(StepLog(action, safety.ALLOW, False, "목표 완료 판단",
                                             ToolResult("skipped", "done", action.thought)))
                    stop_run = True
                    break
                if a == "abort":
                    res.error = action.thought or "중단(abort)"
                    res.steps.append(StepLog(action, safety.ALLOW, False, f"중단: {res.error}",
                                             ToolResult("skipped", "abort", res.error)))
                    stop_run = True
                    break
                if a == "ask":
                    question = action.text or action.thought
                    if ask_fn is not None:
                        answer = ask_fn(question)
                        clarifications.append((question, answer))
                        res.steps.append(StepLog(action, safety.ALLOW, False, f"되물음→답변: {answer}",
                                                 ToolResult("skipped", "ask", question)))
                        break   # 묶음 중단 → 재관찰(다음 스샷)
                    res.clarification = question
                    res.steps.append(StepLog(action, safety.ALLOW, False, f"되물음: {question}",
                                             ToolResult("skipped", "ask", question)))
                    stop_run = True
                    break
                if mode == "plan":
                    res.steps.append(StepLog(action, "plan", False, "plan 모드 — 실행 안 함",
                                             ToolResult("skipped", "plan", action.short())))
                    stop_run = True
                    break

                # ★frontmost 가드: 두뇌 '생각'(🟢) 동안 사용자가 창을 바꿨으면, 이 행동은 *옛 화면 기준*이라
                #   사용자 창에 꽂힌다(토스증권 사고 패턴). 실행 전 *항상 대상 앱을 전면화*해 그 창에서만 동작하게 한다.
                if action.kind() in ("screen", "text"):
                    if isolate_app:
                        # ★능동 재포커스: 매 조작 직전 대상 앱을 앞으로 가져온다 → 사용자가 딴짓하다 와도,
                        #   포커스를 잃었어도 사피가 *자동으로 그 탭/창으로 전환*하고 동작한다(사용자 안심).
                        perceive.focus_app(isolate_app)
                        time.sleep(0.35)
                        if not perceive.is_frontmost(isolate_app):
                            refocus_fails += 1
                            if refocus_fails < 2:
                                # 1회: 안전 보류·재관찰(모달·Spotlight가 포커스 가로챔 등 일시적일 수 있음)
                                if verbose:
                                    print(f"      🛡 '{isolate_app}' 전면화 실패 — 실행 보류, 재관찰")
                                res.steps.append(StepLog(action, safety.ALLOW, False, "대상앱 전면화 실패 — 실행 보류",
                                                         ToolResult("blocked", "front_guard",
                                                                    f"'{isolate_app}'를 앞으로 못 올림 — 재관찰",
                                                                    target_label=action.target_label)))
                                reobserve = True
                                break
                            # 2회 연속: 슬러그 미해석·오버레이 고착 등 → *무한보류 방지*로 best-effort 진행.
                            if verbose:
                                print(f"      ⚠️ '{isolate_app}' 전면화 거듭 실패 — best-effort로 진행")
                        refocus_fails = 0
                        expected_front = perceive.frontmost_app() or expected_front
                    elif expected_front:
                        # 격리 대상이 없는 일반 작업: 무엇을 전면화할지 모르므로 *변경 감지 시 보류*(기존 안전 동작).
                        now_front = perceive.frontmost_app()
                        if now_front and now_front != expected_front:
                            if verbose:
                                print(f"      🛡 맨앞 앱 변경 감지({expected_front}→{now_front}) — 실행 보류, 재관찰")
                            res.steps.append(StepLog(action, safety.ALLOW, False, "frontmost 변경 — 실행 보류",
                                                     ToolResult("blocked", "front_guard",
                                                                f"화면 기준이 달라짐({expected_front}→{now_front}) — 재관찰",
                                                                target_label=action.target_label)))
                            reobserve = True
                            break

                # ★post 위반 집행: 같은 행동이 *반복*(누적 _BLOCK_AT회) 위반하면 차단. 단 activate/open/wait
                #   같은 *멱등 복구 동작*은 환경이 바뀌면 통할 수 있으니 영구차단하지 않는다(손발 묶기 방지).
                if violation_counts.get(action.signature(), 0) >= _BLOCK_AT and not _is_recovery_action(action):
                    if verbose:
                        print(f"      🚫 post 위반 {_BLOCK_AT}회 반복 — 실행 차단: {action.short()[:55]}")
                    res.steps.append(StepLog(action, safety.ALLOW, False, "post 반복위반 — 차단",
                                             ToolResult("blocked", "post_guard",
                                                        f"이 행동은 {_BLOCK_AT}회 연속 postcondition 위반 — 다른 접근 필요",
                                                        target_label=action.target_label)))
                    tool_outputs.append((action.target_label or action.action,
                                         f"차단됨: {_BLOCK_AT}회 반복 위반 행동 — 근본적으로 다른 접근을 선택하라"))
                    reobserve = True
                    break

                risk = safety.decide(action, mode)
                decision = risk.gate
                if decision == safety.STOP:
                    res.stopped_at_commit = True
                    res.pending_commit = action
                    res.commits.append(CommitRecord(action=action, state="pending",
                                                    note="rehearse stopped before commit"))
                    res.steps.append(StepLog(action, decision, False,
                                             f"리허설 정지: 비가역 커밋 직전 — {risk.reason}",
                                             ToolResult("skipped", "commit_gate", "pending user approval",
                                                        target_label=action.target_label),
                                             risk.level, risk.reason))
                    stop_run = True
                    break
                if decision == safety.CONFIRM:
                    # ★승인 대기: 이 구간만은 사용자가 *터미널에 y/n 입력*해야 한다(맥 조작 정상).
                    #   🔴'손대지 마' 대신 🟡'입력하세요'로 바꿔 모순(손대지 말랬는데 승인하라)을 없앤다.
                    statuslight.set_phase("awaiting")
                    approved = bool(confirm_fn and confirm_fn(action))
                    statuslight.set_phase("acting")
                    if not approved:
                        res.pending_commit = action
                        res.commits.append(CommitRecord(action=action, state="blocked",
                                                        note="user denied or deferred commit"))
                        res.steps.append(StepLog(action, decision, False, "사람이 커밋 거부/보류",
                                                 ToolResult("blocked", "commit_gate", "user denied/deferred",
                                                            target_label=action.target_label),
                                                 risk.level, risk.reason))
                        stop_run = True
                        break
                    # ★★핵심 버그 수정: 방금 사용자가 터미널에 'y'를 치느라 *맨앞 앱이 터미널*이 됐다.
                    #   이 상태로 key/type/click 을 쏘면 키스트로크가 *터미널로 새서* 타깃 앱엔 안 들어간다
                    #   (실측: 카톡 Enter 전송이 안 먹힌 진짜 원인 — 더블클릭/좌표가 아니라 *포커스 탈취*).
                    #   → 승인된 커밋 실행 *직전*에 타깃 앱을 다시 전면화하고 가드 기준선도 그쪽으로 맞춘다.
                    if isolate_app and action.kind() in ("screen", "text"):
                        perceive._restore_app_window(isolate_app)
                        time.sleep(0.8)
                        expected_front = perceive.frontmost_app() or expected_front
                    commit = CommitRecord(action=action, state="user_confirmed",
                                          note="user confirmed irreversible action")
                    out = act.execute(action)
                    commit.tool_result = out
                    commit.state = "executed" if out.ok else "blocked"
                    _record_perception_result(res, action, out)
                    if out.ok:
                        state_revision += 1
                        state_snapshot.clear_active()
                    res.steps.append(StepLog(action, decision, out.ok, f"사람 승인 후 커밋 실행 — {risk.reason}",
                                             out, risk.level, risk.reason))
                    history.append(action)
                    if out:
                        tool_outputs.append((action.command or action.target_label or a, out.summary()))
                        time.sleep(1.2)
                    expected_front = perceive.frontmost_app() or expected_front   # 가드 기준선 갱신
                    # 커밋은 실행 뒤 반드시 관찰 로그를 남긴다. 최종 VLM 검증은 RUBI verify/autoloop 가 담당한다.
                    if out.ok:
                        obs_path = os.path.join(out_dir, f"commit_{len(res.commits):02d}.png")
                        perceive.screenshot(obs_path, app=isolate_app)
                        commit.observation_path = obs_path
                        commit.state = "observed"
                        commit.verified = False
                        commit.note = "observed after commit; external verifier still required"
                    res.commits.append(commit)
                    if out.ok and action.postcondition:
                        if action.action in CLICK_ACTIONS:
                            pending_observation_checks.append(_pending_effect_check(action, out, shot))
                        pending_postconditions.append(_pending_postcondition(action, out))
                        if verbose:
                            print("        ↳ (커밋 postcondition 있음 → 다음 화면에서 효과 확인)")
                        reobserve = True
                        break
                    continue

                # ALLOW
                if verbose:
                    print(f"      ▶ {action.short()}", flush=True)
                out = act.execute(action)
                if out:
                    _record_perception_result(res, action, out)
                if out and out.ok:
                    state_revision += 1
                    state_snapshot.clear_active()
                if out:
                    tool_outputs.append((action.command or action.target_label or a, out.summary()))
                    if verbose and out.evidence not in ("(출력 없음)",):
                        print(f"        ↳ {out.summary(80)}")
                    res.steps.append(StepLog(action, decision, out.ok, f"{a}→ {out.summary(60)}", out,
                                             risk.level, risk.reason))
                    time.sleep(1.2)   # 앱 실행/클릭 후 화면 갱신 시간
                else:
                    res.steps.append(StepLog(action, decision, True, result=ToolResult("success", a, "executed")))
                history.append(action)
                # ★가드 기준선 갱신: 워커 *자신의* 행동(shell activate 등)이 맨앞 앱을 바꾼 건 정상이므로
                #   실행할 때마다 기준선을 현재로 재설정(사용자 개입만 가드에 걸리게).
                expected_front = perceive.frontmost_app() or expected_front
                # ★초점 따라가기: 워커가 *스스로 연/전환한 앱*으로 격리·재포커스 대상을 옮긴다.
                #   사용자가 "네이버 앱"이라 했어도 워커가 목표 위해 Safari를 골랐으면 그 선택을 따른다 —
                #   안 그러면 화면이 지시앱에 고정돼 워커가 *자기 작업을 못 보고* 헤맨다(실측 버그).
                if isolate_app and _is_app_switch(action):
                    nf = perceive.frontmost_app()
                    if nf and nf != isolate_app:
                        if verbose:
                            print(f"      🎯 작업 앱 전환 → 이제 '{nf}' 창에 집중(지시 '{isolate_app}' 해제)")
                        isolate_app = nf
                if action.postcondition and out and out.ok:
                    if a in CLICK_ACTIONS:
                        pending_observation_checks.append(_pending_effect_check(action, out, shot))
                    pending_postconditions.append(_pending_postcondition(action, out))
                    if verbose:
                        print("        ↳ (postcondition 있음 → 다음 화면에서 효과 확인)")
                    reobserve = True
                    break
                # ★클릭 후엔 *효과를 확인하러* 즉시 멈추고 재관찰 — 묶음에 뒤따르는 동작(타이핑 등)을
                #   *옛 화면 가정으로* 실행하지 않는다(헛클릭→허공타이핑→상태소실 참사 차단). '클릭됨'은
                #   클릭이 *발사*됐단 뜻일 뿐, *맞는 게 열렸단* 보장이 아니다 → 다음 스샷서 두뇌가 검증.
                if verify_clicks and a in CLICK_ACTIONS:
                    if out and out.ok:
                        pending_observation_checks.append(_pending_effect_check(action, out, shot))
                    if verbose:
                        print("        ↳ (클릭함 → 효과 확인 위해 재관찰)")
                    reobserve = True
                    break

            if reobserve:    # zoom/클릭후 → 즉시 재관찰(다음 스샷)
                continue
            if stop_run:
                break
        else:
            res.note = "max_steps 도달"
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    finally:
        statuslight.set_phase("idle")      # 상태등 끄기(⚪) — 죽어도 stale 보호가 2차 방어
        act._ISOLATE_OCR = _prev_iso_ocr   # 클릭 OCR 격리 복원
        if _lock is not None:
            _lock.release()
            if verbose:
                print("      🔓 입력 잠금 OFF" + (f" (삼킨 사용자입력 {_lock.suppressed}건)" if _lock.suppressed else ""))
        res.final_isolate_app = isolate_app   # 워커가 끝낸 앱(스스로 전환했을 수 있음) — RUBI 검증 범위에 전달
        state_snapshot.clear_active()
        res.trace_path = _write_trace(res, out_dir)
    return res


def _is_app_switch(action: Action) -> bool:
    """워커가 *앱을 열거나 전환*하는 행동인가 — open_app, 또는 shell `open ...`(URL/앱 열기)."""
    if action.action == "open_app":
        return True
    if action.action == "shell":
        return (action.command or "").strip().lower().startswith("open ")
    return False


def _is_recovery_action(action: Action) -> bool:
    """멱등 복구 동작 — 환경(다른 창/오버레이)이 바뀌면 통할 수 있어 post 위반으로 *영구차단하지 않는다*.
    activate/open(앱 전면화)·wait(대기)가 해당. 클릭·타이핑처럼 *상태를 바꾸는* 행동은 반복차단 대상."""
    if action.action == "wait":
        return True
    if action.action == "shell":
        cmd = (action.command or "").lower()
        return "activate" in cmd or cmd.lstrip().startswith("open ")
    return False


def _pending_postcondition(action: Action, out: ToolResult) -> dict:
    return {
        "after_action": action.short(),
        "signature": action.signature(),   # post 위반 시 *재시도 금지 등록*용(라벨 미세변형 반복 차단)
        "expected": action.postcondition,
        "tool_result": out.summary(),
        "status": "pending_next_observation",
    }


def _postcondition_prompt(check: dict) -> str:
    return (
        f"직전 행동 `{check.get('after_action', '')}` 실행 결과는 "
        f"`{check.get('tool_result', '')}`였다. 기대 postcondition: "
        f"`{check.get('expected', '')}`. 현재 화면에서 이 기대 효과가 실제로 생겼는지 먼저 판단하라."
    )


def _pending_effect_check(action: Action, out: ToolResult, before_shot: str) -> dict:
    return {
        "after_action": action.short(),
        "signature": action.signature(),
        "before_screenshot": before_shot,
        "tool_result": out.summary(),
        "status": "pending_next_observation",
    }


def _effect_diagnosis(check: dict, after_shot: str) -> dict:
    metrics = _screen_change_metrics(check.get("before_screenshot", ""), after_shot)
    ratio = float(metrics.get("changed_ratio") or 0.0)
    mean_delta = float(metrics.get("mean_delta") or 0.0)
    if not metrics.get("ok"):
        verdict = "unknown"
        hint = "화면 변화량을 계산하지 못했다. 현재 화면을 직접 보고 판단하라."
    elif ratio < 0.003 and mean_delta < 1.5:
        verdict = "no_visible_change"
        hint = (
            "직전 클릭 뒤 화면이 거의 변하지 않았다. 헛클릭, 비활성 요소, 이미 같은 상태, "
            "또는 더블클릭/우클릭/키보드 접근이 필요한 경우일 수 있다. 같은 클릭을 반복하지 말고 "
            "라벨을 바꾸거나 double_click/right_click/key/scroll 등 다른 접근을 고려하라."
        )
    elif ratio < 0.025 and mean_delta < 5:
        verdict = "tiny_change"
        hint = (
            "직전 클릭 뒤 작은 변화만 감지됐다. 포커스/커서/선택 표시만 바뀌었을 수 있다. "
            "목표 패널이나 결과가 실제로 열렸는지 확인하고, 아니면 같은 행동 반복 대신 다른 접근을 택하라."
        )
    else:
        verdict = "visible_change"
        hint = "직전 클릭 뒤 화면 변화가 감지됐다. 새 상태를 기준으로 목표에 가까워졌는지 판단하라."
    return {
        "after_action": check.get("after_action", ""),
        "signature": check.get("signature", ""),
        "before_screenshot": check.get("before_screenshot", ""),
        "after_screenshot": after_shot,
        "tool_result": check.get("tool_result", ""),
        "verdict": verdict,
        "changed_ratio": ratio,
        "mean_delta": mean_delta,
        "hint": hint,
    }


def _effect_prompt(diag: dict) -> str:
    return (
        f"직전 행동 `{diag.get('after_action', '')}`의 도구 결과는 `{diag.get('tool_result', '')}`였다. "
        f"이전/현재 화면 저비용 diff 판정: {diag.get('verdict')} "
        f"(changed_ratio={diag.get('changed_ratio', 0):.3f}, mean_delta={diag.get('mean_delta', 0):.2f}). "
        f"{diag.get('hint', '')}"
    )


def _screen_change_metrics(before_path: str, after_path: str) -> dict:
    try:
        from PIL import Image, ImageChops, ImageStat

        with Image.open(before_path) as b0, Image.open(after_path) as a0:
            b = b0.convert("L").resize((96, 96))
            a = a0.convert("L").resize((96, 96))
        diff = ImageChops.difference(b, a)
        stat = ImageStat.Stat(diff)
        mean_delta = float(stat.mean[0])
        pixels = list(diff.getdata())
        changed = sum(1 for p in pixels if p >= 12)
        return {
            "ok": True,
            "changed_ratio": changed / max(1, len(pixels)),
            "mean_delta": mean_delta,
        }
    except Exception as e:
        return {"ok": False, "changed_ratio": 0.0, "mean_delta": 0.0, "error": f"{type(e).__name__}: {e}"}


def _preview_line(n: int, action: Action) -> str:
    decision = safety.classify(action)
    risk = f" · {decision.level.upper()}" if decision.level in {"high", "critical"} else ""
    target = action.target_label or action.text or action.command or ""
    target = f" — {target[:80]}" if target else ""
    return f"{n}. {action.kind()}:{action.action}{risk}{target}"


def _new_perception_stats() -> dict:
    return {
        "smart_click": {
            "total": 0,
            "snapshot_hit": 0,
            "ax_hit": 0,
            "ocr_hit": 0,
            "ground_hit": 0,
            "ambiguous": 0,
            "failed": 0,
            "other": 0,
        },
        "methods": {},
        "events": [],
    }


def _record_perception_result(res: RunResult, action: Action, out: ToolResult | None) -> None:
    """Count which perception tier actually handled GUI targeting."""
    if out is None:
        return
    stats = res.perception_stats or _new_perception_stats()
    res.perception_stats = stats
    method = out.method or action.action or "unknown"
    if action.action in CLICK_ACTIONS or method.startswith("smart/"):
        methods = stats.setdefault("methods", {})
        methods[method] = int(methods.get(method, 0)) + 1
        event = {
            "action": action.action,
            "target_label": action.target_label or out.target_label or action.text or "",
            "method": method,
            "status": out.status,
            "confidence": out.confidence,
        }
        if out.bbox:
            event["bbox"] = list(out.bbox)
        stats.setdefault("events", []).append(event)
        stats["events"] = stats["events"][-80:]
    if action.action != "smart_click" and not method.startswith("smart/"):
        return
    smart = stats.setdefault("smart_click", {})
    for key, default in _new_perception_stats()["smart_click"].items():
        smart.setdefault(key, default)
    smart["total"] = int(smart.get("total", 0)) + 1
    if out.status == "ambiguous":
        smart["ambiguous"] = int(smart.get("ambiguous", 0)) + 1
        return
    if not out.ok:
        smart["failed"] = int(smart.get("failed", 0)) + 1
        return
    tier = method.split("/", 1)[1] if method.startswith("smart/") and "/" in method else method
    key = f"{tier}_hit"
    if key in smart:
        smart[key] = int(smart.get(key, 0)) + 1
    else:
        smart["other"] = int(smart.get("other", 0)) + 1


def _write_trace(res: RunResult, out_dir: str) -> str | None:
    """SAPPHI/RUBI가 공유할 실행 artifact 를 남긴다."""
    path = os.path.join(out_dir, "run_trace.json")
    try:
        payload = {
            "objective": res.objective,
            "mode": res.mode,
            "plan_preview": res.plan_preview,
            "postcondition_checks": res.postcondition_checks,
            "observation_checks": res.observation_checks,
            "state_snapshots": res.state_snapshots,
            "perception_stats": res.perception_stats,
            "done": res.done,
            "error": res.error,
            "clarification": res.clarification,
            "stopped_at_commit": res.stopped_at_commit,
            "pending_commit": asdict(res.pending_commit) if res.pending_commit else None,
            "steps": [_step_dict(s) for s in res.steps],
            "commits": [_commit_dict(c) for c in res.commits],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


def _step_dict(step: StepLog) -> dict:
    return {
        "action": asdict(step.action),
        "gate": step.gate,
        "executed": step.executed,
        "note": step.note,
        "risk_level": step.risk_level,
        "risk_reason": step.risk_reason,
        "result": asdict(step.result) if step.result else None,
    }


def _commit_dict(commit: CommitRecord) -> dict:
    return {
        "action": asdict(commit.action),
        "state": commit.state,
        "tool_result": asdict(commit.tool_result) if commit.tool_result else None,
        "observation_path": commit.observation_path,
        "verified": commit.verified,
        "note": commit.note,
    }
