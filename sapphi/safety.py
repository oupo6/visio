"""리허설+게이팅 안전층 — Sapphi 의 핵심 차별점.

모델의 자기 위험 평가(action.risk/commit)만 믿지 않는다. 키워드 백스톱으로
'비가역 커밋' 후보(전송/구매/삭제/송금/결제…)를 독립적으로 한 번 더 잡는다.
(감독과 워커가 같은 맹점이면 같이 놓친다 → 독립 검증)
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Action

# 위험한 셸 명령 패턴 — 이런 게 있으면 비가역으로 간주(삭제·시스템 변경·전송 등)
DANGEROUS_SHELL = [
    "rm ", "rm -", "rmdir", "mkfs", "dd ", "sudo", "kill", "shutdown", "reboot",
    "mv ", "> ", ">>", "diskutil", "format", "defaults delete", "launchctl",
    "curl", "wget", "ssh", "scp", "git push", "npm publish",
    "do shell script",  # osascript 자체는 가역(키 입력 등)일 수 있어 통째 차단 X.
                        # 다만 osascript 안에서 셸을 호출(do shell script)하면 위험으로 본다.
]

# 되돌릴 수 없는 '커밋' 신호 — 버튼 라벨/생각에 이런 말이 있으면 비가역으로 간주
IRREVERSIBLE_HINTS = [
    # 한국어
    "전송", "보내기", "보내", "구매", "결제", "주문", "송금", "이체", "삭제", "제거",
    "탈퇴", "확인", "동의", "지불", "신청", "등록", "게시", "발행", "공개",
    # 영어
    "send", "buy", "purchase", "pay", "order", "submit", "delete", "remove",
    "transfer", "confirm", "post", "publish", "checkout", "place order", "withdraw",
]

# 게이팅 결정
ALLOW = "allow"        # 그냥 실행
STOP = "stop"          # 리허설: 비가역 직전 정지
CONFIRM = "confirm"    # live: 사람 확인 필요


@dataclass
class RiskDecision:
    """안전 정책의 구조화된 판단.

    gate 는 기존 런타임 호환용 결과이고, level/reason 은 trace·preview·RUBI가 읽는 중간 언어다.
    """

    gate: str
    level: str          # low | medium | high | critical
    reason: str
    matched: str = ""


def classify(action: Action) -> RiskDecision:
    """액션 자체의 위험을 분류한다. mode 와 무관하게 level/reason 만 정한다."""
    if action.action == "done":
        return RiskDecision(ALLOW, "low", "agent-only done marker")
    if action.commit or action.risk == "irreversible":
        return RiskDecision(CONFIRM, "high", "model marked action as irreversible/commit",
                            "commit|risk")
    if action.action == "shell":
        cmd = (action.command or "").lower()
        for p in DANGEROUS_SHELL:
            if p in cmd:
                level = "critical" if p in ("sudo", "rm ", "rm -", "dd ", "mkfs", "git push") else "high"
                return RiskDecision(CONFIRM, level, "dangerous shell pattern", p)
        return RiskDecision(ALLOW, "medium", "shell primitive requires act-level allowlist")
    if action.action == "device_query":
        return RiskDecision(ALLOW, "low", "local device signal query")
    hay = (action.target_label or "").lower()
    for h in IRREVERSIBLE_HINTS:
        if h in hay:
            level = "critical" if h in ("결제", "송금", "이체", "pay", "transfer", "checkout") else "high"
            return RiskDecision(CONFIRM, level, "target label looks like external commit", h)
    if action.action in {"type", "key", "click", "smart_click", "ax_click", "ocr_click", "ground_click"}:
        return RiskDecision(ALLOW, "medium", "screen/text action may affect UI")
    return RiskDecision(ALLOW, "low", "agent or wait action")


def is_irreversible(action: Action) -> bool:
    d = classify(action)
    return d.level in {"high", "critical"}


def decide(action: Action, mode: str) -> RiskDecision:
    """모드별 게이트까지 포함한 최종 정책 판단."""
    d = classify(action)
    if d.level in {"high", "critical"}:
        if mode == "rehearse":
            return RiskDecision(STOP, d.level, d.reason, d.matched)
        if mode == "live":
            return RiskDecision(CONFIRM, d.level, d.reason, d.matched)
    return RiskDecision(ALLOW, d.level, d.reason, d.matched)


def gate(action: Action, mode: str) -> str:
    """모드별 게이팅 결정.

    plan     : 아무것도 실행 안 함(별도 처리) — 여기서는 호출 안 됨
    rehearse : 비가역이면 STOP, 아니면 ALLOW
    live     : 비가역이면 CONFIRM(사람 y/n), 아니면 ALLOW
    """
    return decide(action, mode).gate
