"""State snapshot cache for SAPPHI observations.

This layer is intentionally cheap: it stores structured affordances already
available from AX/OCR so later decisions can reuse "what was on this screen"
without turning every small question into another vision-grounding call.
"""

from __future__ import annotations

import hashlib
import difflib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

_INDEX_FILE = "state_snapshots.json"
_MAX_AX = 60
_MAX_TEXT = 80
_MAX_PROMPT_AX = 28
_MAX_PROMPT_TEXT = 34
_MATCH_THRESHOLD = 0.6

_ACTIVE_SNAPSHOT: "StateSnapshot | None" = None


@dataclass
class StateSnapshot:
    snapshot_id: str
    revision: int
    step_index: int
    screenshot_path: str
    frontmost_app: str = ""
    isolate_app: str = ""
    created_at: float = field(default_factory=time.time)
    image_size: tuple[int, int] | None = None
    ax_candidates: list[dict] = field(default_factory=list)
    text_items: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def prompt_text(self) -> str:
        """Compact text block for the brain prompt."""
        lines = [
            f"snapshot_id={self.snapshot_id} revision={self.revision} step={self.step_index}",
            f"front_app={self.frontmost_app or '(unknown)'} isolate_app={self.isolate_app or '(none)'}",
            "이 스냅샷은 현재 스크린샷에서 미리 뽑은 저비용 후보다. "
            "라벨/텍스트가 있는 대상은 smart_click에 쓰고, 목록에 없지만 화면에 보이는 무라벨 아이콘은 ground_click을 써라. "
            "실제 행동 뒤에는 이 스냅샷을 낡은 증거로 보고 다음 관찰을 기다려라.",
        ]
        if self.ax_candidates:
            lines.append("AX/버튼 후보:")
            for m in self.ax_candidates[:_MAX_PROMPT_AX]:
                label = m.get("label") or "(무라벨)"
                role = m.get("role") or "AX"
                lines.append(
                    f"  - {role} '{label}' @({m.get('cx')},{m.get('cy')}) {m.get('w')}x{m.get('h')}"
                )
        else:
            lines.append("AX/버튼 후보: (없음)")
        if self.text_items:
            lines.append("화면 텍스트/OCR 후보:")
            for t in self.text_items[:_MAX_PROMPT_TEXT]:
                conf = t.get("conf")
                cstr = f" conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
                lines.append(f"  - '{t.get('text')}' @({t.get('cx')},{t.get('cy')}){cstr}")
        else:
            lines.append("화면 텍스트/OCR 후보: (없음)")
        if self.errors:
            lines.append("스냅샷 수집 경고: " + " / ".join(self.errors[:3]))
        return "\n".join(lines)


def set_active(snap: StateSnapshot | None) -> None:
    """Expose the current observation snapshot to the action layer."""
    global _ACTIVE_SNAPSHOT
    _ACTIVE_SNAPSHOT = snap


def clear_active() -> None:
    set_active(None)


def active_snapshot() -> StateSnapshot | None:
    return _ACTIVE_SNAPSHOT


def find_click_target(label: str, near: tuple[int | None, int | None] | None = None) -> dict:
    """Find a click target in the active snapshot without re-running AX/OCR.

    Returns:
      {"status":"pick", "candidate":..., "score":float, "snapshot_id":str, "by_near":bool}
      {"status":"ambiguous", "candidates":[...], ...}
      {"status":"none", "seen":str}
    """
    snap = active_snapshot()
    if snap is None:
        return {"status": "none", "reason": "no_active_snapshot", "seen": ""}
    target = (label or "").strip()
    if not target:
        return {"status": "none", "reason": "empty_label", "seen": ""}
    scored = _score_candidates(snap, target)
    strong = [(s, c) for s, c in scored if s >= _MATCH_THRESHOLD]
    if not strong:
        return {
            "status": "none",
            "reason": "not_found",
            "snapshot_id": snap.snapshot_id,
            "seen": _candidate_summary([c for _, c in scored[:8]]),
        }
    strong = _dedupe_scored(strong)
    has_near = bool(near) and near[0] is not None and near[1] is not None
    if has_near:
        nx, ny = int(near[0]), int(near[1])
        score, cand = min(strong, key=lambda sc: (sc[1]["cx"] - nx) ** 2 + (sc[1]["cy"] - ny) ** 2)
        return {
            "status": "pick",
            "snapshot_id": snap.snapshot_id,
            "candidate": cand,
            "score": round(float(score), 3),
            "by_near": True,
        }
    exacts = [(s, c) for s, c in strong if s >= 0.999]
    contenders = exacts if exacts else [(s, c) for s, c in strong if strong[0][0] - s <= 0.03]
    if len(contenders) >= 2:
        return {
            "status": "ambiguous",
            "snapshot_id": snap.snapshot_id,
            "candidates": [
                {"score": round(float(s), 3), **c} for s, c in contenders[:6]
            ],
            "seen": _candidate_summary([c for _, c in contenders[:6]]),
        }
    score, cand = contenders[0]
    return {
        "status": "pick",
        "snapshot_id": snap.snapshot_id,
        "candidate": cand,
        "score": round(float(score), 3),
        "by_near": False,
    }


