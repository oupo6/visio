"""그라운딩 클릭 — Qwen2.5-VL 로 '말(설명)→좌표'.

ax_click(접근성 라벨)·ocr_click(화면 글자)·아이콘검출(YOLO)이 *다 안 되는* 요소 —
글자도 라벨도 없고 비표준 모양인 것(예: 카톡 친구탭 사람 실루엣 아이콘) — 을 *설명으로* 찾아 클릭.

탐지학파(YOLO/OmniParser)는 '클릭가능 아이콘이냐'를 분류해서 비표준 아이콘을 놓친다.
그라운딩 학파(VLM)는 분류 없이 *네가 묘사한 것*을 직접 좌표로 짚는다 → 친구탭 실증 성공.

설계(RUBI 반영):
 - 모델은 프로세스당 1회만 로드(lazy singleton) — 로드 ~13s, 추론 ~20s/회(로컬 VLM 비용).
 - 좌표변환을 여기서 완결: 리사이즈→크롭px→논리pt(레티나 scale은 스샷/논리크기 비로 *동적* 산출, 다중모니터 대비).
 - 프런트 '창'만 캡처해 그라운딩(전체화면은 vision 토큰 폭증으로 MPS OOM + 작은 아이콘 해상도 손실).
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

MODEL = "Qwen/Qwen2.5-VL-3B-Instruct"
_CAP = "/tmp/sapphi_ground.png"
_CROP = "/tmp/sapphi_ground_crop.png"
_CROP2 = "/tmp/sapphi_ground_crop2.png"   # 2단계 정밀화용 타이트 크롭
_REFINE_BELOW = 110   # 크롭px: 1단계 bbox 최대변이 이보다 작으면 '작은 타깃' → 주변 확대 재그라운딩
_model = None
_proc = None


def available() -> bool:
    if _BACKEND == "uitars":
        try:
            import mlx_vlm  # noqa: F401
            return True
        except Exception:
            return False
    try:
        import torch  # noqa: F401
        from transformers import Qwen2_5_VLForConditionalGeneration  # noqa: F401
        from qwen_vl_utils import process_vision_info  # noqa: F401
        return True
    except Exception:
        return False


def _load():
    global _model, _proc
    if _model is None:
        import torch
        from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL, torch_dtype=torch.bfloat16).to(dev)
        # max_pixels 캡: ①추론 속도(비전 토큰 ∝ 픽셀) ②MPS OOM 방지.
        # 실측(친구탭): 2M=20.8s, 600K=10.4s, 350K=6.5s, 200K=4.0s — 좌표는 ~4px 내로 안정.
        # 400K = 속도(~7s, 2M 대비 3배)와 작은요소 정밀도 여유의 균형. (첫 grounding만 이 비용; 이후 캐시 0.5s)
        _proc = AutoProcessor.from_pretrained(MODEL, max_pixels=400_000)
        _model._dev = dev
        # patch_size 를 config에서 읽는다(하드코딩 14 금지 — 모델 교체 시 좌표비율 어긋남 방지, RUBI).
        try:
            _model._patch = int(_model.config.vision_config.patch_size)
        except Exception:
            _model._patch = 14
    return _model, _proc


# ─────────────────────────────────────────────────────────────────────────────
# UI-TARS 백엔드 (GUI 전용 그라운딩) — 글자없는 아이콘서 Qwen-3B보다 압도적 정밀(검증됨:
#   길찾기 버튼 Qwen은 옆버튼으로 빗나감 / UI-TARS는 정중앙 명중). MLX(Apple 네이티브, 4bit ~6.7GB).
#   ★Qwen과 *동시 로드 금지*(13GB→OOM) — 백엔드 하나만 상주. OCR(글자)·캐시(재클릭)가 먼저 거르므로
#   UI-TARS는 *글자없는 콜드 아이콘일 때만* 호출 = 발열 최소.
# ─────────────────────────────────────────────────────────────────────────────
_BACKEND = os.environ.get("SAPPHI_GROUND", "uitars").lower()   # 'uitars' | 'qwen'
_UT_MODEL = "mlx-community/UI-TARS-1.5-7B-4bit"
_UT_MAXPIX = 1_200_000   # 입력 픽셀 캡(속도·발열 vs 작은아이콘 정밀 균형; 창크롭은 대개 이 아래)
_ut_model = None
_ut_proc = None


def _load_uitars():
    global _ut_model, _ut_proc
    if _ut_model is None:
        from mlx_vlm import load
        _ut_model, _ut_proc = load(_UT_MODEL)
        try:                                   # 픽셀 캡(토큰↓→속도↑·발열↓). 좌표는 grid_thw로 보정되므로 안전.
            _ut_proc.image_processor.max_pixels = _UT_MAXPIX
        except Exception:
            pass
    return _ut_model, _ut_proc


def _vlm_point_uitars(img_path: str, query: str) -> Optional[tuple]:
    """UI-TARS로 query 요소의 *중심점*을 *이미지 픽셀좌표* (x,y)로 반환(없으면 None).
    출력은 모델이 본 리사이즈 공간이라 grid_thw(=실제 리사이즈 해상도)로 이미지 px에 매핑(Qwen과 동일 원리)."""
    import re as _re
    import numpy as _np
    from PIL import Image
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template
    model, proc = _load_uitars()
    prompt = f"Output the center coordinate (x,y) in pixels of: {query}"
    formatted = apply_chat_template(proc, model.config, prompt, num_images=1)
    out = generate(model, proc, formatted, [img_path], max_tokens=48, verbose=False)
    txt = out.text if hasattr(out, "text") else str(out)
    m = _re.search(r"\((\d+),\s*(\d+)\)", txt)
    if not m:
        return None
    xm, ym = float(m.group(1)), float(m.group(2))
    im = Image.open(img_path).convert("RGB")
    iw, ih = im.size
    try:                                       # 모델이 본 리사이즈 해상도(grid_thw × patch)
        ip = proc.image_processor
        thw = ip(images=im, return_tensors="np")["image_grid_thw"]
        _, gh, gw = [int(v) for v in _np.array(thw).reshape(-1)[:3]]
        rw, rh = gw * ip.patch_size, gh * ip.patch_size
    except Exception:
        rw, rh = iw, ih
    return (xm * iw / rw, ym * ih / rh)         # → 이미지 px


def _logical_size() -> tuple[int, int]:
    import pyautogui
    s = pyautogui.size()
    return int(s.width), int(s.height)


def _front_window_bounds() -> Optional[tuple]:
    """프런트 앱 window1의 (x,y,w,h) 논리pt. 실패 시 None(→전체화면 폴백)."""
    scr = ('tell application "System Events" to tell (first process whose frontmost is true) '
           'to get {position, size} of window 1')
    try:
        r = subprocess.run(["osascript", "-e", scr], capture_output=True, text=True, timeout=5)
        nums = [int(float(x)) for x in (r.stdout or "").replace(" ", "").split(",") if x.strip()]
        # 너무 얇거나 작은 창(메뉴바·알림 등 엉뚱한 window1)은 거른다 → None(전체화면 폴백).
        # (이게 없으면 얇은 띠를 크롭해 그 위에서 그라운딩 → 헛클릭.)
        if len(nums) == 4 and nums[2] >= 120 and nums[3] >= 120:
            return tuple(nums)
    except Exception:
        pass
    return None


def _vlm_bbox(img_path: str, query: str) -> Optional[tuple]:
    """주어진 이미지에서 query 요소의 bbox 를 *그 이미지의 픽셀좌표* (x1,y1,x2,y2)로 반환(없으면 None).
    리사이즈(모델 입력)↔이미지 픽셀 변환을 여기서 완결 → 호출자는 이미지 px만 다룬다(1·2단계 공통)."""
    if _BACKEND == "uitars":                    # UI-TARS = 점 출력 → 작은 박스로 감싸 파이프라인 호환
        pt = _vlm_point_uitars(img_path, query)
        if not pt:
            return None
        x, y = pt
        r = 10
        return (x - r, y - r, x + r, y + r)
    import torch
    from PIL import Image
    from qwen_vl_utils import process_vision_info
    model, proc = _load()
    prompt = (f'Locate {query}. Output ONLY its bounding box as JSON: {{"bbox_2d":[x1,y1,x2,y2]}}.')
    msg = [{"role": "user", "content": [
        {"type": "image", "image": img_path},
        {"type": "text", "text": prompt}]}]
    txt = proc.apply_chat_template(msg, tokenize=False, add_generation_prompt=True)
    ii, vi = process_vision_info(msg)
    inp = proc(text=[txt], images=ii, videos=vi, padding=True, return_tensors="pt").to(model._dev)
    with torch.no_grad():
        g = model.generate(**inp, max_new_tokens=64, do_sample=False)
    out = proc.batch_decode([gg[len(i):] for i, gg in zip(inp.input_ids, g)],
                            skip_special_tokens=True)[0]
    thw = inp["image_grid_thw"][0].tolist()   # 메모리 정리 전에 grid 추출
    try:                                       # MPS 누적 OOM 방지
        del inp, g
        if model._dev == "mps":
            torch.mps.empty_cache()
    except Exception:
        pass
    m = re.search(r"\[\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)\s*\]", out)
    if not m:
        return None
    rw, rh = thw[2] * model._patch, thw[1] * model._patch   # 모델이 본 리사이즈 해상도(patch=config)
    iw, ih = Image.open(img_path).size
    x1, y1, x2, y2 = [float(v) for v in m.groups()]
    return (x1 * iw / rw, y1 * ih / rh, x2 * iw / rw, y2 * ih / rh)   # → 이미지 px


def ground(query: str) -> Optional[dict]:
    """설명(query)에 맞는 요소 중심의 *논리좌표* 를 찾는다 → {'lx','ly',...} 또는 None.
    ★2단계: 1단계(창 전체)에서 bbox가 *작으면*(스타일드 작은 버튼) 그 주변만 타이트 크롭→재그라운딩으로 정밀화.
    (네이버 카드의 작은 출발/도착 버튼을 1단계 ground가 빗나가던 문제 대응.)"""
    from PIL import Image
    from . import ocr
    _load_uitars() if _BACKEND == "uitars" else _load()   # 백엔드 하나만 상주(OOM 방지)
    LW, LH = _logical_size()
    subprocess.run(["screencapture", "-x", "-m", _CAP], timeout=10)
    ocr._assert_not_blank(_CAP)               # 화면녹화 권한 거부=검은화면 무음실패 차단(ocr 검사 재사용)
    full = Image.open(_CAP).convert("RGB")
    fw, fh = full.size
    scale = fw / LW if LW else 2.0            # 레티나/HiDPI 동적 산출(스샷px ÷ 논리폭)
    win = _front_window_bounds()
    if win:
        wx, wy, ww, wh = win
        img = full.crop((int(wx * scale), int(wy * scale),
                         int((wx + ww) * scale), int((wy + wh) * scale)))
        ox, oy = wx, wy
    else:
        img, ox, oy = full, 0, 0
    img.save(_CROP)                           # _CROP = 창 크롭(1단계 + 지문 추출 기준, 2단계 후에도 유지)
    ow, oh = img.size
    bb = _vlm_bbox(_CROP, query)              # 1단계: 창 전체에서
    if not bb:
        return None
    x1, y1, x2, y2 = bb
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2     # 창크롭 px
    bbox_crop = (int(x1), int(y1), int(x2), int(y2))
    refined = False
    bw, bh = x2 - x1, y2 - y1
    if _BACKEND != "uitars" and max(bw, bh) < _REFINE_BELOW:   # ★작은 타깃 → 2단계 정밀화(UI-TARS는 이미 정밀, 생략)
        R = int(min(420, max(200, max(bw, bh) * 5)))   # 예측 주변 정사각 ROI(창크롭 px)
        tx0, ty0 = max(0, int(cx - R / 2)), max(0, int(cy - R / 2))
        tx1, ty1 = min(ow, int(cx + R / 2)), min(oh, int(cy + R / 2))
        if (tx1 - tx0) >= 40 and (ty1 - ty0) >= 40:
            img.crop((tx0, ty0, tx1, ty1)).save(_CROP2)   # 풀해상도 타이트 크롭(max_pixels 안 넘어 다운스케일 없음→정밀)
            bb2 = _vlm_bbox(_CROP2, query)
            if bb2:
                bx1, by1, bx2, by2 = bb2
                cx, cy = (bx1 + bx2) / 2 + tx0, (by1 + by2) / 2 + ty0   # 타이트→창크롭 px(offset 복원)
                bbox_crop = (int(bx1 + tx0), int(by1 + ty0), int(bx2 + tx0), int(by2 + ty0))
                refined = True
    lx = ox + cx / scale                      # 창크롭 px → 논리pt (offset + scale)
    ly = oy + cy / scale
    if not (0 <= lx <= LW and 0 <= ly <= LH):  # 화면 밖이면 무효(헛클릭 방지)
        return None
    return {"lx": int(lx), "ly": int(ly), "raw": ("2단계정밀" if refined else "1단계"),
            "bbox_crop": bbox_crop, "crop_path": _CROP, "win": win, "scale": scale}


# ─────────────────────────────────────────────────────────────────────────────
# 자가검증 캐시 (비용 절감) — 한 번 grounding(20s)하면 *창-상대 오프셋 + 시각 지문*을 저장.
# 다음 호출엔 VLM 없이: 현재 창위치+오프셋으로 후보좌표 → 그 자리 작은 ROI에서 지문을
# template-match(OpenCV, ~수십ms)해 *진짜 거기 있나* 확인. 맞으면 그 위치 클릭(드리프트 자동보정),
# 아니면 VLM 폴백. "고정 레이아웃이냐"를 사전 판단하지 않고 매번 싸게 검증한다.
# ─────────────────────────────────────────────────────────────────────────────
import json as _json
import hashlib as _hashlib

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "ground_cache.json")
_FP_DIR = os.path.join(os.path.dirname(__file__), "ground_fp")
_SEP = "§"
_MATCH_THRESH = 0.60   # template-match 신뢰 임계(이상이면 '거기 있음'으로 클릭)


def _front_window_info() -> Optional[tuple]:
    """(앱이름, 창제목, x, y, w, h) 논리pt. 캐시 키(앱+제목)와 현재 창위치용."""
    scr = ('tell application "System Events"\n'
           ' set fp to first process whose frontmost is true\n'
           ' set a to name of fp\n'
           ' tell fp\n'
           '  set t to name of window 1\n'
           '  set {x, y} to position of window 1\n'
           '  set {w, h} to size of window 1\n'
           ' end tell\n'
           'end tell\n'
           'return a & "' + _SEP + '" & t & "' + _SEP + '" & x & "' + _SEP + '" & y & "' + _SEP + '" & w & "' + _SEP + '" & h')
    try:
        r = subprocess.run(["osascript", "-e", scr], capture_output=True, text=True, timeout=5)
        parts = (r.stdout or "").strip().split(_SEP)
        if len(parts) == 6:
            a, t = parts[0], parts[1]
            x, y, w, h = [int(float(v)) for v in parts[2:]]
            if w >= 120 and h >= 120:
                return a, t, x, y, w, h
    except Exception:
        pass
    return None


def _load_cache() -> dict:
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {}


def _save_cache(d: dict) -> None:
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            _json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _verify(cand_lx: float, cand_ly: float, fp_path: str, scale: float) -> Optional[tuple]:
    """후보좌표 주변 ROI에서 지문(아이콘)을 template-match. 맞으면 (논리x, 논리y, score) 반환."""
    try:
        import cv2
    except Exception:
        return None
    if not os.path.exists(fp_path):
        return None
    subprocess.run(["screencapture", "-x", "-m", _CAP], timeout=10)
    full = cv2.imread(_CAP, cv2.IMREAD_GRAYSCALE)
    tmpl = cv2.imread(fp_path, cv2.IMREAD_GRAYSCALE)
    if full is None or tmpl is None:
        return None
    th, tw = tmpl.shape[:2]
    H, W = full.shape[:2]
    px, py = cand_lx * scale, cand_ly * scale       # 후보 = 물리px
    margin = int(max(tw, th) * 1.5)                 # 드리프트 허용 검색범위
    x0, y0 = max(0, int(px - tw / 2 - margin)), max(0, int(py - th / 2 - margin))
    x1, y1 = min(W, int(px + tw / 2 + margin)), min(H, int(py + th / 2 + margin))
    roi = full[y0:y1, x0:x1]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return None
    res = cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)
    _, maxv, _, maxloc = cv2.minMaxLoc(res)
    if maxv >= _MATCH_THRESH:
        mcx = x0 + maxloc[0] + tw / 2                # 매칭 중심(물리px)
        mcy = y0 + maxloc[1] + th / 2
        return (mcx / scale, mcy / scale, float(maxv))   # → 논리pt
    return None


def _save_entry(cache: dict, query: str, r: dict, info: tuple) -> None:
    """grounding 결과를 창-상대 오프셋 + 지문으로 캐시 저장."""
    from PIL import Image
    app, title = info[0], info[1]
    win = r.get("win")
    if not win or not r.get("crop_path"):
        return
    wx, wy = win[0], win[1]
    os.makedirs(_FP_DIR, exist_ok=True)
    crop = Image.open(r["crop_path"]).convert("RGB")
    bb = r["bbox_crop"]
    pad = 4
    fp = crop.crop((max(0, bb[0] - pad), max(0, bb[1] - pad), bb[2] + pad, bb[3] + pad))
    fname = "fp_" + _hashlib.md5(f"{app}{title}{query}".encode()).hexdigest()[:10] + ".png"
    fp.save(os.path.join(_FP_DIR, fname))
    cache[f"{app}{_SEP}{title}{_SEP}{query}"] = {
        "dx": r["lx"] - wx, "dy": r["ly"] - wy,      # 창-상대 오프셋(논리pt)
        "w": win[2], "h": win[3], "scale": r["scale"],
        "fp": os.path.join("ground_fp", fname),
    }
    _save_cache(cache)


def ground_cached(query: str) -> Optional[dict]:
    """캐시 우선 → 지문검증 통과면 VLM 없이 즉시. 실패/콜드면 VLM(ground) 후 캐시 저장."""
    info = _front_window_info()
    cache = _load_cache()
    if info:
        app, title, x, y, w, h = info
        e = cache.get(f"{app}{_SEP}{title}{_SEP}{query}")
        if e and abs(e["w"] - w) <= 20 and abs(e["h"] - h) <= 20:   # 리사이즈면 무효
            v = _verify(x + e["dx"], y + e["dy"],
                        os.path.join(os.path.dirname(__file__), e["fp"]), e.get("scale", 2.0))
            if v:   # 지문이 그 자리에 있음 → 캐시 클릭(드리프트 보정된 위치)
                return {"lx": int(v[0]), "ly": int(v[1]), "via": "cache", "score": round(v[2], 2)}
    # 콜드 / 키 없음 / 검증 실패 → VLM
    r = ground(query)
    if r:
        if info:
            try:
                _save_entry(cache, query, r, info)
            except Exception:
                pass
        r["via"] = "vlm"
    return r
