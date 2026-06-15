"""화면 인지 — 스크린샷 캡처 + 좌표계 정렬 (computer-use 이식의 핵심).

★레티나 문제: macOS `screencapture` 는 물리픽셀(예 3024px)을 뱉지만, pyautogui.click 은
논리좌표(예 1512px, 레티나면 물리의 1/2)로 동작한다. 모델이 물리해상도 스샷을 보고 좌표를 주면
클릭이 약 2배 어긋난다 — 이게 (구) Sapphi 가 GUI 클릭을 한 번도 못 맞춘 *구조적* 원인이었다.

→ 해결(computer-use 가 내부적으로 하는 것과 동일): 캡처 직후 스샷을 '논리 화면 크기'로 리사이즈해서
   모델이 보는 좌표계 == pyautogui 클릭 좌표계로 만든다. 그러면 좌표 변환 없이 클릭이 정확히 꽂힌다.
   (부수효과: 풀해상도→논리크기 다운스케일이라 전송 바이트·비용도 함께 절감.)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

_FALLBACK_MAX_EDGE = 1568  # 논리 화면 크기를 못 구할 때만 쓰는 비용절감용 상한


def _logical_size() -> tuple[int, int] | None:
    """pyautogui 가 클릭에 쓰는 논리 화면 크기. 레티나면 물리 해상도의 1/2."""
    try:
        import pyautogui
        w, h = pyautogui.size()
        return int(w), int(h)
    except Exception:
        return None


def _png_size(path: str) -> tuple[int, int] | None:
    sips = shutil.which("sips")
    if not sips:
        return None
    try:
        out = subprocess.run([sips, "-g", "pixelWidth", "-g", "pixelHeight", path],
                             capture_output=True, text=True, timeout=10).stdout
        w = h = None
        for line in out.splitlines():
            if "pixelWidth" in line:
                w = int(line.split(":")[-1])
            elif "pixelHeight" in line:
                h = int(line.split(":")[-1])
        return (w, h) if w and h else None
    except Exception:
        return None


def _resize(path: str, w: int | None = None, h: int | None = None,
            max_edge: int | None = None) -> bool:
    """sips 리사이즈. ★성공 여부를 반환한다(returncode 검사) — 조용히 삼키면
    정렬 실패가 안 드러나 레티나 버그가 무음 재발하기 때문(RUBI 지적)."""
    sips = shutil.which("sips")
    if not sips:
        return False
    try:
        if w and h:
            r = subprocess.run([sips, "-z", str(h), str(w), path], capture_output=True, timeout=10)
        elif max_edge:
            r = subprocess.run([sips, "-Z", str(max_edge), path], capture_output=True, timeout=10)
        else:
            return False
        return r.returncode == 0
    except Exception:
        return False


def _align(path: str) -> bool:
    """스샷을 논리좌표 크기로 맞춘다(=좌표 정렬). 성공 시 True.
    ★실패를 조용히 넘기지 않는다: 정렬이 깨지면 모델좌표 != 클릭좌표가 되어 레티나 2x
    어긋남이 *무음으로* 재현되므로(=고치려던 그 버그), 결과 크기를 검증하고 어긋나면 경고한다."""
    import sys
    size = _logical_size()
    if not size:
        print("[perceive] ⚠️ 논리 화면크기 불명 — 좌표 정렬 불가(클릭 어긋남 위험). pyautogui 확인 필요.",
              file=sys.stderr)
        _resize(path, max_edge=_FALLBACK_MAX_EDGE)
        return False
    ok = _resize(path, w=size[0], h=size[1])   # 모델 좌표계 == pyautogui 좌표계
    actual = _png_size(path)
    if not ok or not actual or abs(actual[0] - size[0]) > 2 or abs(actual[1] - size[1]) > 2:
        print(f"[perceive] ⚠️ 좌표 정렬 실패: 스샷 {actual} != 논리 {size} — 클릭이 어긋날 수 있다 "
              "(sips 실패/권한 등). 모델 좌표를 신뢰하지 마라.", file=sys.stderr)
        return False
    return True


def _front_window():
    """맨 앞(z-order 최상위) 일반 창의 (ownerName, ownerPID). 없으면 (None,None).
    ★Quartz *라이브* 조회 — NSWorkspace.frontmostApplication() 은 runloop 없는 스크립트에서 *stale*(갱신 안 됨)이라
      open -b 로 앱을 앞에 올려도 옛 앱을 반환한다(실측 버그 — front_guard 가 거짓 차단). Quartz 는 항상 현재값."""
    try:
        import Quartz
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID)
        for w in wins:   # 앞→뒤 z-order. 첫 layer-0(일반) 창의 owner = 맨앞 앱
            if w.get("kCGWindowLayer", 0) == 0:
                return (w.get("kCGWindowOwnerName"), int(w.get("kCGWindowOwnerPID", -1)))
    except Exception:
        pass
    return (None, None)


def frontmost_app() -> str | None:
    """현재 맨 앞 앱의 이름(Quartz 라이브). 앱-격리 스샷 대상 자동지정·front 가드용."""
    name, _ = _front_window()
    if name:
        return name
    try:   # 폴백: NSWorkspace(stale 가능)
        from AppKit import NSWorkspace
        a = NSWorkspace.sharedWorkspace().frontmostApplication()
        return a.localizedName() if a else None
    except Exception:
        return None


def _norm_app(s: str) -> str:
    """앱 이름 비교용 정규화 — NFC + 소문자 + *공백 전부 제거*.
    ★공백 한 칸이 매칭을 깨뜨리는 실측 버그 차단: 두뇌/TaskSpec 의 "네이버지도"(공백X) vs
      실제 앱 이름 "네이버 지도"(공백O) → 공백 제거하면 둘 다 "네이버지도" 로 일치."""
    import unicodedata
    return "".join(unicodedata.normalize("NFC", str(s or "")).lower().split())


def _tokens(s: str) -> list[str]:
    """앱 쿼리를 토큰으로 — 구분자(_ . - /)를 공백처럼 쪼개고 복수형 s 를 stem.
    ★TaskSpec 이 만든 슬러그('naver_maps'·'naver.maps')를 실제 앱(번들 com.nhncorp.naverMAP)에 잇는 핵심:
      ['naver','map'] 토큰이 *전부* 앱 이름/번들/경로에 있으면 매칭(naver 검색앱은 'map' 없어 안 걸림)."""
    import unicodedata
    import re
    t = unicodedata.normalize("NFC", str(s or ""))
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t)   # camelCase 분리: NaverMap → Naver Map
    t = t.lower()
    for ch in ("_", ".", "-", "/"):
        t = t.replace(ch, " ")
    out = []
    for w in t.split():
        w = w.rstrip("s") if len(w) > 3 else w   # 단순 복수형 stem(짧은 단어 제외)
        if len(w) >= 2:
            out.append(w)
    return out


def _resolve_pid(app_query: str) -> int | None:
    """앱 이름/번들ID 쿼리를 실행 중 앱의 PID 로 해석한다(공백 무시 + *최적* 매칭 점수).
    ★단순 '첫 부분일치'의 두 함정을 피한다:
      ①공백("네이버지도"≠"네이버 지도") → _norm_app 으로 공백 제거.
      ②느슨한 역매칭("naver" in "navermap" → NaverMap 쿼리가 NAVER 검색앱에 걸림) →
        정확일치(100) > 쿼리⊂이름·번들·경로(50) > 이름⊂쿼리(짧은 이름 제외, 20) 점수로 *가장 잘 맞는* 앱 채택."""
    try:
        from AppKit import NSWorkspace
    except Exception:
        return None
    q = _norm_app(app_query)
    if not q:
        return None
    best_pid, best_score = None, 0
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        name = _norm_app(a.localizedName() or "")
        bid = (a.bundleIdentifier() or "").lower().replace(" ", "")
        path = (a.bundleURL().path().lower().replace(" ", "") if a.bundleURL() else "")
        qtoks = _tokens(app_query)
        score = 0
        if name and name == q:
            score = 100                                   # 이름 정확일치(공백무시)
        elif q in name or q in bid or q in path:
            score = 50                                    # 쿼리가 이름/번들/경로에 들어감(forward)
        elif len(qtoks) >= 2:
            # ★다중 토큰 슬러그('naver_maps')는 *모든 토큰*이 이름/번들/경로에 있어야 매칭.
            #   부분 단어 역매칭 금지 — 'naver'만으론 NAVER 검색앱에 안 걸리고 'map'까지 있는 NaverMap만 잡힘.
            hay = f"{name} {bid} {path}"
            if all(t in hay for t in qtoks):
                score = 35
        elif name and len(name) >= 4 and name in q:
            score = 20                                    # 단일 토큰일 때만 역매칭(쿼리에 군더더기 단어가 붙은 경우)
        if score == 0:
            continue
        # ★헬퍼 오염 방지: Regular 앱(Dock에 뜨는 본체, activationPolicy 0)을 헬퍼보다 우선.
        if int(a.activationPolicy()) == 0:
            score += 5
        if score > best_score:
            best_score, best_pid = score, int(a.processIdentifier())
    return best_pid


_APP_DIRS = [
    "/Applications", "/Applications/Utilities", "/System/Applications",
    "/System/Applications/Utilities", os.path.expanduser("~/Applications"),
]


def _bundle_id_of(app_path: str) -> str | None:
    """`.app` 경로의 번들ID(mdls). `open -b 번들ID` 는 한글·공백 이름도 안 깨지므로 실행에 이게 제일 안전."""
    ml = shutil.which("mdls")
    if not ml or not app_path:
        return None
    try:
        out = subprocess.run([ml, "-name", "kMDItemCFBundleIdentifier", "-raw", app_path],
                             capture_output=True, text=True, timeout=8).stdout.strip()
        return out if out and out != "(null)" else None
    except Exception:
        return None


def _find_app_bundle(app_query: str):
    """설치된 앱을 *실행 중이 아니어도* fuzzy 이름으로 찾아 (path, bundle_id) 반환. 공백/영한 무관.
    ★'앱을 못 연다'의 핵심 수정: _resolve_pid 는 실행 중 앱만 본다 → 안 떠 있으면 영영 못 찾음.
      여기서 ①실행 중 → ②/Applications 등 디스크 스캔(.app 이름 공백무시 매칭) → ③mdfind(Spotlight) 순으로 찾는다."""
    q = _norm_app(app_query)
    if not q:
        return None
    # ① 실행 중이면 그 번들(가장 확실 — 이미 떠 있음)
    try:
        from AppKit import NSWorkspace
        pid = _resolve_pid(app_query)
        if pid is not None:
            for a in NSWorkspace.sharedWorkspace().runningApplications():
                if int(a.processIdentifier()) == pid and a.bundleURL():
                    return (a.bundleURL().path(), a.bundleIdentifier())
    except Exception:
        pass
    # ② 앱 디렉터리 스캔 — .app 파일명을 공백무시로 매칭. 정확(100)·forward(50)만 쓴다.
    #   ★약한 역매칭(이름⊂쿼리)은 *제외*: "naver"⊂"navermap" 으로 NAVER 검색앱을 *잘못 실행*하던 버그.
    #   못 찾느니만 못한 오실행을 막고, 영어/번들 매칭은 아래 ③ mdfind(번들ID)로 정확히 처리한다.
    best = None   # (score, path)
    for d in _APP_DIRS:
        try:
            entries = os.listdir(d)
        except Exception:
            continue
        for entry in entries:
            if not entry.endswith(".app"):
                continue
            base = _norm_app(entry[:-4])
            score = 100 if base == q else (50 if q in base else 0)
            if score and (best is None or score > best[0]):
                best = (score, os.path.join(d, entry))
    # ③ mdfind 폴백 — 디스크 이름이 안 맞을 때(영어쿼리↔한글앱). *번들ID*와 파일명 둘 다로 찾는다.
    #   예: "NaverMap"→번들 com.nhncorp.NaverMap→/Applications/네이버 지도.app (이름은 한글이라 ②서 못 잡힘).
    if best is None:
        md = shutil.which("mdfind")
        if md:
            exprs = [f'kMDItemCFBundleIdentifier == "*{q}*"c',
                     f'kMDItemFSName == "*{str(app_query).strip()}*"cd']
            toks = _tokens(app_query)              # 슬러그 'naver_maps' → 번들에 'naver'·'map' *둘 다* 포함
            if len(toks) >= 2:
                exprs.insert(0, " && ".join(f'kMDItemCFBundleIdentifier == "*{t}*"c' for t in toks))
            for expr in exprs:
                try:
                    out = subprocess.run(
                        [md, f'kMDItemContentType == "com.apple.application-bundle"c && ({expr})'],
                        capture_output=True, text=True, timeout=8).stdout
                except Exception:
                    continue
                cands = [ln.strip() for ln in out.splitlines() if ln.strip().endswith(".app")]
                if cands:
                    # /Applications 정식 경로 우선(내부 Wrapper·캐시 .app 경로보다) — open <path> 폴백 안전.
                    apps = [p for p in cands if p.startswith("/Applications/")]
                    best = (40, apps[0] if apps else cands[0])
                    break
    if best is None:
        return None
    return (best[1], _bundle_id_of(best[1]))


def open_app(app_query: str) -> bool:
    """앱을 fuzzy 이름으로 *연다/전면화한다* — 실행 중이면 활성화, 아니면 디스크에서 찾아 실행.
    `open -b 번들ID`(이름 무관) 우선, 실패 시 경로 open, 최후에 `open -a 이름`."""
    found = _find_app_bundle(app_query)
    if found:
        path, bid = found
        try:
            if bid:
                r = subprocess.run(["open", "-b", bid], capture_output=True, timeout=10)
                if r.returncode == 0:
                    return True
            r = subprocess.run(["open", path], capture_output=True, timeout=10)
            if r.returncode == 0:
                return True
        except Exception:
            pass
    try:   # 최후 폴백: 이름으로(영어 앱이면 통함)
        r = subprocess.run(["open", "-a", str(app_query)], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def focus_app(app_query: str) -> bool:
    """대상 앱을 *전면화(activate)* 한다 — 매 조작 직전 호출해 *항상 그 창에서* 동작하게 한다.
    ①NSRunningApplication.activate(빠름) 시도 → ②Quartz 로 *실제* 전면화 검증 → 안 됐으면 ③open -b(실측 확실).
    (activateWithOptions_ 는 백그라운드 프로세스에서 간헐 무효 → 반드시 검증 후 open_app 폴백.)"""
    try:
        from AppKit import NSWorkspace, NSApplicationActivateIgnoringOtherApps
        pid = _resolve_pid(app_query)
        if pid is not None:
            for a in NSWorkspace.sharedWorkspace().runningApplications():
                if int(a.processIdentifier()) == pid:
                    a.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                    break
    except Exception:
        pass
    if is_frontmost(app_query):
        return True
    return open_app(app_query)   # open -b 번들ID — 전면화 확실(미실행이면 실행까지)


def is_frontmost(app_query: str) -> bool:
    """현재 맨 앞 창의 owner PID == app_query 의 PID 인지 (Quartz 라이브 — NSWorkspace stale 회피)."""
    target = _resolve_pid(app_query)
    if target is None:
        return False
    _, front_pid = _front_window()
    return front_pid == target


def _find_app_windows(app_query: str):
    """그 앱이 소유한 *모든* normal(layer 0) 창의 [(winID,x,y,w,h), ...] 논리, **뒤→앞 z-order**.
    ★카톡처럼 채팅방을 *별도 창*으로 여는 앱: 가장 큰 창 하나만 보면 새로 열린
      채팅방 창을 영영 못 본다(메인목록이 더 커서 항상 목록만 캡처됨 — 실측 무한루프 원인).
      → 모든 창을 합성해 브레인이 *화면에 실제 보이는 그대로*(목록+채팅방) 보게 한다.
    반환 순서는 뒤(바닥)부터라 그대로 paste 하면 앞 창이 위에 덮인다(=실제 화면과 동일)."""
    try:
        import Quartz
        import unicodedata
    except Exception:
        return []
    # OnScreenOnly = 앞→뒤 z-order. paste 는 뒤→앞이라야 겹침이 맞으므로 뒤집는다.
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    q = _norm_app(app_query)
    target_pid = _resolve_pid(app_query)
    out = []
    for w in wins:
        if w.get("kCGWindowLayer", 0) != 0:
            continue
        owner = _norm_app(w.get("kCGWindowOwnerName") or "")
        pid_match = target_pid is not None and int(w.get("kCGWindowOwnerPID", -1)) == target_pid
        if not pid_match and q and q not in owner:
            continue
        b = w.get("kCGWindowBounds", {})
        ww, wh = int(b.get("Width", 0)), int(b.get("Height", 0))
        if ww < 120 or wh < 120:
            continue
        out.append((w.get("kCGWindowNumber"), int(b.get("X", 0)), int(b.get("Y", 0)), ww, wh))
    out.reverse()   # 뒤(바닥)부터 → paste 하면 앞 창이 위에 덮임
    return out


def _is_mostly_black(img) -> bool:
    """*거의 순흑*(평균<3 AND 분산<3)인지. 다크모드(분산 큼)와 구분 — ocr._assert_not_blank 와 동일 로직.
    wrapped iOS앱은 `screencapture -l` 이 간헐적으로 순흑을 뱉어 → 이걸로 잡아 크롭 폴백으로 전환."""
    try:
        from PIL import ImageStat
        st = ImageStat.Stat(img.convert("L"))
        return st.mean[0] < 3.0 and st.stddev[0] < 3.0
    except Exception:
        return False


def _isolate_capture(path: str, app_query: str) -> bool:
    """★앱-격리 스샷(computer-use 근사): 그 앱의 *모든 창*을 잡아 *검정 전체캔버스의 원위치에 합성* →
    브레인은 그 앱만(목록+채팅방 등 전부), 좌표는 전역 그대로. 창 하나도 없으면 False.
    ★다중창(2026-06-10): 카톡은 채팅방을 별도 창으로 연다 — 가장 큰 창 하나만 보면(_find_window) 새 채팅방을
      영영 못 봐 무한루프(실측). _find_app_windows 로 *전부* 뒤→앞 순서로 합성한다.
    ★창별 캡처 2단계(native 깜빡임 대응): ①`-l<창ID>`(겹친 창 무시) → *순흑이면* ②전체스샷 크롭 폴백.
      모든 창이 다 순흑이면(진짜 안 보임) False → screenshot 이 복구·전체화면 폴백으로."""
    sc = shutil.which("screencapture")
    if not sc:
        return False
    wins = _find_app_windows(app_query)
    if not wins:
        return False
    size = _logical_size()
    if not size:
        return False
    LW, LH = size
    try:
        from PIL import Image
        # 레티나 scale 은 첫 유효창의 -l 캡처폭÷논리폭으로 실측(전 창 동일 디스플레이 가정).
        scale = 2.0
        canvas = None
        full_img = None       # 크롭 폴백용 전체스샷(필요할 때 1회만)
        any_pasted = False
        tmp = "/tmp/sapphi_win_iso.png"
        for wid, wx, wy, ww, wh in wins:
            win = None
            r = subprocess.run([sc, f"-l{wid}", "-o", "-x", "-t", "png", tmp], capture_output=True, timeout=15)
            if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                cand = Image.open(tmp).convert("RGB")
                if not _is_mostly_black(cand):
                    win = cand
            if win is None:    # -l 순흑 → 전체스샷 크롭 폴백(전체스샷은 검정 안 남)
                if full_img is None:
                    ftmp = "/tmp/sapphi_full_iso.png"
                    r2 = subprocess.run([sc, "-x", "-t", "png", ftmp], capture_output=True, timeout=15)
                    if r2.returncode == 0 and os.path.exists(ftmp):
                        full_img = Image.open(ftmp).convert("RGB")
                if full_img is not None:
                    sr = (full_img.size[0] / LW) if LW else 2.0
                    crop = full_img.crop((int(wx * sr), int(wy * sr), int((wx + ww) * sr), int((wy + wh) * sr)))
                    if not _is_mostly_black(crop):
                        win = crop
            if win is None:
                continue       # 이 창은 순흑 — 건너뛰고 다음 창
            if canvas is None:
                scale = win.size[0] / ww if ww else 2.0   # 첫 유효창의 물리폭÷논리폭 = 레티나 scale
                canvas = Image.new("RGB", (int(LW * scale), int(LH * scale)), (0, 0, 0))
            canvas.paste(win, (int(wx * scale), int(wy * scale)))   # 원위치 합성 → 전역 좌표 보존
            any_pasted = True
        if not any_pasted or canvas is None:
            return False       # 모든 창이 순흑 → 진짜 안 보임
        canvas.save(path)
        return True
    except Exception:
        return False


def _restore_app_window(app_query: str) -> bool:
    """앱 창을 복구/전면화한다 — 숨김(ESC로 메뉴바)·최소화면 다시 띄우고, *아예 안 떠 있으면 실행*한다.
    ★open_app 로 통일: 실행 중이면 활성화, 미실행이면 디스크에서 찾아 `open -b 번들ID`(한글·공백 무관)로 연다.
      (구버전은 실행 중 앱만 봐서 '네이버 지도'가 안 떠 있으면 영영 못 열었다 — 그 버그 수정.)"""
    return open_app(app_query)


def screenshot(path: str, app: str | None = None) -> str:
    """현재 화면을 path 에 PNG 로 저장(논리좌표 크기로 정렬)하고 경로를 반환.
    app 주어지면(앱 이름/일부) *그 앱의 모든 창만* 격리 캡처(겹친 창 무시, 나머지 검정).
    ★app 지정인데 창을 못 찾으면: 앱 복구(open) 후 1회 재시도 → 그래도 안 되면 *전체화면 폴백*
      (검정 프레임 금지 — 검정은 워커가 '화면 잠김'으로 환각해 루프에 빠진다. 엉뚱앱 행동은 frontmost 가드가 막음)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    # ★앱-격리 스샷: 타깃 앱 창만(전역 좌표 보존).
    if app:
        if _isolate_capture(path, app):
            _align(path)
            return path
        # ★타깃 창을 못 찾음(숨김/최소화/방금 ESC로 닫힘) → 검정 주기 전에 *앱 창을 복구*하고 1회 재시도.
        #   (이게 없으면 워커가 검정을 '화면 잠김'으로 오인해 엉뚱하게 화면 깨우기만 반복한다 — 실측 버그.)
        if _restore_app_window(app):
            time.sleep(1.2)
            if _isolate_capture(path, app):
                _align(path)
                return path
        # ★복구·격리 모두 실패 → *검정 프레임 금지*(워커가 '화면 잠김'으로 환각해 루프에 빠진다 — 실측).
        #   대신 *전체화면*으로 폴백한다(전체 캡처는 안정적). 워커가 실제 화면을 보고 타깃 앱을 직접 열어 복구하게.
        #   엉뚱한 앱에 행동하는 위험은 frontmost 가드(실행 직전 맨앞 앱 확인)가 막는다.
        # → 아래 일반 전체화면 캡처로 진행.

    # 1순위: macOS 네이티브 screencapture (가장 안정적, -x=무음)
    sc = shutil.which("screencapture")
    if sc:
        try:
            r = subprocess.run([sc, "-x", "-t", "png", path], capture_output=True, timeout=15)
            if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
                _align(path)   # 좌표 정렬(+비용 절감)
                return path
        except Exception:
            pass

    # 폴백: pyautogui
    import pyautogui

    pyautogui.screenshot().save(path)
    _align(path)
    return path


def zoom_crop(src: str, cx, cy, out_dir: str, idx: int,
              region: tuple[int, int] = (440, 340), factor: float = 2.4) -> str:
    """워커가 작은 UI/글자를 또렷이 보게 — (cx,cy) 주변을 잘라 확대(나쁜 눈 보정, track A).
    cx,cy 는 논리좌표(스샷이 논리크기로 정렬됨)=픽셀과 동일공간. 확대본 경로 반환(실패 시 원본)."""
    try:
        from PIL import Image
        im = Image.open(src).convert("RGB")
    except Exception:
        return src
    W, H = im.size
    cx = W // 2 if cx is None else int(cx)
    cy = H // 2 if cy is None else int(cy)
    rw, rh = min(region[0], W), min(region[1], H)
    x0 = max(0, min(cx - rw // 2, W - rw))
    y0 = max(0, min(cy - rh // 2, H - rh))
    crop = im.crop((x0, y0, x0 + rw, y0 + rh)).resize((int(rw * factor), int(rh * factor)))
    path = os.path.join(out_dir, f"zoom_{idx:02d}.png")
    try:
        crop.save(path)
        return path
    except Exception:
        return src