def capture(
    screenshot_path: str,
    out_dir: str,
    step_index: int,
    revision: int,
    isolate_app: str | None = None,
    expected_front: str | None = None,
) -> StateSnapshot:
    """Build and persist a state snapshot for a freshly captured screenshot."""
    from . import perceive

    errors: list[str] = []
    frontmost = ""
    try:
        frontmost = perceive.frontmost_app() or ""
    except Exception as e:
        errors.append(f"frontmost:{type(e).__name__}")
    ax_allowed = True
    if expected_front and frontmost and frontmost != expected_front:
        ax_allowed = False
        errors.append(f"ax:frontmost_changed({expected_front}->{frontmost})")
    if isolate_app and frontmost:
        front_is_target = _same_app(frontmost, isolate_app)
        try:
            target_pid = perceive._resolve_pid(isolate_app)
            _, front_pid = perceive._front_window()
            if target_pid is not None and front_pid is not None and int(target_pid) == int(front_pid):
                front_is_target = True
        except Exception:
            pass
        if not front_is_target:
            ax_allowed = False
            errors.append(f"ax:frontmost_not_isolate({frontmost})")
    ax = _collect_ax(errors) if ax_allowed else []
    text = _collect_ocr(screenshot_path, errors)
    snap = build_from_parts(
        screenshot_path=screenshot_path,
        step_index=step_index,
        revision=revision,
        frontmost_app=frontmost,
        isolate_app=isolate_app or "",
        ax_candidates=ax,
        text_items=text,
        errors=errors,
    )
    append(out_dir, snap)
    return snap


def build_from_parts(
    screenshot_path: str,
    step_index: int,
    revision: int,
    frontmost_app: str = "",
    isolate_app: str = "",
    ax_candidates: list[dict] | None = None,
    text_items: list[dict] | None = None,
    errors: list[str] | None = None,
) -> StateSnapshot:
    ax = _compact_ax(ax_candidates or [])
    text = _compact_text(text_items or [])
    image_size = _image_size(screenshot_path)
    sid = _snapshot_id(screenshot_path, revision, step_index, frontmost_app, ax, text)
    return StateSnapshot(
        snapshot_id=sid,
        revision=revision,
        step_index=step_index,
        screenshot_path=os.path.abspath(screenshot_path),
        frontmost_app=frontmost_app,
        isolate_app=isolate_app,
        image_size=image_size,
        ax_candidates=ax,
        text_items=text,
        errors=list(errors or []),
    )


