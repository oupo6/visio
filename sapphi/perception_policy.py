"""Perception policy for GUI actions.

The brain should not decide whether to use AX, OCR, or VLM grounding. The tool
layer profiles the current app and chooses the cheapest reliable signal first.
"""

from __future__ import annotations

import json
import os
import re
import time

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "app_profiles.json")
_TTL_SEC = 60 * 60 * 24 * 7
_TARGET_FAIL_TTL_SEC = 60 * 10
_TARGET_POLICY_TTL_SEC = 60 * 60 * 24 * 7


def profile_front_app(force: bool = False) -> dict:
    """Return a cached profile for the current frontmost app."""
    from . import marks, perceive

    app = perceive.frontmost_app() or "unknown"
    cache = _load_cache()
    row = cache.get(app)
    if row and not force and time.time() - float(row.get("ts", 0)) < _TTL_SEC:
        return row
    old = row if isinstance(row, dict) else {}
    ax = marks.ax_marks(timeout=3)
    labels = [str(m.get("label") or "") for m in ax if str(m.get("label") or "").strip()]
    row = {
        "app": app,
        "mode": "structured" if len(ax) >= 4 else "opaque",
        "ax_count": len(ax),
        "sample_labels": labels[:40],
        "ts": time.time(),
    }
    if isinstance(old.get("target_stats"), dict):
        row["target_stats"] = _prune_target_stats(old.get("target_stats") or {})
    if isinstance(old.get("ax_target_failures"), dict):
        row["ax_target_failures"] = old.get("ax_target_failures") or {}
    if old.get("last_ax_ok") is not None:
        row["last_ax_ok"] = bool(old.get("last_ax_ok"))
    if old.get("last_ax_ts") is not None:
        row["last_ax_ts"] = old.get("last_ax_ts")
    cache[app] = row
    _save_cache(cache)
    return row


def should_try_ax(target: str) -> bool:
    """AX first for structured native apps; skip for opaque wrapped apps."""
    target = (target or "").strip()
    if not target:
        return False
    try:
        prof = profile_front_app()
    except Exception:
        return False
    return should_try_ax_for_profile(target, prof)


def preferred_tier(target: str) -> str:
    """Return a cached preferred tier for the current app/target, if known."""
    target = (target or "").strip()
    if not target:
        return ""
    try:
        prof = profile_front_app()
    except Exception:
        return ""
    return preferred_tier_for_profile(target, prof)


def preferred_tier_for_profile(target: str, prof: dict) -> str:
    """Pure cached target policy.

    This only returns a preference after previous real outcomes make one tier
    clearly better. It is deliberately conservative; stale or weak evidence
    falls back to the normal snapshot -> AX/OCR/ground ladder.
    """
    row = _target_stats_for_profile(target, prof)
    if not row or _stale(row.get("last_ts"), _TARGET_POLICY_TTL_SEC):
        return ""
    success = row.get("success") or {}
    failure = row.get("failure") or {}
    last = str(row.get("last_success_method") or "")
    if _last_failure_is_newer(row, last):
        return ""
    if last == "ground" and int(success.get("ground") or 0) >= 1:
        if int(failure.get("ax") or 0) >= 1 or int(failure.get("ocr") or 0) >= 1:
            return "ground"
    if last == "ocr" and int(success.get("ocr") or 0) >= 1 and int(failure.get("ax") or 0) >= 1:
        return "ocr"
    if last == "ax" and int(success.get("ax") or 0) >= 1:
        return "ax"
    return ""


def should_try_ax_for_profile(target: str, prof: dict) -> bool:
    """Pure target-level AX policy, testable without touching the live screen.

    App-level AX richness is not enough. KakaoTalk can expose plenty of text via
    AX while a specific target like "친구 탭 아이콘" is an unlabeled drawing.
    """
    target = (target or "").strip()
    if not target:
        return False
    preferred = preferred_tier_for_profile(target, prof)
    if preferred == "ax":
        return True
    if preferred in {"ocr", "ground"}:
        return False
    if _recent_target_ax_failure(target, prof):
        return False
    labels = [str(x or "") for x in (prof.get("sample_labels") or [])]
    if _matches_sample_label(target, labels):
        return True
    # Do not infer "AX should work" from app-level richness alone. The current
    # state may contain many AX labels while the requested target is an
    # unlabeled visual icon. If the target is not represented in sampled AX
    # labels, let OCR/grounding handle it.
    return False


