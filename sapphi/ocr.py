"""비전 grounding — macOS 내장 Vision OCR(ocrground)로 화면 텍스트+좌표를 얻어,
'라벨'을 화면에 보이는 글자로 매칭해 *정확한 논리좌표* 를 돌려준다.

ax_click(접근성 라벨)이 안 되는 경우 — 라벨이 접근성 트리에 없지만 화면엔 글자가 보일 때 —
픽셀 추측 대신 이걸 쓴다. 좌표변환(Y뒤집기/Retina)은 ocrground(Swift) 안에서 완결됨.

★RUBI 사후비평 반영:
 - 모든 실패(타임아웃·바이너리없음·캡처실패·검은화면)를 OcrError로 명시 — *무음 폴백 금지*.
 - 매칭은 토큰 인식: '전송'=토큰 '메시지 전송'(0.9) 은 살리고, '편집'⊂'텍스트편집기'(부분, 길이페널티) 는 죽인다.
"""

from __future__ import annotations

import difflib
import json
import os
import subprocess
from typing import Optional

_BIN = os.path.join(os.path.dirname(__file__), "ocrground")


class OcrError(RuntimeError):
    pass


def available() -> bool:
    return os.path.exists(_BIN) and os.access(_BIN, os.X_OK)


def _logical_size() -> tuple[int, int]:
    import pyautogui
    s = pyautogui.size()
    return int(s.width), int(s.height)


def _front_window_bounds() -> Optional[tuple]:
    """프런트 앱 window1의 (x,y,w,h) 논리pt. 실패/너무작으면 None(→전체화면)."""
    scr = ('tell application "System Events" to tell (first process whose frontmost is true) '
           'to get {position, size} of window 1')
    try:
        r = subprocess.run(["osascript", "-e", scr], capture_output=True, text=True, timeout=5)
        nums = [int(float(x)) for x in (r.stdout or "").replace(" ", "").split(",") if x.strip()]
        if len(nums) == 4 and nums[2] >= 120 and nums[3] >= 120:
            return tuple(nums)
    except Exception:
        pass
    return None


def _assert_not_blank(path: str) -> None:
    """화면녹화 권한 거부 시 screencapture는 rc=0 + *검은 이미지* 를 준다 → 무음 실패가 됨.
    평균 밝기로 잡아낸다(PIL 문제 시엔 과민중단 방지 위해 통과)."""
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as im:
            st = ImageStat.Stat(im.convert("L"))
            mean, std = st.mean[0], st.stddev[0]
    except OcrError:
        raise
    except Exception:
        return
    # ★다크모드(평균 어둡지만 분산 큼)와 구분: 권한거부 캡처는 *균일 순흑*(평균~0 AND 분산~0).
    if mean < 3.0 and std < 2.0:
        raise OcrError("화면이 균일 검정 — 화면녹화 권한 미승인 가능성(시스템 설정>개인정보 보호>화면 기록)")


def _capture(path: str) -> None:
    try:
        r = subprocess.run(["screencapture", "-x", "-m", path],
                           capture_output=True, text=True, timeout=10)
    except Exception as e:
        raise OcrError(f"screencapture 실행 실패: {e}")
    if r.returncode != 0 or not os.path.exists(path) or os.path.getsize(path) == 0:
        raise OcrError("screencapture 실패 — 화면녹화 권한(시스템 설정>개인정보 보호>화면 기록) 확인")
    _assert_not_blank(path)


