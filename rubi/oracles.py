"""RUBI deterministic oracles.

VLM 판정은 강하지만 비싸고 흔들릴 수 있다. 여기에는 화면/파일/DOM/AX 같은
싼 증거를 모아 같은 포맷으로 제공한다. 최종 판정은 RUBI verifier 가 하되,
이 evidence 를 먼저 읽게 만든다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict


@dataclass
class OracleEvidence:
    kind: str
    status: str
    summary: str
    details: dict


def collect(goal: str, screenshot_path: str | None = None, trace_text: str | None = None) -> list[OracleEvidence]:
    out: list[OracleEvidence] = []
    if screenshot_path:
        out.append(_ocr_evidence(screenshot_path))
    if goal and screenshot_path:
        out.append(_goal_text_overlap(goal, out))
    if trace_text:
        out.append(_trace_evidence(trace_text))
    return out


def format_evidence(items: list[OracleEvidence]) -> str:
    if not items:
        return "(deterministic evidence unavailable)"
    lines = []
    for e in items:
        lines.append(f"- {e.kind} [{e.status}]: {e.summary}")
        if e.details:
            details = ", ".join(f"{k}={v}" for k, v in e.details.items() if v not in (None, "", []))
            if details:
                lines.append(f"  details: {details[:500]}")
    return "\n".join(lines)


def to_dicts(items: list[OracleEvidence]) -> list[dict]:
    return [asdict(e) for e in items]


def _ocr_evidence(screenshot_path: str) -> OracleEvidence:
    if not os.path.exists(screenshot_path):
        return OracleEvidence("ocr", "failed", "screenshot does not exist", {"path": screenshot_path})
    try:
        from sapphi import ocr
        if not ocr.available():
            return OracleEvidence("ocr", "unavailable", "ocrground binary unavailable", {})
        items = ocr.ocr_screen(screenshot_path)
        texts = [str(it.get("text", "")).strip() for it in items if str(it.get("text", "")).strip()]
        summary = " | ".join(texts[:80]) if texts else "no text detected"
        return OracleEvidence("ocr", "success" if texts else "empty", summary, {"count": len(texts)})
    except Exception as e:
        return OracleEvidence("ocr", "failed", f"{type(e).__name__}: {str(e)[:120]}", {})


def _goal_text_overlap(goal: str, evidence: list[OracleEvidence]) -> OracleEvidence:
    ocr_text = " ".join(e.summary for e in evidence if e.kind == "ocr" and e.status == "success")
    goal_tokens = _tokens(goal)
    seen_tokens = _tokens(ocr_text)
    overlap = sorted(goal_tokens & seen_tokens)
    status = "success" if overlap else "empty"
    return OracleEvidence(
        "goal_text_overlap",
        status,
        "goal keywords visible in screen OCR" if overlap else "no goal keywords visible in OCR",
        {"overlap": overlap[:20], "goal_token_count": len(goal_tokens)},
    )


def _trace_evidence(trace_text: str) -> OracleEvidence:
    commits = len(re.findall(r"\bconfirm\b|\bcommit\b|전송|결제|삭제|송금", trace_text, re.IGNORECASE))
    failures = len(re.findall(r"failed|blocked|abort|실패|차단|중단", trace_text, re.IGNORECASE))
    return OracleEvidence(
        "trace",
        "success",
        f"trace has {commits} commit-ish signals and {failures} failure/block signals",
        {"commit_signals": commits, "failure_signals": failures},
    )


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[0-9A-Za-z가-힣]+", (s or "").lower()) if len(t) >= 2}
