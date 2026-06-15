"""네이티브 결정론 자극 도구함 — VISIO가 테스트 자극을 *직접* 쏜다(= 함수발생기).

VISIO(독립 테스터)는 시나리오를 스스로 짜고 *직접 트리거*한다. 그 트리거는 GUI 워커가 즉흥으로
하는 게 아니라(세션12 실패: '알림 20개 만들기'서 헤맴) — 여기 *결정론적 네이티브 자극기*로 깔끔히 쏜다.

자기 도구로 *못 만드는* 자극(특정 앱 내부이벤트·하드웨어·복잡 사전상태)은 여기 없고, 그건 VISIO가
에이전트에게 *픽스처(신호발생기) 작성을 요청*한다(rubi/visio.py 의 fixture_requests).

produce(kind, params) → {"ok", "kind", "injected"(=ground truth, 판정 대조용), "detail"}.
"""

from __future__ import annotations

import hashlib
import os
import random
import shutil
import subprocess
import tempfile
import time

# VISIO가 *직접* 만들 수 있는 자극 종류. 이 외(app_internal/hardware/...)는 fixture 요청 대상.
KNOWN_KINDS = {"notification", "file", "clipboard", "screenshot_file", "clipboard_image"}


def can_produce(kind: str) -> bool:
    return (kind or "").strip().lower() in KNOWN_KINDS


def produce(kind: str, params: dict | None = None) -> dict:
    """자극 1개를 결정론적으로 주입. injected = *무엇을 넣었는지*(판정의 ground truth)."""
    kind = (kind or "").strip().lower()
    params = params or {}
    try:
        if kind == "notification":
            return _notification(params)
        if kind == "file":
            return _file(params)
        if kind == "clipboard":
            return _clipboard(params)
        if kind == "screenshot_file":
            return _screenshot_file(params)
        if kind == "clipboard_image":
            return _clipboard_image(params)
        return {"ok": False, "kind": kind, "injected": {},
                "detail": f"VISIO가 못 만드는 자극: {kind} (픽스처 요청 대상)"}
    except Exception as e:
        return {"ok": False, "kind": kind, "injected": {}, "detail": f"{type(e).__name__}: {str(e)[:160]}"}


# ─────────────────────────────────────────────────────────────────────────────
def _osa_escape(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _notify_one(title: str, body: str) -> None:
    osa = shutil.which("osascript")
    if not osa:
        raise RuntimeError("osascript 없음")
    script = f'display notification "{_osa_escape(body)}" with title "{_osa_escape(title)}"'
    subprocess.run([osa, "-e", script], capture_output=True, text=True, timeout=8)


def _notification(params: dict) -> dict:
    """단일 또는 버스트 알림. params: {title, body} | {items:[{title,body}|str...], interval} | {count}."""
    items = params.get("items")
    interval = float(params.get("interval", 0.4))
    posted: list = []
    if items:                                   # 버스트(여러 개 빠르게) — '중간 누락' 같은 규모 테스트
        for i, it in enumerate(items):
            if isinstance(it, dict):
                t, b = it.get("title", f"VISIO 테스트 {i+1}"), it.get("body", "")
            else:
                t, b = f"VISIO 테스트 {i+1}", str(it)
            _notify_one(t, b)
            posted.append({"title": t, "body": b})
            if i < len(items) - 1:
                time.sleep(interval)
    else:
        count = int(params.get("count", 1) or 1)
        title = params.get("title", "VISIO 테스트 알림")
        body = params.get("body", "테스트 알림 본문")
        for i in range(max(1, count)):
            t = title if count == 1 else f"{title} ({i+1}/{count})"
            _notify_one(t, body)
            posted.append({"title": t, "body": body})
            if i < count - 1:
                time.sleep(interval)
    return {"ok": True, "kind": "notification", "injected": {"notifications": posted, "count": len(posted)},
            "detail": f"알림 {len(posted)}개 발사"}


def _file(params: dict) -> dict:
    """감시폴더 등에 테스트 파일 생성. params: {dir, name, content}."""
    directory = os.path.expanduser(params.get("dir") or params.get("directory") or "/tmp/visio_drop")
    name = params.get("name") or "visio_test.txt"
    content = params.get("content", "VISIO 테스트 파일 내용")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "kind": "file", "injected": {"path": path, "content": content},
            "detail": f"파일 생성: {path}"}


def _clipboard(params: dict) -> dict:
    """클립보드에 텍스트 주입. params: {text}."""
    pbcopy = shutil.which("pbcopy")
    if not pbcopy:
        raise RuntimeError("pbcopy 없음")
    text = params.get("text", "VISIO 테스트 클립보드 텍스트")
    subprocess.run([pbcopy], input=text, text=True, timeout=8)
    return {"ok": True, "kind": "clipboard", "injected": {"text": text}, "detail": f"클립보드 주입({len(text)}자)"}


