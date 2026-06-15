"""읽기 사다리 — 화면 *내용*을 읽는 캐스케이드 (클릭 사다리 `act.smart_click`의 짝).

  OCR(공짜·로컬) → 빈약하거나 force_vision → 비전(의미 이해)으로 escalate.  OCR은 비전 *교차검증 앵커*.
  (AX는 *클릭* 사다리용 — 읽기엔 창 chrome만 주므로 제외.)

비용/프라이버시(세션13 방향 정정 — 온디바이스 강제 아님, 민감만 로컬):
  비전 rung = 비-민감 → 클라우드 Claude(강함·기본 sonnet) / 민감 → 로컬 gemma4(스샷 클라우드 미전송).

read_content(app, sensitive, instruction) → {"method","text","ocr","shot","escalated"}.
"""
from __future__ import annotations

import os
import time

from sapphi import ocr, perceive

_OCR_MIN = 20   # OCR 글자가 이 미만 = 'OCR 빈약' → 비전 escalate
_CLOUD_READ_MODEL = "claude-sonnet-4-6"   # 비-민감 비전 읽기(가성비). opus는 판정 전용.
# ※AX는 *클릭* 사다리(smart_click)용 — 읽기엔 창 chrome(버튼/스크롤)만 줘서 content가 안 나온다(실측).
#   그래서 읽기 사다리는 AX 빼고 OCR → 비전.


def _zoom_crops(shot: str, app: str, out_dir: str, factor: float = 2.2, max_windows: int = 3) -> list[str]:
    """앱 창별로 캡처를 크롭+확대 → 비전 읽기 충실도↑(작은 글씨 누락 방지, 실측). 창 못 찾으면 [shot]."""
    try:
        from PIL import Image
        wins = perceive._find_app_windows(app)
        if not wins:
            return [shot]
        im = Image.open(shot).convert("RGB")
        lw = (perceive._logical_size() or (im.size[0], 0))[0]
        sc = im.size[0] / lw if lw else 1.0
        out = []
        for i, (_wid, x, y, w, h) in enumerate(sorted(wins, key=lambda t: -t[3] * t[4])[:max_windows]):
            crop = im.crop((int(x * sc), int(y * sc), int((x + w) * sc), int((y + h) * sc)))
            if crop.size[0] < 40 or crop.size[1] < 40:
                continue
            crop = crop.resize((int(crop.size[0] * factor), int(crop.size[1] * factor)))
            cp = os.path.join(out_dir, f"zoom_{i}.png")
            crop.save(cp)
            out.append(cp)
        return out or [shot]
    except Exception:
        return [shot]


def read_content(app: str, *, sensitive: bool = False, instruction: str | None = None,
                 out_dir: str = "visio_out/read", model: str | None = None,
                 force_vision: bool = False) -> dict:
    from . import provider

    os.makedirs(out_dir, exist_ok=True)
    shot = os.path.join(out_dir, "read.png")
    perceive.open_app(app)
    perceive.focus_app(app)
    time.sleep(0.8)
    perceive.screenshot(shot, app=app)

    # ① OCR rung — 화면 글자(공짜·로컬). 단순 텍스트엔 충분 + 비전 교차검증 앵커.
    ocr_text = ""
    if ocr.available():
        items = ocr.ocr_screen(image_path=shot)
        ocr_text = "\n".join(str(it.get("text", "")).strip()
                             for it in items if str(it.get("text", "")).strip())
    if not force_vision and len(ocr_text.strip()) >= _OCR_MIN:
        return {"method": "ocr", "text": ocr_text, "ocr": ocr_text,
                "shot": shot, "escalated": False}

    # ② 비전 escalate — OCR 빈약 or force_vision → 모델 로드.
    #    ★줌: 전체 캡처를 통째 읽으면 작은 글씨를 놓침(실측: gemma4가 'VISIO'→'V510' 오독·누락) →
    #      앱 *창별 크롭+확대* 후 읽어 충실도↑. 민감=로컬 gemma4 / 비-민감=클라우드 Claude.
    prompt = (instruction or
              "이 화면에 보이는 내용을 *있는 그대로* 텍스트로 옮겨라. 메시지를 위→아래 순서로. "
              "화면에 없는 건 지어내지 마라.")
    cloud_model = model or _CLOUD_READ_MODEL

    def _read_img(img_path: str) -> str:
        if sensitive:
            return provider.complete_local(
                prompt, model=os.environ.get("SAPPHI_LOCAL_MODEL", "gemma4"), image_path=img_path) or ""
        data = provider.complete_json(prompt + '\nONLY JSON: {"content":"<옮긴 내용 전체>"}',
                                      cloud_model, image_path=img_path)
        return (data or {}).get("content") or ""

    try:
        crops = _zoom_crops(shot, app, out_dir)         # 창별 크롭+확대(없으면 [shot])
        parts = [t.strip() for t in (_read_img(cp) for cp in crops) if t.strip()]
        txt = "\n---\n".join(parts)
        method = (("vision-local(gemma4)" if sensitive else f"vision-cloud({cloud_model})")
                  + f"·줌{len(crops)}창")
        return {"method": method, "text": txt, "ocr": ocr_text, "shot": shot, "escalated": True}
    except Exception as e:
        # 비전 실패 → OCR로 graceful 폴백(공짜 앵커라도 반환).
        return {"method": "ocr(vision실패)", "text": ocr_text, "ocr": ocr_text, "shot": shot,
                "escalated": True, "error": f"{type(e).__name__}: {str(e)[:120]}"}
