"""Sapphi 행동 스키마.

모델은 자연어로 '생각'하지만, 런타임/감독관/안전층은 같은 중간 언어를 읽어야 한다.
그래서 Action 은 모델이 요청한 의도, ToolResult 는 도구가 실제로 한 일을 구조화한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# GUI 행동 + 'shell'(확실한 명령: open -a 등) + 'ask'(되묻기) + 'abort'(안전 중단) + 'done'
VALID_ACTIONS = {"move", "click", "double_click", "right_click", "type", "key", "scroll", "wait",
                 "shell", "open_app", "smart_click", "ax_click", "ocr_click", "ground_click",
                 "zoom", "device_query", "ask", "abort", "done"}

SCREEN_ACTIONS = {"move", "click", "double_click", "right_click", "smart_click", "ax_click", "ocr_click", "ground_click"}
TEXT_ACTIONS = {"type", "key"}
AGENT_ACTIONS = {"ask", "abort", "done", "zoom", "device_query"}
COMMIT_STATES = {"pending", "user_confirmed", "executed", "observed", "verified", "blocked"}


def _to_int(v):
    """모델이 좌표를 문자열("150")로 줘도 안전하게 int 로. 실패 시 None."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


@dataclass
class Action:
    action: str                       # VALID_ACTIONS 중 하나
    thought: str = ""                 # 왜 이 행동을 하는가
    x: Optional[int] = None           # move/click 좌표
    y: Optional[int] = None
    text: Optional[str] = None        # type 할 문자열
    keys: Optional[str] = None        # key 조합 (예: "cmd+space", "enter")
    amount: Optional[int] = None      # scroll 양(+위/-아래)
    command: Optional[str] = None     # shell 명령 (예: open -a "계산기")
    # 안전: 모델의 자기 위험 평가 (백스톱은 safety.py 가 별도로 검사)
    risk: str = "safe"                # "safe" | "reversible" | "irreversible"
    commit: bool = False              # 되돌릴 수 없는 '커밋'(전송/구매/삭제 등) 단계인가
    target_label: str = ""            # 클릭 대상 텍스트(예: 버튼 라벨) — 게이팅 백스톱용
    postcondition: str = ""           # 이 행동 후 충족되어야 할 관찰 조건(선택)
    # ★직전 행동의 postcondition 판정(기계가독). 런타임이 점검을 요청했을 때 plan 첫 행동에 실린다:
    #   True=기대효과 확인 / False=위반(런타임이 그 행동을 재시도 금지 등록) / None=점검 없음.
    post_met: Optional[bool] = None
    # ★음성 중계: 지금 하는 일을 사용자에게 *말로* 들려줄 한 문장(따뜻·짧게, 자비스 톤).
    #   예: "네이버 지도 열고 있어요", "찾았어요! 경로 읽어드릴게요". thought(기술적 사유)와 별개.
    say: str = ""

    @staticmethod
    def from_dict(d: dict) -> "Action":
        pm = d.get("post_met")
        return Action(
            action=d.get("action", ""),
            thought=d.get("thought", ""),
            say=d.get("say", ""),
            x=_to_int(d.get("x")),         # 모델이 "150" 처럼 문자열로 줘도 안전하게 int
            y=_to_int(d.get("y")),
            text=d.get("text"),
            keys=d.get("keys"),
            amount=_to_int(d.get("amount")),
            command=d.get("command"),
            risk=d.get("risk", "safe"),
            commit=bool(d.get("commit", False)),
            target_label=d.get("target_label", ""),
            postcondition=d.get("postcondition", ""),
            post_met=bool(pm) if isinstance(pm, bool) else None,
        )

    def short(self) -> str:
        bits = [self.action]
        if self.command:
            bits.append(f"`{self.command}`")
        if self.x is not None:
            bits.append(f"({self.x},{self.y})")
        if self.text:
            bits.append(repr(self.text))
        if self.keys:
            bits.append(self.keys)
        if self.target_label:
            bits.append(f"[{self.target_label}]")
        if self.postcondition:
            bits.append(f"{{post: {self.postcondition[:40]}}}")
        return " ".join(bits)

    def signature(self) -> str:
        """무한반복 감지용 — 같은 행동인지 식별."""
        return f"{self.action}|{self.keys}|{self.x},{self.y}|{self.command}|{self.target_label}"

    def kind(self) -> str:
        """런타임이 쓰는 큰 분류. ask/done 같은 에이전트 행동과 화면 행동을 분리한다."""
        if self.action in ("shell", "open_app"):   # OS 능력 행동(좌표 무관) — front 가드 대상 아님
            return "shell"
        if self.action in SCREEN_ACTIONS:
            return "screen"
        if self.action in TEXT_ACTIONS:
            return "text"
        if self.action in AGENT_ACTIONS:
            return "agent"
        return "unknown"

    def validates(self) -> tuple[bool, str]:
        """모델 JSON 을 그대로 믿지 않고, 액션별 필수 필드를 가볍게 검사한다."""
        if self.action not in VALID_ACTIONS:
            return False, f"unknown action: {self.action}"
        if self.action == "shell" and not self.command:
            return False, "shell action requires command"
        if self.action == "open_app" and not (self.target_label or self.text):
            return False, "open_app requires app name in target_label"
        if self.action in {"smart_click", "ax_click", "ocr_click", "ground_click"} and not (self.target_label or self.text):
            return False, f"{self.action} requires target_label or text"
        if self.action in {"move", "click", "double_click", "right_click"} and (self.x is None or self.y is None):
            return False, f"{self.action} requires x and y"
        if self.action == "type" and self.text is None:
            return False, "type action requires text"
        if self.action == "key" and not self.keys:
            return False, "key action requires keys"
        if self.action == "device_query" and not (self.target_label or self.text or self.command):
            return False, "device_query requires target_label, text, or command"
        if self.action == "ask" and not (self.text or self.thought):
            return False, "ask action requires question text"
        return True, ""


@dataclass
class ToolResult:
    """도구 실행 결과.

    status:
      success   — 의도한 primitive 가 실행됨
      failed    — 실행 실패
      ambiguous — 후보가 여럿이라 재지정 필요
      blocked   — 안전 정책상 실행하지 않음
      skipped   — plan/zoom/done 등 실제 OS 조작 없음
    """

    status: str = "success"
    method: str = ""
    evidence: str = ""
    target_label: str = ""
    confidence: Optional[float] = None
    bbox: Optional[tuple[int, int, int, int]] = None
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def summary(self, limit: int = 120) -> str:
        parts = [self.status]
        if self.method:
            parts.append(self.method)
        if self.target_label:
            parts.append(f"[{self.target_label}]")
        text = self.evidence or self.raw
        if text:
            parts.append(text)
        out = " · ".join(parts)
        return out[:limit]


@dataclass
class CommitRecord:
    """비가역 커밋의 상태 머신 로그."""

    action: Action
    state: str = "pending"
    tool_result: ToolResult | None = None
    observation_path: str | None = None
    verified: bool = False
    note: str = ""