# 스크린샷 자동정리 류 기능을 *샌드박스에서* 시험하려면 실데이터(~/Desktop)를 건드리면 안 된다 →
# VISIO가 *임시 폴더에 테스트 이미지를 직접 만들어 주입*한다. injected.sha256 = 복사 성공 시 클립보드에
# 올라가야 할 PNG 바이트 해시(결정론 검증 닻). fault 는 SUT 어댑터로 흘려보내 '복사 강제 실패'를 만든다.
_SCREENSHOT_VARIANTS = {"valid", "huge", "nonscreenshot", "corrupt", "zero"}


def _screenshot_file(params: dict) -> dict:
    """샌드박스 temp 폴더에 테스트 스크린샷 이미지(들)를 생성. 절대 ~/Desktop·실데이터 미접촉.

    params:
      variant: valid|huge|nonscreenshot|corrupt|zero   (기본 valid)
      count:   다발(burst) 개수 (기본 1)
      width/height: 이미지 크기 (huge 는 기본 4000²)
      dir/name/names: 명시 지정(생략 시 mkdtemp + 스샷풍 이름 자동)
      fault:   'copy_fail' → injected 로 흘려보내 SUT 가 복사를 강제 실패(안전 불변식 시험)
    """
    variant = str(params.get("variant") or "valid").lower()
    if variant not in _SCREENSHOT_VARIANTS:
        variant = "valid"
    base = os.path.expanduser(params.get("dir") or "")
    if base:
        os.makedirs(base, exist_ok=True)
        sandbox = base
    else:
        sandbox = tempfile.mkdtemp(prefix="visio_screenshot_sandbox_")
    count = max(1, int(params.get("count", 1) or 1))
    names = params.get("names")
    w = int(params.get("width", 4000 if variant == "huge" else 320))
    h = int(params.get("height", 4000 if variant == "huge" else 240))

    def _name(i: int) -> str:
        if names and i < len(names):
            return names[i]
        if params.get("name") and count == 1:
            return params["name"]
        if variant == "nonscreenshot":            # 스샷 명명 규칙 *불일치* → 정리 대상 아님(오삭제 금지 시험)
            return f"vacation_photo_{i + 1}.png"
        return f"Screenshot 2026-06-13 at {10 + i:02d}.20.30.png"   # macOS 기본 스샷 풍 이름

    paths, shas = [], []
    for i in range(count):
        path = os.path.join(sandbox, _name(i))
        if variant == "zero":
            open(path, "wb").close()              # 0바이트
        elif variant == "corrupt":
            with open(path, "wb") as f:           # 이미지가 아닌 깨진 바이트(.png 확장자만)
                f.write(b"not-a-real-png " + bytes(random.randrange(256) for _ in range(256)))
        else:                                     # valid | huge | nonscreenshot = 진짜 PNG
            from PIL import Image
            Image.new("RGB", (w, h), ((30 + i * 25) % 256, 90, 200)).save(path, "PNG")
        with open(path, "rb") as f:
            raw = f.read()
        paths.append(path)
        shas.append(hashlib.sha256(raw).hexdigest() if raw else "")

    injected: dict = {"variant": variant, "sandbox": sandbox, "fault": params.get("fault")}
    if count == 1:
        injected["path"], injected["sha256"] = paths[0], shas[0]
    else:
        injected["paths"], injected["sha256s"] = paths, shas
        injected["path"], injected["sha256"] = paths[-1], shas[-1]   # 마지막 = 클립보드에 남는 것
    return {"ok": True, "kind": "screenshot_file", "injected": injected,
            "detail": f"{variant} 스샷 {len(paths)}개 @ {sandbox}"
                      + (f" (fault={params['fault']})" if params.get("fault") else "")}


def _clipboard_image(params: dict) -> dict:
    """이미지를 시스템 클립보드에 *PNG로* 주입(클립보드 이미지 처리 기능 테스트용).
    params: {path}(기존 이미지 파일) 또는 생성 {size:[w,h], color}. injected:{sha256,nbytes}
    → probe('clipboard_image', {'from_injected':'sha256'})로 *같은 이미지가 들어갔나* 검증."""
    import hashlib
    path = params.get("path")
    if path and os.path.exists(os.path.expanduser(path)):
        with open(os.path.expanduser(path), "rb") as f:
            payload = f.read()
    else:
        from PIL import Image
        import io
        size = params.get("size") or [64, 64]
        buf = io.BytesIO()
        Image.new("RGB", (int(size[0]), int(size[1])), params.get("color", "blue")).save(buf, "PNG")
        payload = buf.getvalue()
    from AppKit import NSPasteboard, NSPasteboardTypePNG
    from Foundation import NSData
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    data = NSData.dataWithBytes_length_(payload, len(payload))
    if not bool(pb.setData_forType_(data, NSPasteboardTypePNG)):
        return {"ok": False, "kind": "clipboard_image", "injected": {}, "detail": "클립보드 PNG 쓰기 거부"}
    sha = hashlib.sha256(payload).hexdigest()
    return {"ok": True, "kind": "clipboard_image", "injected": {"sha256": sha, "nbytes": len(payload)},
            "detail": f"클립보드에 PNG {len(payload)}B 주입"}
