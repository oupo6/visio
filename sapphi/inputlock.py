"""입력 잠금 — 자동화 실행 중 *사용자의 실제 마우스·키보드를 차단*해 오염을 막는다.

원리: macOS 이벤트탭(CGEventTap, 세션 레벨)으로 입력을 가로채, *우리(pyautogui) 합성 이벤트*
(소스 PID == 우리 프로세스)는 통과시키고, *사용자의 실제 입력*(다른 PID)은 억제한다.
(실증: pyautogui 이벤트는 kCGEventSourceUnixProcessID == os.getpid() 를 달고 온다.)

★안전장치(잠금이 사용자를 가두지 않게):
  ① ESC 키는 *항상 통과 + 즉시 해제*(비상탈출).
  ② watchdog 타임아웃(max_seconds) 자동 해제.
  ③ with 블록 종료/예외 시 해제. 프로세스가 죽어도 탭은 함께 사라져 입력 복구.
  ④ 탭 생성 실패(권한 없음 등)면 *조용히 비활성*(자동화는 계속, 잠금만 미적용).

※ '손쉬운 사용(Accessibility)' 권한 필요(ax_click 과 동일 칸).
"""

from __future__ import annotations

import os
import threading
import time

_ESC_KEYCODE = 53


def available() -> bool:
    try:
        import Quartz  # noqa: F401
        return True
    except Exception:
        return False


def _all_input_mask():
    import Quartz
    types = [
        Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp,
        Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp,
        Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDragged,
        Quartz.kCGEventRightMouseDragged, Quartz.kCGEventKeyDown,
        Quartz.kCGEventKeyUp, Quartz.kCGEventFlagsChanged,
        Quartz.kCGEventScrollWheel, Quartz.kCGEventOtherMouseDown,
        Quartz.kCGEventOtherMouseUp, Quartz.kCGEventOtherMouseDragged,
    ]
    m = 0
    for t in types:
        m |= Quartz.CGEventMaskBit(t)
    return m


class InputLock:
    """컨텍스트 매니저. with InputLock(): 동안 사용자 입력 차단(우리 자동화는 통과).
    on_abort: ESC 비상탈출 시 호출(예: 실행 중단 신호)."""

    def __init__(self, max_seconds: int = 180, on_abort=None, verbose: bool = False):
        self.max_seconds = max_seconds
        self.on_abort = on_abort
        self.verbose = verbose
        self.engaged = False        # 실제로 잠금이 걸렸나(권한 등 실패면 False)
        self.aborted = False        # 사용자가 ESC로 탈출했나
        self.suppressed = 0         # 삼킨 사용자 입력 이벤트 수(관측용)
        self._tap = None
        self._rl = None
        self._thread = None
        self._mypid = os.getpid()
        self._released = False

    # 탭 콜백 — 우리 PID면 통과, 사용자면 억제(ESC만 예외)
    def _callback(self, proxy, type_, event, refcon):
        import Quartz
        if type_ in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            try:
                Quartz.CGEventTapEnable(self._tap, True)   # 재활성(콜백 지연 시 OS가 끔)
            except Exception:
                pass
            return event
        try:
            pid = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUnixProcessID)
        except Exception:
            return event
        if pid == self._mypid:
            return event   # 우리 합성 입력 → 통과
        # === 실제 사용자 입력 ===
        if type_ == Quartz.kCGEventKeyDown:
            try:
                kc = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            except Exception:
                kc = -1
            if kc == _ESC_KEYCODE:   # ★비상탈출
                self.aborted = True
                if self.on_abort:
                    try:
                        self.on_abort()
                    except Exception:
                        pass
                self.release()
                return event   # ESC 자체는 통과
        self.suppressed += 1
        return None   # 그 외 사용자 입력 = 억제(삼킴)

    def _run(self):
        import Quartz
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault, _all_input_mask(), self._callback, None)
        if not tap:
            if self.verbose:
                print("[inputlock] ⚠️ 이벤트탭 생성 실패 — 손쉬운 사용 권한? (잠금 없이 진행)")
            return   # 권한 등 실패 → 잠금 미적용(자동화는 계속)
        self._tap = tap
        src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._rl = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._rl, src, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self.engaged = True
        Quartz.CFRunLoopRun()

    def _watchdog(self):
        t0 = time.time()
        while time.time() - t0 < self.max_seconds:
            if self._released:
                return
            time.sleep(0.4)
        if self.verbose and not self._released:
            print(f"[inputlock] ⏱ {self.max_seconds}s 타임아웃 — 자동 해제")
        self.release()

    def __enter__(self):
        if not available():
            return self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        threading.Thread(target=self._watchdog, daemon=True).start()
        time.sleep(0.25)   # 탭 올라올 시간
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def release(self):
        if self._released:
            return
        self._released = True
        try:
            import Quartz
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, False)
            if self._rl is not None:
                Quartz.CFRunLoopStop(self._rl)
        except Exception:
            pass
        self.engaged = False