def append(out_dir: str, snap: StateSnapshot, keep: int = 80) -> None:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, _INDEX_FILE)
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            rows = data
    except Exception:
        rows = []
    rows.append(snap.to_dict())
    rows = rows[-keep:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def _collect_ax(errors: list[str]) -> list[dict]:
    try:
        from . import marks

        return marks.ax_marks(timeout=3)
    except Exception as e:
        errors.append(f"ax:{type(e).__name__}")
        return []


def _collect_ocr(screenshot_path: str, errors: list[str]) -> list[dict]:
    try:
        from . import ocr

        if not ocr.available():
            errors.append("ocr:unavailable")
            return []
        return ocr.ocr_screen(screenshot_path)
    except Exception as e:
        errors.append(f"ocr:{type(e).__name__}")
        return []


def _score_candidates(snap: StateSnapshot, target: str) -> list[tuple[float, dict]]:
    scored: list[tuple[float, dict]] = []
    for m in snap.ax_candidates:
        label = str(m.get("label") or "").strip()
        if not label:
            continue
        sc = _match_score(target, label)
        scored.append((sc, {"source": "snapshot_ax", "text": label, **m}))
    for t in snap.text_items:
        text = str(t.get("text") or "").strip()
        if not text:
            continue
        sc = _match_score(target, text)
        scored.append((sc, {"source": "snapshot_text", "label": text, **t}))
    scored.sort(key=lambda it: it[0], reverse=True)
    return scored


def _dedupe_scored(scored: list[tuple[float, dict]]) -> list[tuple[float, dict]]:
    out: list[tuple[float, dict]] = []
    seen: set[str] = set()
    for score, cand in scored:
        label = cand.get("label") or cand.get("text") or ""
        key = f"{_norm(label)}|{int(cand.get('cx') or 0)//8}|{int(cand.get('cy') or 0)//8}"
        if key in seen:
            continue
        seen.add(key)
        out.append((score, cand))
    return out


def _candidate_summary(candidates: list[dict]) -> str:
    return ", ".join(
        f"'{(c.get('label') or c.get('text') or '')[:14]}'@({c.get('cx')},{c.get('cy')})"
        for c in candidates
    )


def _match_score(query: str, text: str) -> float:
    q = _norm(query)
    t = _norm(text)
    if not q or not t:
        return 0.0
    tokens = [_norm(x) for x in str(text or "").split() if _norm(x)]
    if q == t:
        return 1.0
    if q in tokens:
        return 0.9
    if q in t:
        return 0.55 + 0.35 * (len(q) / max(1, len(t)))
    if t in q:
        return 0.6 * (len(t) / max(1, len(q)))
    sc = difflib.SequenceMatcher(None, q, t).ratio()
    if len(q) <= 3:
        sc *= 0.7
    return float(sc)


def _compact_ax(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        role = _clean(raw.get("role") or raw.get("kind") or "AX", 28)
        label = _clean(raw.get("label") or "", 60)
        cx = _to_int(raw.get("cx"))
        cy = _to_int(raw.get("cy"))
        w = _to_int(raw.get("w")) or 0
        h = _to_int(raw.get("h")) or 0
        if cx is None or cy is None:
            continue
        if w > 1400 or h > 1000:
            continue
        key = f"{_norm(role)}|{_norm(label)}|{cx//8}|{cy//8}|{w//8}|{h//8}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"role": role, "label": label, "cx": cx, "cy": cy, "w": w, "h": h})
        if len(out) >= _MAX_AX:
            break
    out.sort(key=lambda m: (_ax_rank(m), int(m.get("cy") or 0), int(m.get("cx") or 0)))
    return out


def _compact_text(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        text = _clean(raw.get("text") or "", 80)
        if not _useful_text(text):
            continue
        cx = _to_int(raw.get("cx"))
        cy = _to_int(raw.get("cy"))
        if cx is None or cy is None:
            continue
        key = f"{_norm(text)}|{cx//10}|{cy//10}"
        if key in seen:
            continue
        seen.add(key)
        row: dict[str, Any] = {"text": text, "cx": cx, "cy": cy}
        for k in ("w", "h"):
            v = _to_int(raw.get(k))
            if v is not None:
                row[k] = v
        conf = raw.get("conf")
        if isinstance(conf, (int, float)):
            row["conf"] = round(float(conf), 3)
        out.append(row)
        if len(out) >= _MAX_TEXT:
            break
    out.sort(key=lambda m: (int(m.get("cy") or 0), int(m.get("cx") or 0)))
    return out


def _ax_rank(m: dict) -> int:
    role = str(m.get("role") or "").lower()
    label = str(m.get("label") or "")
    if "button" in role:
        return 0 if label else 1
    if "menu" in role or "link" in role:
        return 2
    if "text" in role or "search" in role or "field" in role:
        return 3
    return 4


def _image_size(path: str) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return int(im.size[0]), int(im.size[1])
    except Exception:
        return None


def _snapshot_id(
    screenshot_path: str,
    revision: int,
    step_index: int,
    frontmost_app: str,
    ax: list[dict],
    text: list[dict],
) -> str:
    h = hashlib.sha1()
    h.update(os.path.abspath(screenshot_path).encode("utf-8", "ignore"))
    try:
        st = os.stat(screenshot_path)
        h.update(f"{st.st_mtime_ns}:{st.st_size}".encode())
    except OSError:
        pass
    h.update(f"{revision}:{step_index}:{frontmost_app}".encode("utf-8", "ignore"))
    for row in ax[:12]:
        h.update(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8", "ignore"))
    for row in text[:12]:
        h.update(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8", "ignore"))
    return "snap_" + h.hexdigest()[:12]


def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _norm(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").lower())


def _same_app(a: str | None, b: str | None) -> bool:
    na, nb = _norm(a or ""), _norm(b or "")
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _useful_text(text: str) -> bool:
    if not text:
        return False
    if len(text) > 80:
        return False
    if re.fullmatch(r"[\W_]+", text):
        return False
    return True


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
