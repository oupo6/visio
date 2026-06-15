"""행동 실행 — pyautogui(마우스/키보드) + shell(확실한 명령).

plan 모드에서는 호출되지 않는다(실행 없음). rehearse/live 에서만 실제 조작.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from typing import Optional

from .models import Action, ToolResult

# ★OCR 클릭을 *맨앞 창 안으로* 격리할지(채팅창 등 다른 창의 같은 글자 헛클릭 방지). CLI(tool.py)가 켠다.
_ISOLATE_OCR = False

# pyautogui 가 인식하는 키 이름으로 정규화 (예: cmd→command). 이게 틀리면 단축키가 조용히 씹힌다.
_KEY_ALIASES = {
    "cmd": "command", "win": "command", "super": "command", "meta": "command",
    "opt": "option", "alt": "option",
    "control": "ctrl", "return": "enter", "esc": "escape",
    "del": "delete",
}


def _norm_keys(keys: str) -> list[str]:
    parts = [k.strip().lower() for k in (keys or "").replace("+", " ").split() if k.strip()]
    return [_KEY_ALIASES.get(k, k) for k in parts]


def _quartz_double_click(x, y) -> None:
    """★진짜 macOS 더블클릭 — pyautogui.doubleClick 은 두 단일클릭이라 clickState=2 가 안 잡혀
    앱이 더블클릭으로 인식 못한다. Quartz CGEvent 로 clickState 1→2 를 직접 실어 보낸다.
    (drive.py 에서 실측 검증된 구현과 동일 — 카톡 채팅방 더블클릭 열기 성공.)"""
    import Quartz
    pos = Quartz.CGPointMake(float(x), float(y))

    def post(etype, state):
        e = Quartz.CGEventCreateMouseEvent(None, etype, pos, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventSetIntegerValueField(e, Quartz.kCGMouseEventClickState, state)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)

    post(Quartz.kCGEventLeftMouseDown, 1)
    post(Quartz.kCGEventLeftMouseUp, 1)
    post(Quartz.kCGEventLeftMouseDown, 2)
    post(Quartz.kCGEventLeftMouseUp, 2)


def _paste_text(text: str) -> None:
    """텍스트 입력 — 클립보드 경유(pbcopy + ⌘V). pyautogui.write 는 한글 등 비ASCII 를
    물리키 시뮬로 처리해 *입력이 통째로 씹힌다* (구 Sapphi 의 한글 입력 실패 원인).
    클립보드 붙여넣기는 유니코드 전체를 확실히 입력한다. 기존 클립보드는 복원(텍스트 한정)."""
    import pyautogui
    old = None
    try:
        old = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        pass
    try:
        subprocess.run(["pbcopy"], input=text, text=True, timeout=5)
    except Exception:
        pyautogui.write(text, interval=0.02)   # 최후수단(ASCII만 제대로)
        return
    pyautogui.hotkey("command", "v")
    # ★레이스 완화(RUBI 지적): 느린 앱이 ⌘V 를 늦게 처리하면, 너무 빨리 클립보드를 복원할 때
    #   '이전 내용'이 붙여질 수 있다. 붙여넣기가 반영될 시간을 더 준다.
    time.sleep(0.4)
    if old is not None:
        try:
            subprocess.run(["pbcopy"], input=old, text=True, timeout=5)
        except Exception:
            pass


# 접근성 요소를 '이름'으로 클릭 — 픽셀 좌표 추측 대신 라벨 매칭(레티나·레이아웃 무관, 안 깨짐).
# ★구현 핵심(여러 시도로 검증): ①요소별 직접읽기(description/name) — 배치(description of kids)는 한 요소만
# 깨져도 전체가 빈값. ②★진짜 BFS(큐) — 같은 깊이를 전부 본 뒤 다음 깊이로. DFS는 첫 자식(거대한 노트목록/
# 채팅목록)으로 깊게 파고들어 툴바·탭 버튼 도달 전 cap 초과→타임아웃. BFS면 그 버튼들이 ~14노드/0.5초에 잡힘.
# ③entire contents 금지(앱 따라 멈춤/빈값). ④AppleScript {} 때문에 .format 금지→.replace.
_AX_CLICK_TMPL = (
    'property cnt : 0\n'
    'set TARGET to "{target}"\n'
    'on lblOf(k)\n'
    '  set t to ""\n'
    '  tell application "System Events"\n'
    '    try\n      set t to description of k\n    end try\n'
    '    if (t is missing value) or (t is "") then\n'
    '      try\n        set t to name of k\n      end try\n'
    '    end if\n'
    '  end tell\n'
    '  if t is missing value then set t to ""\n'
    '  return t\n'
    'end lblOf\n'
    'on kidsOf(el)\n'
    '  set ks to {}\n'
    '  tell application "System Events"\n'
    '    try\n      set ks to UI elements of el\n    end try\n'
    '  end tell\n'
    '  return ks\n'
    'end kidsOf\n'
    'on bfs(root, target)\n'
    '  set q to {root}\n'
    '  repeat while (count of q) > 0\n'
    '    if cnt > 70 then return false\n'
    '    set el to item 1 of q\n'
    '    if (count of q) > 1 then\n'
    '      set q to items 2 thru -1 of q\n'
    '    else\n'
    '      set q to {}\n'
    '    end if\n'
    '    set kids to my kidsOf(el)\n'
    '    repeat with k in kids\n'
    '      set cnt to cnt + 1\n'
    '      set t to my lblOf(k)\n'
    '      if (t is not "") and (t contains target) then\n'
    '        tell application "System Events"\n'
    '          try\n            click k\n            return true\n          end try\n'
    '          try\n            perform action "AXPress" of k\n            return true\n          end try\n'
    '        end tell\n'
    '      end if\n'
    '      set q to q & {k}\n'
    '    end repeat\n'
    '  end repeat\n'
    '  return false\n'
    'end bfs\n'
    'tell application "System Events"\n'
    '  set cnt to 0\n'
    '  set fp to first application process whose frontmost is true\n'
    '  set w to missing value\n'
    '  try\n    set w to front window of fp\n  end try\n'
    'end tell\n'
    'set ok to false\n'
    'if w is not missing value then set ok to my bfs(w, TARGET)\n'
    'if ok then\n  return "OK"\nelse\n  return "NOT_FOUND"\nend if\n'
)


def _ax_click(label: str) -> ToolResult:
    osa = shutil.which("osascript")
    if not osa or not label:
        return ToolResult("failed", "ax", "osascript 없음/라벨 없음", target_label=label)
    safe = label.replace("\\", "\\\\").replace('"', '\\"')
    try:
        r = subprocess.run([osa, "-e", _AX_CLICK_TMPL.replace("{target}", safe)],
                           capture_output=True, text=True, timeout=5)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if "OK" in out:
            return ToolResult("success", "ax", "AX element clicked", target_label=label)
        if "NOT_FOUND" in out:
            return ToolResult("failed", "ax", "접근성에 그 라벨 없음", target_label=label, raw=out[:200])
        return ToolResult("failed", "ax", out[:200], target_label=label, raw=out)
    except Exception as e:
        return ToolResult("failed", "ax", f"시간초과/오류: {str(e)[:80]}", target_label=label)


def _choose_ocr(scored: list, near: Optional[tuple]):
    """OCR 후보(scored=[(score,item)] 내림차순)에서 클릭 대상 선택 — *순수함수*(클릭 없음, 테스트가능).
    ★near-respect(네이버 실증 버그 수정): near=(x,y)가 있으면 *정확/부분 가리지 말고* 강한 후보 중
      가장 가까운 것을 고른다. (이전엔 exact-priority가 near를 무시 → 멀리 있는 정확일치(사이드바/지도라벨)를
      가까운 부분일치(필드버튼 '길찾기 >'/리스트항목)보다 우선해 엉뚱한 곳을 눌렀다.)
    near가 없을 때만 완전일치 우선 → 동점이면 ('ambiguous', …)로 near 요청.
    반환: ('pick', (score,item), by_near) | ('ambiguous', contenders) | ('none', seen_str)."""
    strong = [(s, it) for s, it in scored if s >= 0.6]
    if not strong:
        seen = ", ".join(f"'{it['text'][:14]}'" for _, it in scored[:6])
        return ("none", seen)
    has_near = bool(near) and near[0] is not None and near[1] is not None
    if has_near:
        nx, ny = near
        pick = min(strong, key=lambda c: (c[1]["cx"] - nx) ** 2 + (c[1]["cy"] - ny) ** 2)
        return ("pick", pick, True)
    exacts = [(s, it) for s, it in strong if s >= 0.999]
    if exacts:
        contenders = exacts
    else:
        top = strong[0][0]
        contenders = [(s, it) for s, it in strong if top - s <= 0.03]
    if len(contenders) >= 2:
        return ("ambiguous", contenders)
    return ("pick", contenders[0], False)


def _ocr_click(label: str, near: Optional[tuple] = None) -> ToolResult:
    """화면의 *보이는 글자* 로 클릭 — ax_click(접근성)이 못 찾는 라벨용. 픽셀 추측 없음.
    좌표는 ocrground(Swift)가 논리포인트로 완결 변환 → 그대로 pyautogui 클릭.
    near=(x,y): 두뇌가 화면에서 본 대략 위치 → 정확/부분 안 가리고 *가장 가까운* 강한 후보 선택(near-respect)."""
    from . import ocr
    if not label:
        return ToolResult("failed", "ocr", "라벨 없음")
    if not ocr.available():
        return ToolResult("failed", "ocr", "ocrground 미빌드(swiftc -O ocrground.swift -o ocrground)", target_label=label)
    try:
        items = ocr.ocr_screen(front_window_only=_ISOLATE_OCR)   # 격리 시 맨앞 창만(다른 창 헛클릭 방지)
    except ocr.OcrError as e:
        return ToolResult("failed", "ocr", f"OCR 실패({e})", target_label=label)
    if not items:
        return ToolResult("failed", "ocr", "화면에서 글자를 하나도 못 읽음(권한?)", target_label=label)
    scored = ocr.match(items, label)
    res = _choose_ocr(scored, near)
    if res[0] == "none":
        return ToolResult("failed", "ocr", f"그 글자 화면에 없음(읽힌 것: {res[1]}...)", target_label=label)
    if res[0] == "ambiguous":
        contenders = res[1]
        cands = ", ".join(f"'{it['text'][:12]}'@({int(it['cx'])},{int(it['cy'])})" for _, it in contenders[:5])
        return ToolResult("ambiguous", "ocr", f"같은 후보 {len(contenders)}개: {cands}", target_label=label)
    (top_s, top_it), by_near = res[1], res[2]
    import pyautogui
    pyautogui.PAUSE = 0.4
    pyautogui.FAILSAFE = True
    cx, cy = int(top_it["cx"]), int(top_it["cy"])
    pyautogui.click(cx, cy)
    tag = " [near로 선택]" if by_near else ""
    return ToolResult(
        "success", "ocr",
        f"clicked text='{top_it['text'][:20]}' score={top_s:.2f} @{cx},{cy}{tag}",
        target_label=label,
        confidence=float(top_s),
        bbox=(cx, cy, cx, cy),
    )


def _ground_click(query: str) -> ToolResult:
    """화면의 요소를 *말(설명)으로* 찾아 클릭 — 글자도 접근성 라벨도 없는 비표준 아이콘용
    (예: 카톡 친구탭). Qwen2.5-VL 그라운딩(느림 ~수초). 좌표는 ground.py가 논리pt로 완결."""
    from . import ground
    query = (query or "").strip()[:200]   # 길이 캡: 화면본문이 묘사로 흘러들어 프롬프트 인젝션되는 것 차단(RUBI)
    if not query:
        return ToolResult("failed", "ground", "설명 없음")
    if not ground.available():
        return ToolResult("failed", "ground", "Qwen/torch 미설치", target_label=query)
    try:
        r = ground.ground_cached(query)   # 캐시 우선(지문검증) → 실패시 VLM, 결과 캐시 저장
    except Exception as e:
        return ToolResult("failed", "ground", f"그라운딩 오류({str(e)[:80]})", target_label=query)
    if not r:
        return ToolResult("failed", "ground", "그 요소 못 찾음", target_label=query)
    import pyautogui
    pyautogui.PAUSE = 0.4
    pyautogui.FAILSAFE = True
    pyautogui.click(r["lx"], r["ly"])
    via = "캐시⚡" if r.get("via") == "cache" else "VLM"
    sc = f" score={r['score']}" if r.get("score") is not None else ""
    return ToolResult(
        "success", "ground", f"clicked @({r['lx']},{r['ly']}) [{via}{sc}]",
        target_label=query,
        confidence=float(r["score"]) if r.get("score") is not None else None,
        bbox=(int(r["lx"]), int(r["ly"]), int(r["lx"]), int(r["ly"])),
    )


def _snapshot_click(label: str, near: Optional[tuple] = None) -> ToolResult:
    """현재 관찰 스냅샷의 AX/OCR 후보 좌표로 클릭한다.

    비용 절감 경로: 방금 관찰하며 이미 뽑은 후보를 재사용하므로 AX/OCR을 다시 돌리지 않는다.
    스냅샷은 agent가 행동 성공 뒤 바로 비우므로 낡은 좌표 재사용을 줄인다.
    """
    if not label:
        return ToolResult("failed", "snapshot", "라벨 없음")
    try:
        from . import state_snapshot

        hit = state_snapshot.find_click_target(label, near=near)
    except Exception as e:
        return ToolResult("failed", "snapshot", f"스냅샷 조회 실패({type(e).__name__})", target_label=label)
    status = hit.get("status")
    if status == "none":
        return ToolResult("failed", "snapshot", hit.get("reason", "스냅샷 후보 없음"),
                          target_label=label, raw=hit.get("seen", ""))
    if status == "ambiguous":
        seen = hit.get("seen", "")
        return ToolResult("ambiguous", "snapshot", f"스냅샷 후보 {len(hit.get('candidates') or [])}개: {seen}",
                          target_label=label, raw=seen)
    if status != "pick":
        return ToolResult("failed", "snapshot", f"알 수 없는 스냅샷 판정: {status}", target_label=label)
    cand = hit.get("candidate") or {}
    try:
        cx, cy = int(cand["cx"]), int(cand["cy"])
    except Exception:
        return ToolResult("failed", "snapshot", "스냅샷 후보 좌표 없음", target_label=label, raw=str(cand)[:200])
    import pyautogui
    pyautogui.PAUSE = 0.4
    pyautogui.FAILSAFE = True
    pyautogui.click(cx, cy)
    source = cand.get("source") or "snapshot"
    text = cand.get("label") or cand.get("text") or label
    tag = " [near로 선택]" if hit.get("by_near") else ""
    return ToolResult(
        "success", "snapshot",
        f"clicked {source}='{str(text)[:20]}' score={hit.get('score')} @{cx},{cy} "
        f"from {hit.get('snapshot_id')}{tag}",
        target_label=label,
        confidence=float(hit.get("score") or 0.0),
        bbox=(cx, cy, cx, cy),
    )


def _smart_click(target: str, near=None) -> ToolResult:
    """★통합 클릭 — 두뇌는 '무엇을'만 말하면 도구가 '어떻게'를 자동 선택(computer-use 같은 한 번 호출 느낌).
    AX가 풍부한 native 앱은 AX 먼저 → 보이는 글자는 OCR → 마지막에 grounding(비전)으로 흡수.
    두뇌가 티어 선택·중복 해결·좌표를 신경 쓸 필요가 없다."""
    target = (target or "").strip()
    if not target:
        return ToolResult("failed", "smart", "대상 없음")
    # 0) State snapshot — 현재 관찰에서 이미 뽑은 AX/OCR 후보를 재사용한다.
    snap = _snapshot_click(target, near=near)
    if snap.ok:
        snap.method = "smart/snapshot"
        _note_perception_tier("snapshot", True, target)
        return snap
    if snap.status == "ambiguous":
        snap.method = "smart/snapshot"
        return snap
    preferred = _preferred_perception_tier(target)
    ground_attempted = False
    ocr_attempted = False
    if preferred == "ground":
        g = _try_ground(target)
        ground_attempted = True
        if g.ok:
            return g
        if g.status == "ambiguous":
            return g
        preferred = ""
    if preferred == "ocr":
        r = _ocr_click(target, near=near)
        ocr_attempted = True
        _note_perception_tier("ocr", r.ok, target)
        if r.ok:
            r.method = "smart/ocr"
            return r
        if r.status == "ambiguous":
            return r
        preferred = ""
    # 1) AX — native 앱의 구조적 신호가 충분하면 가장 싸고 정확하다.
    # near가 있으면 중복 후보를 화면 위치로 해소해야 하므로 AX를 건너뛰고 OCR/ground로 간다.
    if near is None and preferred not in {"ocr", "ground"}:
        try:
            from . import perception_policy
            if perception_policy.should_try_ax(target):
                ax = _ax_click(target)
                perception_policy.note_axis_result(ax.ok, target)
                perception_policy.note_tier_result("ax", ax.ok, target)
                if ax.ok:
                    ax.method = "smart/ax"
                    return ax
        except Exception:
            pass
    # 2) OCR — 보이는 글자면 빠르게
    if not ocr_attempted:
        r = _ocr_click(target, near=near)
        _note_perception_tier("ocr", r.ok, target)
        if r.ok:
            r.method = "smart/ocr"
            return r
        if r.status == "ambiguous":
            return r
    # 3) grounding(비전) — AX/OCR 실패를 자동 흡수
    if ground_attempted:
        return ToolResult("failed", "smart", "ground 정책 우선 시도 실패, OCR도 실패 → 다른 묘사/스크롤 후 재시도",
                          target_label=target, raw=r.summary())
    return _try_ground(target, ocr_summary=r.summary())


def _try_ground(target: str, ocr_summary: str = "") -> ToolResult:
    from . import ground
    if not ground.available():
        _note_perception_tier("ground", False, target)
        return ToolResult("failed", "smart", "AX/OCR 실패, 비전 미설치 → 더 또렷한 라벨/픽셀",
                          target_label=target, raw=ocr_summary)
    r2 = _ground_click(target)
    _note_perception_tier("ground", r2.ok, target)
    if r2.ok:
        r2.method = "smart/ground"
        return r2
    return ToolResult("failed", "smart", "ocr·ground 둘 다 못 찾음 → 다른 묘사/스크롤 후 재시도",
                      target_label=target, raw=f"ocr={ocr_summary} / ground={r2.summary()}")


def _preferred_perception_tier(target: str) -> str:
    try:
        from . import perception_policy

        return perception_policy.preferred_tier(target)
    except Exception:
        return ""


def _note_perception_tier(method: str, ok: bool, target: str) -> None:
    try:
        from . import perception_policy

        perception_policy.note_tier_result(method, ok, target)
    except Exception:
        pass


_SAFE_SHELL_PREFIXES = (
    ("open",),
    ("osascript",),
    ("pbcopy",),
    ("pbpaste",),
    ("screencapture",),
    ("sips",),
    ("mdls",),
    ("sleep",),
)


def _shell_allowed(command: str) -> tuple[bool, str]:
    """raw shell 대신 안전 primitive 성격의 명령만 허용한다.

    모델이 생성한 셸은 CUA에서 가장 위험한 도구라서, 복합 shell 문법과 임의 명령은 막는다.
    """
    command = (command or "").strip()
    if not command:
        return False, "empty shell command"
    if any(tok in command for tok in (";", "&&", "||", "|", "$(", "`", ">", "<")):
        return False, "compound shell syntax is blocked"
    try:
        parts = shlex.split(command)
    except ValueError as e:
        return False, f"shell parse failed: {e}"
    if not parts:
        return False, "empty shell command"
    if any(tuple(parts[: len(prefix)]) == prefix for prefix in _SAFE_SHELL_PREFIXES):
        return True, ""
    return False, f"shell command '{parts[0]}' is not in safe primitive allowlist"


def execute(action: Action) -> ToolResult:
    """행동 실행. 모든 결과는 ToolResult 로 반환한다."""
    a = action.action

    ok, reason = action.validates()
    if not ok:
        return ToolResult("failed", a, reason, target_label=action.target_label)

    if a == "shell":
        allowed, reason = _shell_allowed(action.command or "")
        if not allowed:
            return ToolResult("blocked", "shell", reason, raw=action.command or "")
        try:
            r = subprocess.run(shlex.split(action.command or ""), capture_output=True,
                               text=True, timeout=20)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            status = "success" if r.returncode == 0 else "failed"
            return ToolResult(status, "shell", out[:600] if out else "(출력 없음)", raw=out)
        except Exception as e:
            return ToolResult("failed", "shell", f"shell 오류: {e}", raw=action.command or "")

    if a == "open_app":   # ★앱 열기/활성화 — 번들ID·osascript·mdfind 맨손검색 대신 우리 파인더 한 방
        from . import perceive
        name = (action.target_label or action.text or "").strip()
        found = perceive._find_app_bundle(name)
        if perceive.open_app(name):
            bid = found[1] if found else ""
            return ToolResult("success", "open_app", f"'{name}' 열림{(' ('+bid+')') if bid else ''}",
                              target_label=name)
        return ToolResult("failed", "open_app",
                          f"'{name}' 앱을 찾지 못함 — 이름을 바꿔 다시 시도하거나 설치 여부 확인",
                          target_label=name)

    if a == "ax_click":   # 접근성 라벨로 요소 클릭 (픽셀보다 우선)
        return _ax_click(action.target_label or action.text or "")

    if a == "ocr_click":  # 화면에 보이는 '글자'로 클릭 (ax_click 실패 시, 픽셀보다 우선)
        near = (action.x, action.y) if action.x is not None else None
        return _ocr_click(action.target_label or action.text or "", near=near)

    if a == "ground_click":  # 글자도 라벨도 없는 요소를 '말(설명)'로 찾아 클릭 (픽셀 직전 최후)
        return _ground_click(action.target_label or action.text or "")

    if a == "smart_click":   # ★통합: ocr→ground 자동 캐스케이드 (두뇌는 '무엇을'만)
        near = (action.x, action.y) if action.x is not None else None
        return _smart_click(action.target_label or action.text or "", near=near)

    import pyautogui  # 지연 임포트
    pyautogui.PAUSE = 0.4
    pyautogui.FAILSAFE = True

    # ★좌표: perceive 가 스샷을 '논리 화면 크기'로 정렬해 두므로, 모델이 준 (x,y)는 이미
    #   pyautogui 논리좌표와 같은 공간이다 → 추가 변환 없이 그대로 클릭하면 정확히 꽂힌다.
    if a == "move":
        pyautogui.moveTo(action.x, action.y, duration=0.3)
    elif a == "click":
        pyautogui.click(action.x, action.y) if action.x is not None else pyautogui.click()
    elif a == "double_click":
        # ★pyautogui.doubleClick 금지 — 두 단일클릭이라 clickState=2 가 안 실려 맥 앱이
        #   더블클릭으로 인식 못한다(실측: 카톡 채팅방이 안 열림). drive.py 와 동일한 Quartz 구현.
        x, y = (action.x, action.y) if action.x is not None else pyautogui.position()
        _quartz_double_click(x, y)
    elif a == "right_click":
        # 컨텍스트 메뉴(예: 카톡 프로필 우클릭 → '나와의 채팅')용. 단일클릭이라 pyautogui 로 충분.
        pyautogui.rightClick(action.x, action.y) if action.x is not None else pyautogui.rightClick()
    elif a == "type":
        _paste_text(action.text or "")   # 한글 포함 유니코드 확실히 입력
    elif a == "key":
        keys = _norm_keys(action.keys or "")
        if len(keys) > 1:
            pyautogui.hotkey(*keys)
        elif keys:
            pyautogui.press(keys[0])
    elif a == "scroll":
        pyautogui.scroll(action.amount or 0)
    elif a == "wait":
        time.sleep((action.amount or 500) / 1000)
    return ToolResult("success", a, "executed", target_label=action.target_label)
