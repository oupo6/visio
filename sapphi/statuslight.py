"""상태등 — 자율 실행 중 사용자에게 "지금 만져도 되는가"를 화면에 표시한다.

원리(사용자 통찰): 위험 구간은 *행동 실행(act) 순간*뿐이고, 가장 긴 구간(두뇌 생각 15~60s)은
사용자가 맥을 써도 된다 — 단, agent 의 frontmost 가드가 함께 있어야 안전(생각 중 사용자가
창을 바꾸면 다음 행동이 거기 꽂히는 사고 방지 → 가드가 잡고 재관찰).

구성:
  - set_phase(phase): 에이전트 루프가 단계를 파일(/tmp/sapphi_phase.json)에 기록 (acting|thinking|idle)
  - 오버레이 프로세스(`python -m sapphi.statuslight`): 화면 우상단 항상-위 작은 띠.
      🔴 조작 중 — 손대지 마 / 🟢 생각 중 — 자유 / ⚪ 대기
    별도 프로세스(tkinter) → inputlock 의 GIL 문제(VLM 추론이 탭 스레드 굶김) 회피.
  - spawn_overlay(): 이미 떠 있지 않으면 오버레이를 백그라운드로 띄움(에이전트가 호출).

오버레이는 stale 보호가 있다: phase 파일이 STALE_SEC 이상 오래되면 무조건 idle 로 표시
(에이전트가 죽어도 🔴가 남지 않음).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

PHASE_FILE = "/tmp/sapphi_phase.json"
PID_FILE = "/tmp/sapphi_statuslight.pid"
STALE_SEC = 120          # 이보다 오래된 phase 는 idle 취급(죽은 에이전트의 🔴 잔류 방지)
_POLL_MS = 300

_STYLES = {
    "acting":   ("#c62828", "#ffffff", "🔴 SAPPHI 조작 중 — 손대지 마세요"),
    "thinking": ("#2e7d32", "#ffffff", "🟢 생각 중 — 맥 써도 됨"),
    # ★승인 대기: 비가역 동작 직전 사람 y/n 확인 구간. 이때는 *터미널 입력이 필요*하므로
    #   "손대지 마"가 아니라 "지금 터미널에 입력하세요"라고 명시한다(맥 조작=정상). 승인 즉시
    #   에이전트가 타깃 앱을 다시 활성화하므로 입력이 엉뚱한 창으로 새지 않는다.
    "awaiting": ("#f9a825", "#000000", "🟡 승인 대기 — 터미널에 y/n 입력하세요"),
    "idle":     ("#424242", "#bbbbbb", "⚪ SAPPHI 대기"),
}


def set_phase(phase: str) -> None:
    """에이전트 루프가 부른다. 실패해도 본 작업을 막지 않는다(표시는 보조 기능)."""
    try:
        tmp = PHASE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"phase": phase, "ts": time.time()}, f)
        os.replace(tmp, PHASE_FILE)   # 원자적 교체(오버레이가 반쪽 파일을 읽지 않게)
    except Exception:
        pass


def _read_phase() -> str:
    try:
        with open(PHASE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - float(d.get("ts", 0)) > STALE_SEC:
            return "idle"
        p = str(d.get("phase", "idle"))
        return p if p in _STYLES else "idle"
    except Exception:
        return "idle"


def _overlay_running() -> bool:
    try:
        pid = int(open(PID_FILE).read().strip())
        os.kill(pid, 0)   # 신호 0 = 존재 확인만
        return True
    except Exception:
        return False


def spawn_overlay() -> bool:
    """오버레이가 안 떠 있으면 백그라운드로 띄운다(에이전트 시작 시 호출). 띄웠/이미있으면 True."""
    if os.environ.get("SAPPHI_LIGHT", "1") == "0":
        return False
    if _overlay_running():
        return True
    try:
        subprocess.Popen([sys.executable, "-m", "sapphi.statuslight"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return True
    except Exception:
        return False


def main() -> int:
    try:
        import tkinter as tk
    except Exception:
        print("tkinter 없음 — 상태등 오버레이 불가(파일 기록은 계속 작동)")
        return 1
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    root = tk.Tk()
    root.overrideredirect(True)          # 테두리/타이틀 없음
    root.attributes("-topmost", True)    # 항상 위
    try:
        root.attributes("-alpha", 0.88)
    except Exception:
        pass
    label = tk.Label(root, font=("AppleSDGothicNeo-Bold", 13), padx=12, pady=4)
    label.pack(fill="both", expand=True)

    sw = root.winfo_screenwidth()
    W, H = 320, 30
    root.geometry(f"{W}x{H}+{sw - W - 16}+8")   # 우상단(메뉴바 바로 아래)

    def tick():
        bg, fg, text = _STYLES[_read_phase()]
        label.config(bg=bg, fg=fg, text=text)
        root.config(bg=bg)
        root.attributes("-topmost", True)        # 다른 창이 위로 와도 다시 위로
        root.after(_POLL_MS, tick)

    tick()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