def ocr_screen(image_path: Optional[str] = None, front_window_only: bool = False) -> list[dict]:
    """화면(또는 주어진 이미지)을 OCR해 [{text,cx,cy,w,h,conf}] (*전체화면* 논리좌표) 리스트 반환.
    image_path 미지정 시 현재 화면을 풀해상도로 캡처해 OCR. 모든 실패는 OcrError(무음 폴백 없음).
    front_window_only=True: 프런트 창만 크롭해 OCR(다른 앱·창 밖 텍스트 배제 → 노이즈·픽셀↓) 후 좌표를
      전체화면 논리로 보정. 창을 못 찾으면 전체화면 안전 폴백. ★주의: 메뉴바 등 *창 밖* 타깃은 못 읽는다."""
    crop_off = None    # (ox,oy): 창-로컬 → 전체화면 논리 보정 오프셋
    win_dims = None    # (ww,wh): ocrground 에 넘길 좌표 기준 치수(크롭 시 창 논리치수)
    if image_path is None:
        image_path = "/tmp/sapphi_ocr.png"
        _capture(image_path)
        if front_window_only:
            win = _front_window_bounds()
            if win:
                try:
                    from PIL import Image
                    W0, _ = _logical_size()
                    with Image.open(image_path) as im0:
                        fw = im0.size[0]
                    sc = fw / W0 if W0 else 2.0          # 레티나 scale = 스샷폭 ÷ 논리폭
                    wx, wy, ww, wh = win
                    cpath = "/tmp/sapphi_ocr_win.png"
                    Image.open(image_path).convert("RGB").crop(
                        (int(wx * sc), int(wy * sc), int((wx + ww) * sc), int((wy + wh) * sc))).save(cpath)
                    image_path, crop_off, win_dims = cpath, (wx, wy), (ww, wh)
                except Exception:
                    crop_off = win_dims = None            # 어떤 문제든 전체화면으로 폴백
    W, H = win_dims if win_dims else _logical_size()       # 크롭이면 창 논리치수 → 창-로컬 좌표
    try:
        r = subprocess.run([_BIN, image_path, str(W), str(H)],
                           capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise OcrError("ocrground 15s 타임아웃 — 시스템 부하/권한 문제")
    except FileNotFoundError:
        raise OcrError("ocrground 바이너리 없음(swiftc -O ocrground.swift -o ocrground)")
    except Exception as e:
        raise OcrError(f"ocrground 실행 오류: {e}")
    if r.returncode != 0:
        raise OcrError(f"ocrground rc={r.returncode}: {(r.stderr or '').strip()[:120]}")
    raw = r.stdout or "[]"
    # ★시스템 프레임워크 로그(Metal/JIT 'bundle...' 등)가 stdout 끝에 섞일 수 있다 → JSON 배열만 추출.
    s, e2 = raw.find("["), raw.rfind("]")
    if s != -1 and e2 > s:
        raw = raw[s:e2 + 1]
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OcrError(f"ocrground 출력 파싱 실패: {e}")
    if crop_off:           # 창-로컬 → 전체화면 논리좌표(오프셋 복원)
        ox, oy = crop_off
        for it in items:
            if "cx" in it:
                it["cx"] += ox
            if "cy" in it:
                it["cy"] += oy
    return items


def _norm(s: str) -> str:
    return "".join((s or "").split()).lower()


def match(items: list[dict], label: str) -> list[tuple[float, dict]]:
    """라벨과 각 OCR 텍스트의 매칭 점수(0~1)를 매겨 내림차순 정렬.
    ★토큰 인식 + 길이 페널티(RUBI): 완전일치1.0 > 통째토큰0.9 > 부분포함(coverage비례) > 역포함(coverage비례) > 유사도.
    ★역포함도 coverage 페널티(2026-06: 네이버 native 실패서 도출): 두뇌가 준 *긴 아이콘 묘사*('길찾기 파란 화살표
    아이콘')가 화면의 *짧은 글자조각*('길찾기')을 헛매칭해 *글자를* 눌러버리고 ground(비전)까지 못 가던 버그 차단 →
    라벨을 거의 다 덮을 때만 강함, 짧은 조각이면 점수↓ → smart_click이 아이콘을 ground로 흘려보낸다.
    짧은 라벨(≤3자)은 difflib 신뢰를 낮춰 우연한 형태소 일치를 억제."""
    nl = _norm(label)
    if not nl:
        return []
    scored: list[tuple[float, dict]] = []
    for it in items:
        raw = it.get("text", "")
        nt = _norm(raw)
        if not nt:
            continue
        tokens = [_norm(x) for x in raw.split() if _norm(x)]
        if nl == nt:
            sc = 1.0
        elif nl in tokens:                       # 라벨이 통째 토큰: '전송'∈'메시지 전송'
            sc = 0.9
        elif nl in nt:                           # 부분 포함: 짧을수록↓ ('편집'⊂'텍스트편집기'→낮음)
            sc = 0.55 + 0.35 * (len(nl) / len(nt))
        elif nt in nl:                           # 화면글자가 라벨의 *조각*: 긴 묘사라벨(아이콘)이 짧은 글자에 헛매칭 방지
            sc = 0.6 * (len(nt) / len(nl))       # 라벨 거의 덮어야 강함(>=0.6). '길찾기 파란 화살표 아이콘'⊃'길찾기'=0.16 → ground로
        else:
            sc = difflib.SequenceMatcher(None, nl, nt).ratio()
            if len(nl) <= 3:
                sc *= 0.7
        scored.append((sc, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored
