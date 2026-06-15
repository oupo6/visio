"""RUBI verification records.

RUBI의 의미는 '모델이 한 번 더 생각함'이 아니라, 워커의 주장과 실제 증거를
분리해 감사 가능한 기록으로 남기는 데 있다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone


@dataclass
class VerificationRecord:
    goal: str
    task_key: str
    achieved: bool
    rationale: str
    diagnosis: str = ""
    directive: str = ""
    screenshot_path: str = ""
    trace_path: str = ""
    oracle_evidence: list | None = None
    axis_results: list | None = None
    confidence: float = 0.5
    created_at: str = ""


def _path(routines_dir: str) -> str:
    return os.path.join(routines_dir, "verification_records.json")


def load(routines_dir: str) -> list[dict]:
    p = _path(routines_dir)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save(routines_dir: str, record: VerificationRecord, max_entries: int = 200) -> None:
    rows = load(routines_dir)
    if not record.created_at:
        record.created_at = datetime.now(timezone.utc).isoformat()
    rows.append(asdict(record))
    if len(rows) > max_entries:
        rows = rows[-max_entries:]
    os.makedirs(routines_dir, exist_ok=True)
    with open(_path(routines_dir), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def confidence_from_verdict(verdict: dict, achieved: bool) -> float:
    """검증 판정 자체가 기억 저장에 충분히 믿을 만한지 산정한다.

    여기서 confidence 는 "작업이 성공했을 확률"이 아니라 "RUBI의 판정을
    믿을 수 있는 정도"다. 축 fail 은 실패 신호가 명확하다는 뜻일 수 있으므로
    감점하지 않는다. 불확실한 축만 confidence 를 낮춘다.
    """
    base = 0.52
    evidence = verdict.get("oracle_evidence") or []
    if evidence:
        success_count = sum(1 for e in evidence if e.get("status") in ("success", "empty"))
        base += min(0.25, 0.08 * success_count)
    if verdict.get("evidence"):
        base += 0.05
    axes = verdict.get("axis_results") or []
    if axes:
        decisive = sum(1 for a in axes if a.get("verdict") in ("pass", "fail"))
        uncertain = sum(1 for a in axes if a.get("verdict") == "uncertain")
        base += min(0.25, 0.07 * decisive)
        base -= min(0.2, 0.08 * uncertain)
    if achieved:
        base += 0.02
    return max(0.0, min(base, 0.95))
