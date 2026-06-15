"""VISIO / SAPPHI-core / EMERI integration selfcheck.

No live GUI control, no LLM call. Verifies the runtime layers speak the same
structured language: TaskSpec → SAPPHI trace → RUBI verification record → EMERI,
plus VISIO's stimulus(triggers)/oracle(probes)/plan round-trip.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import types

from sapphi.agent import (
    RunResult,
    StepLog,
    _effect_diagnosis,
    _effect_prompt,
    _postcondition_prompt,
    _record_perception_result,
    _write_trace,
)
from sapphi.models import Action, ToolResult
from sapphi.perception_policy import preferred_tier_for_profile, should_try_ax_for_profile
from sapphi import act, state_snapshot, triggers, probes

from . import emeri, provider, records, taskspec, visio


def run_selfcheck() -> dict:
    tmp = tempfile.mkdtemp(prefix="rubi_selfcheck_")
    goal = "엄마한테 오늘 저녁메뉴 뭐냐고 카톡으로 물어봐"
    checks: list[tuple[str, bool, str]] = []

    spec = taskspec.classify_goal(goal, use_llm=False)

    # ── provider ──────────────────────────────────────────────────────────
    local_ok, local_why = provider.available("local")
    checks.append((
        "local_provider_available",
        isinstance(local_ok, bool) and isinstance(local_why, str) and bool(local_why),
        f"{local_ok}:{local_why}",
    ))

    # ── VISIO: 자극(triggers)/오라클(probes) 라우팅 ─────────────────────────
    checks.append((
        "triggers_can_produce",
        triggers.can_produce("notification") and triggers.can_produce("file")
        and not triggers.can_produce("battery_level"),
        "notification/file=직접, battery_level=픽스처요청",
    ))
    checks.append((
        "probes_can_probe",
        probes.can_probe("notes_contains") and probes.can_probe("clipboard_contains")
        and not probes.can_probe("nonexistent_kind"),
        "notes/clipboard=확인가능",
    ))

    # ── VISIO: 계획 저장/로드 round-trip (stimulus/fixture/oracle 보존) ──────
    tc = visio.TestCase(
        id="rt_case", title="roundtrip", goal="알림 저장",
        stimulus={"kind": "notification", "params": {"title": "t"}},
        fixture="native:notification",
        oracle={"kind": "notes_contains", "params": {"title": "📥 알림 정리", "from_injected": "body"}})
    plan_rt = visio.TestPlan("기능", "feat_key", [tc], "claude-opus-4-8", "claude-sonnet-4-6", visio._now())
    rt_path = visio.save_plan(plan_rt, tmp)
    loaded = visio.load_plan(rt_path)
    lc = loaded.cases[0] if loaded.cases else None
    checks.append((
        "visio_plan_roundtrip",
        lc is not None and lc.fixture == "native:notification"
        and lc.oracle.get("kind") == "notes_contains"
        and lc.stimulus.get("kind") == "notification",
        f"cases={len(loaded.cases)} oracle={lc.oracle.get('kind') if lc else '-'}",
    ))

    # ── SAPPHI 코어: target별 AX 정책 + 지각 캐시 ───────────────────────────
    structured_profile = {
        "app": "KakaoTalk", "mode": "structured", "ax_count": 30,
        "sample_labels": ["전송", "검색", "채팅", "프로필"],
    }
    checks.append((
        "target_level_ax_policy",
        should_try_ax_for_profile("전송", structured_profile)
        and not should_try_ax_for_profile("친구 탭 아이콘", structured_profile),
        "전송=AX, 친구 탭 아이콘=skip AX",
    ))
    cached_visual_profile = {
        "app": "KakaoTalk", "mode": "structured", "ax_count": 30,
        "sample_labels": ["검색", "채팅", "프로필"],
        "target_stats": {
            "친구탭아이콘": {
                "target": "친구 탭 아이콘",
                "success": {"ground": 2}, "failure": {"ax": 2, "ocr": 1},
                "last_success_method": "ground",
                "last_success_ts": time.time(), "last_ts": time.time(),
            }
        },
    }
    checks.append((
        "target_perception_policy_cache",
        preferred_tier_for_profile("친구 탭 아이콘", cached_visual_profile) == "ground"
        and not should_try_ax_for_profile("친구 탭 아이콘", cached_visual_profile),
        f"preferred={preferred_tier_for_profile('친구 탭 아이콘', cached_visual_profile)}",
    ))

    # ── SAPPHI 코어: state_snapshot affordance + smart_click ────────────────
    snap = state_snapshot.build_from_parts(
        screenshot_path=os.path.join(tmp, "synthetic.png"),
        step_index=0, revision=2, frontmost_app="KakaoTalk", isolate_app="KakaoTalk",
        ax_candidates=[
            {"role": "Button", "label": "전송", "cx": 800, "cy": 620, "w": 44, "h": 30},
            {"role": "Button", "label": "", "cx": 45, "cy": 90, "w": 32, "h": 32},
        ],
        text_items=[
            {"text": "엄마", "cx": 230, "cy": 170, "w": 38, "h": 18, "conf": 0.94},
            {"text": "오늘 저녁메뉴 뭐야?", "cx": 590, "cy": 610, "w": 150, "h": 20},
        ],
    )
    snap_prompt = snap.prompt_text()
    checks.append((
        "state_snapshot_affordance_map",
        "snapshot_id=snap_" in snap_prompt and "전송" in snap_prompt and "엄마" in snap_prompt and "무라벨" in snap_prompt,
        snap_prompt.splitlines()[0],
    ))
    pick = state_snapshot.find_click_target("전송")
    checks.append((
        "state_snapshot_inactive_safe",
        pick.get("status") == "none" and pick.get("reason") == "no_active_snapshot",
        str(pick),
    ))
    clicked: list[tuple[int, int]] = []
    old_pyautogui = sys.modules.get("pyautogui")
    sys.modules["pyautogui"] = types.SimpleNamespace(
        PAUSE=0.0, FAILSAFE=False, click=lambda x, y: clicked.append((int(x), int(y))))
    try:
        state_snapshot.set_active(snap)
        out = act._smart_click("전송")
    finally:
        state_snapshot.clear_active()
        if old_pyautogui is not None:
            sys.modules["pyautogui"] = old_pyautogui
        else:
            sys.modules.pop("pyautogui", None)
    checks.append((
        "smart_snapshot_click_path",
        out.ok and out.method == "smart/snapshot" and clicked == [(800, 620)],
        f"{out.summary()} clicked={clicked}",
    ))

    # ── TaskSpec ────────────────────────────────────────────────────────────
    checks.append((
        "taskspec_message_send",
        spec.task_type == "message_send" and spec.channel == "kakaotalk",
        f"{spec.task_type}:{spec.channel}",
    ))
    checks.append((
        "taskspec_lens_contracts",
        set(spec.audit_axes or []) == {"state_robustness", "intent_sufficiency", "evidence_adequacy"},
        ",".join(spec.audit_axes or []),
    ))
    saved_template = taskspec.save_template(tmp, spec, goal)
    reused = taskspec.find_template(tmp, taskspec.classify_goal("철수한테 카톡으로 10분 늦는다고 보내줘", use_llm=False))
    checks.append((
        "skill_template_reuse",
        saved_template and reused is not None and reused.key == "message_send:kakaotalk",
        reused.key if reused else "(none)",
    ))

    # ── SAPPHI trace → postcondition / perception stats ─────────────────────
    res = RunResult(objective=goal, mode="selfcheck")
    send_action = Action.from_dict({
        "action": "smart_click", "target_label": "전송", "risk": "irreversible", "commit": True,
        "postcondition": "message appears as outgoing bubble and input field is empty",
    })
    send_result = ToolResult(status="success", method="smart/ocr",
                             evidence="synthetic outgoing bubble evidence",
                             target_label="전송", confidence=0.9)
    res.steps.append(StepLog(send_action, gate="confirm", executed=True,
                             note="selfcheck synthetic commit", result=send_result,
                             risk_level="high", risk_reason="target label looks like external commit"))
    _record_perception_result(res, send_action, send_result)
    trace_path = _write_trace(res, tmp)
    trace = json.load(open(trace_path, encoding="utf-8")) if trace_path else {}
    checks.append((
        "sapphi_trace_postcondition",
        bool(trace_path) and trace["steps"][0]["action"].get("postcondition") == send_action.postcondition,
        trace_path or "(none)",
    ))
    smart_stats = (trace.get("perception_stats") or {}).get("smart_click") or {}
    checks.append((
        "perception_stats_trace",
        smart_stats.get("total") == 1 and smart_stats.get("ocr_hit") == 1,
        str(smart_stats),
    ))
    post_prompt = _postcondition_prompt({
        "after_action": send_action.short(),
        "expected": send_action.postcondition,
        "tool_result": "success · smart/ocr · synthetic outgoing bubble evidence",
    })
    checks.append((
        "sapphi_postcondition_prompt",
        "현재 화면" in post_prompt and "기대 postcondition" in post_prompt,
        post_prompt[:80],
    ))
    try:
        from PIL import Image
        before = os.path.join(tmp, "before.png")
        after = os.path.join(tmp, "after.png")
        Image.new("RGB", (80, 80), (20, 20, 20)).save(before)
        Image.new("RGB", (80, 80), (20, 20, 20)).save(after)
        diag = _effect_diagnosis({
            "after_action": "smart_click [검색]", "signature": "smart_click|검색",
            "before_screenshot": before, "tool_result": "success · smart/ocr",
        }, after)
        prompt = _effect_prompt(diag)
        checks.append((
            "sapphi_observation_diagnosis",
            diag.get("verdict") == "no_visible_change" and "같은 클릭을 반복하지 말고" in prompt,
            f"{diag.get('verdict')} ratio={diag.get('changed_ratio'):.3f}",
        ))
    except Exception as e:
        checks.append(("sapphi_observation_diagnosis", False, f"{type(e).__name__}: {e}"))

    # ── RUBI verification record + confidence ───────────────────────────────
    verdict = {
        "achieved": True, "evidence": "synthetic evidence",
        "oracle_evidence": [{"kind": "trace", "status": "success", "summary": "synthetic"}],
        "axis_results": [
            {"axis": "state_robustness", "verdict": "pass", "violated_clause": "", "evidence": "recipient verified"},
            {"axis": "intent_sufficiency", "verdict": "pass", "violated_clause": "", "evidence": "message sent"},
            {"axis": "evidence_adequacy", "verdict": "pass", "violated_clause": "", "evidence": "outgoing bubble"},
        ],
    }
    conf = records.confidence_from_verdict(verdict, achieved=True)
    negative_verdict = {
        "achieved": False, "evidence": "synthetic negative evidence",
        "oracle_evidence": [
            {"kind": "ocr", "status": "success", "summary": "screen text"},
            {"kind": "trace", "status": "success", "summary": "search loop"},
            {"kind": "goal_overlap", "status": "success", "summary": "missing requested chat"},
        ],
        "axis_results": [
            {"axis": "state_robustness", "verdict": "fail", "violated_clause": "wrong state"},
            {"axis": "intent_sufficiency", "verdict": "fail", "violated_clause": "wrong intent"},
            {"axis": "evidence_adequacy", "verdict": "fail", "violated_clause": "bad evidence"},
        ],
    }
    negative_conf = records.confidence_from_verdict(negative_verdict, achieved=False)
    checks.append(("rubi_negative_lesson_confidence", negative_conf >= 0.62, f"conf={negative_conf:.2f}"))
    records.save(tmp, records.VerificationRecord(
        goal=goal, task_key=spec.key(), achieved=True, rationale="synthetic pass",
        trace_path=trace_path or "", oracle_evidence=verdict["oracle_evidence"],
        axis_results=verdict["axis_results"], confidence=conf))
    loaded_records = records.load(tmp)
    checks.append((
        "rubi_verification_record",
        len(loaded_records) == 1 and loaded_records[0].get("axis_results"),
        f"records={len(loaded_records)} conf={conf:.2f}",
    ))

    # ── EMERI 회귀 기억 ──────────────────────────────────────────────────────
    emeri.save_lesson(
        tmp, goal,
        when="카톡 입력창에 초안이 이미 있음",
        then="초안을 사용자 확인 없이 덮어쓰거나 전송하지 말 것",
        works=False, task_key=spec.key(), axis="state_robustness",
        contract_clause="existing draft text is detected and handled",
        confidence=conf, fail_reason="existing draft can be destroyed or accidentally sent",
        alternative="ask user before clearing the draft")
    recalled = emeri.recall_text(tmp, "철수한테 카톡으로 10분 늦는다고 보내줘", task_key=spec.key())
    checks.append((
        "emeri_axis_recall",
        "state_robustness" in recalled and "초안" in recalled,
        recalled.splitlines()[-1] if recalled else "(none)",
    ))

    failed = [{"name": n, "detail": d} for n, ok, d in checks if not ok]
    return {
        "ok": not failed,
        "tmp": tmp,
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
        "failed": failed,
    }


def main() -> int:
    result = run_selfcheck()
    for check in result["checks"]:
        mark = "PASS" if check["ok"] else "FAIL"
        print(f"[{mark}] {check['name']} — {check['detail']}")
    print(f"artifact_dir: {result['tmp']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
