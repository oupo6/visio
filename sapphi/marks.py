"""set-of-marks — 화면의 *클릭 가능 요소*를 정확한 박스로 열거해 번호를 단다.

핵심: VLM이 좌표를 '회귀'하면 작은 아이콘서 부정확(grounding 미스의 원인). 대신 *정확한 박스를 검출*하고
모델은 '몇 번 박스'만 고르게 한다 → 회귀 오차 0.

검출 우선순위:
 ① 접근성(AX) 요소 — 역할(Button 등)·라벨·*정확한 frame(position/size)*. 정상 앱에선 가장 정확하고 풍부.
 ② OCR 텍스트 박스 — AX가 빈약할 때 보강.

★wrapped iOS 앱(네이버지도)은 AX가 거의 안 나온다 → ①이 비어 ②(글자)만 남고, *글자 없는 아이콘*은
   둘 다 못 잡는다. 이게 그 앱이 자동화에 *구조적으로* 적대적인 이유를 그대로 보여준다.
"""

from __future__ import annotations

import subprocess
from typing import Optional

_SEP = "§"

# 한 요소 X의 role§label§x,y,w,h 를 out 에 추가하는 인라인 스니펫(핸들러·큐 없이 — 참조 스코프 보존).
def _collect(v: str) -> str:
    return (
        f'      set r to ""\n      try\n        set r to role of {v}\n      end try\n'
        f'      set ln to ""\n      try\n        set ln to description of {v}\n      end try\n'
        f'      if ln is "" then\n        try\n          set ln to name of {v}\n        end try\n      end if\n'
        f'      set px to ""\n      try\n'
        f'        set p to position of {v}\n        set s to size of {v}\n'
        f'        set px to ((item 1 of p) as integer as string) & "," & ((item 2 of p) as integer as string) & "," & ((item 1 of s) as integer as string) & "," & ((item 2 of s) as integer as string)\n'
        f'      end try\n'
        f'      if px is not "" then set out to out & r & "{_SEP}" & ln & "{_SEP}" & px & linefeed\n'
    )


# 작동 확인된 *직접 중첩 반복*(3레벨) — 큐에 창 참조 저장하던 버그 회피. 대부분 툴바/패널은 3레벨 안.
_AX_MARKS_TMPL = (
    'tell application "System Events" to tell (first application process whose frontmost is true)\n'
    '  set out to ""\n'
    '  try\n'
    '    repeat with e in (UI elements of window 1)\n'
    + _collect("e") +
    '      try\n'
    '        repeat with e2 in (UI elements of e)\n'
    + _collect("e2") +
    '          try\n'
    '            repeat with e3 in (UI elements of e2)\n'
    + _collect("e3") +
    '            end repeat\n'
    '          end try\n'
    '        end repeat\n'
    '      end try\n'
    '    end repeat\n'
    '  end try\n'
    '  return out\n'
    'end tell\n'
)


def ax_marks(timeout: int = 8, activate_bundle: Optional[str] = None) -> list[dict]:
    """접근성 트리의 클릭가능 요소 후보 [{kind:'ax', role, label, cx, cy, w, h}]. 실패/빈약하면 [].
    activate_bundle 주면 *같은 osascript 안에서* 그 앱을 먼저 activate(포커스 드리프트 틈 제거 — atomic)."""
    script = _AX_MARKS_TMPL
    if activate_bundle:
        script = (f'tell application id "{activate_bundle}" to activate\ndelay 1.3\n') + script
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout + 2)
    except Exception:
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        parts = line.split(_SEP)
        if len(parts) != 3:
            continue
        role, lbl, frame = parts
        try:
            x, y, w, h = [int(v) for v in frame.split(",")]
        except Exception:
            continue
        if w > 1400 or h > 1000:        # 창 전체 같은 거대 컨테이너만 제외
            continue
        if w <= 1 or h <= 1:
            w, h = 30, 20
        # 클릭 후보: 버튼류이거나 라벨이 있는 것
        clickable = ("Button" in role) or ("MenuItem" in role) or ("Link" in role) or bool((lbl or "").strip())
        if not clickable:
            continue
        out.append({"kind": "ax", "role": role.replace("AX", ""), "label": (lbl or "").strip()[:30],
                    "cx": x + w // 2, "cy": y + h // 2, "w": w, "h": h})
    return out


def ocr_marks() -> list[dict]:
    """OCR 텍스트 박스 후보 [{kind:'text', label, cx, cy, w, h}]. AX 보강용."""
    from . import ocr
    out = []
    try:
        for it in ocr.ocr_screen(front_window_only=True):
            t = (it.get("text") or "").strip()
            if not t:
                continue
            out.append({"kind": "text", "label": t[:30], "cx": int(it["cx"]), "cy": int(it["cy"]),
                        "w": int(it.get("w", 40)), "h": int(it.get("h", 18))})
    except Exception:
        pass
    return out


def detect() -> list[dict]:
    """클릭 후보 마크 목록. AX 우선, 빈약하면 OCR 보강. 번호(id) 부여."""
    marks = ax_marks()
    ax_n = len(marks)
    if ax_n < 4:                       # AX가 빈약(wrapped iOS 등) → OCR 텍스트로 보강
        marks = marks + ocr_marks()
    for i, m in enumerate(marks):
        m["id"] = i
    return marks


def summary(marks: list[dict]) -> str:
    ax = [m for m in marks if m["kind"] == "ax"]
    tx = [m for m in marks if m["kind"] == "text"]
    lines = [f"마크 {len(marks)}개 (AX {len(ax)} · 텍스트 {len(tx)})"]
    for m in marks[:40]:
        tag = m.get("role", "text")
        lines.append(f"  [{m['id']}] {tag}: '{m['label']}' @({m['cx']},{m['cy']}) {m['w']}x{m['h']}")
    return "\n".join(lines)