def note_axis_result(ok: bool, target: str = "") -> None:
    """Keep the current app profile honest after an AX attempt."""
    from . import perceive

    app = perceive.frontmost_app() or "unknown"
    cache = _load_cache()
    row = cache.get(app) or {"app": app, "mode": "structured", "ax_count": 0}
    row["ts"] = time.time()
    row["last_ax_ok"] = bool(ok)
    row["last_ax_ts"] = time.time()
    if not ok and row.get("mode") == "structured":
        row["ax_failures"] = int(row.get("ax_failures") or 0) + 1
        if target:
            failures = row.setdefault("ax_target_failures", {})
            failures[_norm(target)] = {"target": target, "ts": time.time()}
        if row["ax_failures"] >= 3:
            row["mode"] = "opaque"
    elif ok:
        row["ax_failures"] = 0
        row["mode"] = "structured"
    cache[app] = row
    _save_cache(cache)


def note_tier_result(method: str, ok: bool, target: str = "") -> None:
    """Remember which perception tier worked for an app+target.

    The cache is used to avoid repeatedly asking AX for unlabeled visual icons,
    while still allowing AX for targets that have actually succeeded before.
    """
    target = (target or "").strip()
    if not target:
        return
    from . import perceive

    app = perceive.frontmost_app() or "unknown"
    cache = _load_cache()
    row = cache.get(app) or {"app": app, "mode": "structured", "ax_count": 0, "sample_labels": []}
    row["ts"] = time.time()
    tier = _tier(method)
    if not tier:
        return
    stats = row.setdefault("target_stats", {})
    key = _norm(target)
    rec = stats.setdefault(key, {
        "target": target,
        "success": {},
        "failure": {},
    })
    bucket = rec.setdefault("success" if ok else "failure", {})
    bucket[tier] = int(bucket.get(tier) or 0) + 1
    rec["last_ts"] = time.time()
    if ok:
        rec["last_success_method"] = tier
        rec["last_success_ts"] = rec["last_ts"]
    else:
        rec["last_failure_method"] = tier
        rec["last_failure_ts"] = rec["last_ts"]
    row["target_stats"] = _prune_target_stats(stats)
    cache[app] = row
    _save_cache(cache)


def _load_cache() -> dict:
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(text or "").lower())


def _tier(method: str) -> str:
    m = str(method or "").lower()
    if "/" in m:
        m = m.split("/", 1)[1]
    for tier in ("snapshot", "ax", "ocr", "ground"):
        if tier in m:
            return tier
    return ""


def _matches_sample_label(target: str, labels: list[str]) -> bool:
    t = _norm(target)
    if not t:
        return False
    for label in labels:
        l = _norm(label)
        if len(l) < 2:
            continue
        if t == l or t in l or l in t:
            return True
    return False


def _recent_target_ax_failure(target: str, prof: dict) -> bool:
    failures = prof.get("ax_target_failures") or {}
    row = failures.get(_norm(target))
    if not isinstance(row, dict):
        row = {}
    recent_legacy = False
    try:
        recent_legacy = time.time() - float(row.get("ts", 0)) < _TARGET_FAIL_TTL_SEC
    except Exception:
        recent_legacy = False
    if recent_legacy:
        return True
    stats = _target_stats_for_profile(target, prof)
    if not stats:
        return False
    failure = stats.get("failure") or {}
    try:
        recent = time.time() - float(stats.get("last_failure_ts", 0)) < _TARGET_FAIL_TTL_SEC
    except Exception:
        recent = False
    return recent and int(failure.get("ax") or 0) > 0


def _target_stats_for_profile(target: str, prof: dict) -> dict:
    stats = prof.get("target_stats") or {}
    row = stats.get(_norm(target))
    return row if isinstance(row, dict) else {}


def _last_failure_is_newer(row: dict, tier: str) -> bool:
    if not tier:
        return False
    if str(row.get("last_failure_method") or "") != tier:
        return False
    try:
        return float(row.get("last_failure_ts") or 0) >= float(row.get("last_success_ts") or 0)
    except Exception:
        return False


def _prune_target_stats(stats: dict, keep: int = 80) -> dict:
    items = []
    now = time.time()
    for key, row in (stats or {}).items():
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("last_ts", 0))
        except Exception:
            ts = 0.0
        if now - ts <= _TARGET_POLICY_TTL_SEC:
            items.append((ts, key, row))
    items.sort(reverse=True)
    return {key: row for _, key, row in items[:keep]}


def _stale(ts, ttl: int) -> bool:
    try:
        return time.time() - float(ts or 0) > ttl
    except Exception:
        return True
