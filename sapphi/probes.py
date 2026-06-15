"""결정론 출력-검증 도구함 — VISIO가 LLM 판정자 말고 *바깥 ground-truth*로 성공을 확인한다.

`triggers`(입력측 자극 주입)의 *출력측 짝*. LLM 판정자는 강하지만 환각·게이밍 가능 →
닫힌 루프에서 '초록불'이 의미 있으려면 *속일 수 없는 닻*이 필요하다. probe는 실제 파일/클립보드/
앱 상태를 결정론적으로 읽어 *주입한 자극(injected ground truth)*이 출력에 반영됐는지 대조한다.

probe(kind, params, injected) → {"ok", "achieved"(bool|None), "evidence", "detail"}.
  achieved=True  : 결정론적으로 확인됨(성공 닻)
  achieved=False : 결정론적으로 *아님*(거짓 pass 거부권 발동 대상)
  achieved=None  : 확인 불가(닻 못 내림 — 거부 안 함, VLM 판정에 위임)

params 의 `from_injected`(키 이름 또는 키 리스트)로 *주입값*을 기대 텍스트로 끌어온다.
예) {"kind":"notes_contains","params":{"title":"📥 알림 정리","from_injected":"body"}}.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

KNOWN_KINDS = {"file_contains", "file_exists", "clipboard_contains", "notes_contains",
               "reminders_contains", "app_running", "defaults_equals", "screen_contains",
               "path_absent", "clipboard_image", "http_received"}


def can_probe(kind: str) -> bool:
    return (kind or "").strip().lower() in KNOWN_KINDS


def probe(kind: str, params: dict | None = None, injected: dict | None = None) -> dict:
    kind = (kind or "").strip().lower()
    params = params or {}
    try:
        if kind == "file_exists":
            return _file_exists(params)
        if kind == "file_contains":
            return _file_contains(params, injected)
        if kind == "clipboard_contains":
            return _clipboard_contains(params, injected)
        if kind == "notes_contains":
            return _notes_contains(params, injected)
        if kind == "reminders_contains":
            return _reminders_contains(params, injected)
        if kind == "app_running":
            return _app_running(params)
        if kind == "defaults_equals":
            return _defaults_equals(params)
        if kind == "screen_contains":
            return _screen_contains(params, injected)
        if kind == "path_absent":
            return _path_absent(params)
        if kind == "clipboard_image":
            return _clipboard_image(params, injected)
        if kind == "http_received":
            return _http_received(params, injected)
        return {"ok": False, "achieved": None, "evidence": "",
                "detail": f"알 수 없는 probe kind: {kind}"}
    except Exception as e:
        return {"ok": False, "achieved": None, "evidence": "",
                "detail": f"{type(e).__name__}: {str(e)[:160]}"}


# ── 기대 텍스트 추출 ──────────────────────────────────────────────────────────
def _expected_texts(params: dict, injected: dict | None) -> list[str]:
    """검사할 기대 문자열들 — params.text(들) + from_injected 로 끌어온 주입값."""
    out: list[str] = []
    t = params.get("text")
    if isinstance(t, str):
        out.append(t)
    elif isinstance(t, list):
        out += [str(x) for x in t]
    keys = params.get("from_injected")
    if keys:
        keys = [keys] if isinstance(keys, str) else list(keys)
        for k in keys:
            out += _injected_values(injected, k)
    return [s for s in (x.strip() for x in out) if s]   # 빈 문자열 제외


def _injected_values(injected: dict | None, key: str) -> list[str]:
    """주입값(injected)에서 key 에 해당하는 스칼라들을 모은다 (최상위 + 리스트 안 dict)."""
    vals: list[str] = []
    if not isinstance(injected, dict):
        return vals
    top = injected.get(key)
    if isinstance(top, (str, int, float)):
        vals.append(str(top))
    for v in injected.values():
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and it.get(key) not in (None, ""):
                    vals.append(str(it.get(key)))
    return vals


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _judge(content: str, expected: list[str]) -> dict:
    """기대 텍스트가 *모두* content 안에 보이면 achieved=True. 닻 없으면 None."""
    if not expected:
        return {"achieved": None, "evidence": "기대 텍스트 없음(닻 못 내림)"}
    nc = _norm(content)
    found, missing = [], []
    for e in expected:
        ne = _norm(e)
        probe_str = ne[:24] if len(ne) > 24 else ne     # 요약/이스케이프 관용 — 앞부분 매칭
        (found if probe_str and probe_str in nc else missing).append(e[:40])
    achieved = len(missing) == 0
    ev = f"확인 {len(found)}/{len(expected)}"
    if missing:
        ev += f" · 누락={missing}"
    return {"achieved": achieved, "evidence": ev}


# ── 개별 probe ────────────────────────────────────────────────────────────────
def _file_exists(params: dict) -> dict:
    path = os.path.expanduser(params.get("path") or "")
    ok = bool(path) and os.path.exists(path)
    return {"ok": True, "achieved": ok, "evidence": f"exists={ok}", "detail": path}


def _file_contains(params: dict, injected: dict | None) -> dict:
    path = os.path.expanduser(params.get("path") or "")
    if not path or not os.path.exists(path):
        return {"ok": True, "achieved": False, "evidence": "파일 없음", "detail": path}
    content = open(path, encoding="utf-8", errors="replace").read()
    r = _judge(content, _expected_texts(params, injected))
    return {"ok": True, "achieved": r["achieved"], "evidence": r["evidence"], "detail": path}


def _clipboard_contains(params: dict, injected: dict | None) -> dict:
    pbpaste = shutil.which("pbpaste")
    if not pbpaste:
        return {"ok": False, "achieved": None, "evidence": "", "detail": "pbpaste 없음"}
    content = subprocess.run([pbpaste], capture_output=True, text=True, timeout=8).stdout
    r = _judge(content, _expected_texts(params, injected))
    return {"ok": True, "achieved": r["achieved"], "evidence": r["evidence"], "detail": "clipboard"}


def _osa_escape(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _notes_contains(params: dict, injected: dict | None) -> dict:
    """Apple Notes 노트 본문을 읽어(plaintext) 기대 텍스트 대조. params.title=읽을 노트 이름."""
    osa = shutil.which("osascript")
    if not osa:
        return {"ok": False, "achieved": None, "evidence": "", "detail": "osascript 없음"}
    title = params.get("title") or ""
    sel = f'(notes whose name is "{_osa_escape(title)}")' if title else 'notes'
    # ★'Recently Deleted'(휴지통) 노트 제외 — 삭제된 노트 내용이 '존재'로 잡히면 거짓PASS.
    #   (라이브 측정에서 발견: notes whose name 은 전 계정+휴지통을 봐서 삭제된 노트가 ~30일 남음.
    #    전역 매칭은 유지하고 note 의 container 가 'Recently Deleted'(휴지통)인 것만 제외한다.
    #    (활성 노트는 폴더 미지정 생성 시 container 이름이 빈 문자열일 수 있어 cn=="" 는 포함 유지.)
    script = (
        'tell application "Notes"\n'
        '  set out to ""\n'
        f'  repeat with n in {sel}\n'
        '    set cn to ""\n'
        '    try\n'
        '      set cn to (name of (container of n))\n'
        '    end try\n'
        '    if cn is not "Recently Deleted" and cn is not "최근 삭제된 항목" then\n'
        '      set out to out & (plaintext of n) & "\\n"\n'
        '    end if\n'
        '  end repeat\n'
        '  return out\n'
        'end tell\n')
    r = subprocess.run([osa, "-e", script], capture_output=True, text=True, timeout=20)
    content = (r.stdout or "")
    res = _judge(content, _expected_texts(params, injected))
    note = f"노트 '{title}'" if title else "전체 노트"
    return {"ok": True, "achieved": res["achieved"], "evidence": f"{note}: {res['evidence']}",
            "detail": f"{len(content)}자 읽음"}


def _reminders_contains(params: dict, injected: dict | None) -> dict:
    """Apple Reminders 항목 이름을 읽어 기대 텍스트 대조. params.list=특정 목록(생략=전체)."""
    osa = shutil.which("osascript")
    if not osa:
        return {"ok": False, "achieved": None, "evidence": "", "detail": "osascript 없음"}
    lst = params.get("list") or ""
    sel = f'reminders of list "{_osa_escape(lst)}"' if lst else "reminders"
    script = ('tell application "Reminders"\n  set out to ""\n'
              f'  repeat with r in {sel}\n    set out to out & (name of r) & "\\n"\n  end repeat\n'
              '  return out\nend tell\n')
    r = subprocess.run([osa, "-e", script], capture_output=True, text=True, timeout=30)
    res = _judge(r.stdout or "", _expected_texts(params, injected))
    where = f"목록 '{lst}'" if lst else "전체 미리알림"
    return {"ok": True, "achieved": res["achieved"], "evidence": f"{where}: {res['evidence']}",
            "detail": f"{len(r.stdout or '')}자"}


def _app_running(params: dict) -> dict:
    """앱이 실행 중인가(+선택 frontmost). params.app(이름), params.frontmost=True 면 맨앞도 확인."""
    osa = shutil.which("osascript")
    app = params.get("app") or params.get("name") or ""
    if not osa or not app:
        return {"ok": True, "achieved": None, "evidence": "osascript/app 미지정", "detail": app}
    run = subprocess.run([osa, "-e",
        f'tell application "System Events" to return (exists (process "{_osa_escape(app)}"))'],
        capture_output=True, text=True, timeout=10)
    running = (run.stdout or "").strip().lower() == "true"
    if params.get("frontmost"):
        fr = subprocess.run([osa, "-e",
            'tell application "System Events" to return (name of first process whose frontmost is true)'],
            capture_output=True, text=True, timeout=10)
        front = _norm(fr.stdout or "") == _norm(app)
        return {"ok": True, "achieved": bool(running and front),
                "evidence": f"running={running} frontmost={front}", "detail": app}
    return {"ok": True, "achieved": running, "evidence": f"running={running}", "detail": app}


def _defaults_equals(params: dict) -> dict:
    """시스템/앱 설정 검증 — `defaults read <domain> <key>`. value 주면 일치 확인, 없으면 존재만."""
    domain, key = params.get("domain") or "", params.get("key") or ""
    if not domain or not key:
        return {"ok": True, "achieved": None, "evidence": "domain/key 미지정", "detail": ""}
    r = subprocess.run(["defaults", "read", domain, key], capture_output=True, text=True, timeout=8)
    if r.returncode != 0:
        return {"ok": True, "achieved": False, "evidence": "키 없음/읽기 실패", "detail": f"{domain} {key}"}
    actual = (r.stdout or "").strip()
    expected = params.get("value", params.get("text"))
    if expected is None:
        return {"ok": True, "achieved": bool(actual), "evidence": f"value={actual[:40]}", "detail": f"{domain} {key}"}
    ach = _norm(actual) == _norm(str(expected)) or _norm(str(expected)) in _norm(actual)
    return {"ok": True, "achieved": ach, "evidence": f"{actual[:24]} {'==' if ach else '≠'} {expected}",
            "detail": f"{domain} {key}"}


def _screen_contains(params: dict, injected: dict | None) -> dict:
    """화면(또는 맨앞 창)을 OCR해 기대 텍스트가 *보이는지* 결정론 확인. front_window_only=True 가능."""
    from . import ocr
    if not ocr.available():
        return {"ok": False, "achieved": None, "evidence": "", "detail": "ocrground 미빌드"}
    try:
        items = ocr.ocr_screen(front_window_only=bool(params.get("front_window_only")))
    except Exception as e:
        return {"ok": False, "achieved": None, "evidence": "", "detail": f"OCR 실패: {type(e).__name__}"}
    text = "\n".join(str(it.get("text", "")) for it in items)
    res = _judge(text, _expected_texts(params, injected))
    return {"ok": True, "achieved": res["achieved"], "evidence": f"화면OCR: {res['evidence']}",
            "detail": f"{len(items)} 조각"}


def _path_absent(params: dict) -> dict:
    """파일이 *없는가*(삭제 확인) — file_exists 의 보완(삭제 성공을 achieved=True 로)."""
    path = os.path.expanduser(params.get("path") or "")
    absent = bool(path) and not os.path.exists(path)
    return {"ok": True, "achieved": absent, "evidence": f"absent={absent}", "detail": path}


def _clipboard_png_sha256():
    """시스템 클립보드의 PNG를 *독립적으로* 읽어 (sha256, 바이트수). 없으면 (None, 0)."""
    try:
        from AppKit import NSPasteboard, NSPasteboardTypePNG
    except Exception:
        return None, 0
    import hashlib
    data = NSPasteboard.generalPasteboard().dataForType_(NSPasteboardTypePNG)
    if data is None:
        return None, 0
    b = bytes(data)
    return hashlib.sha256(b).hexdigest(), len(b)


def _clipboard_image(params: dict, injected: dict | None) -> dict:
    """클립보드에 *이미지(PNG)*가 있나(타입) + (기대 해시 있으면) 그 이미지 맞나(내용). from_injected/sha256."""
    sha, n = _clipboard_png_sha256()
    present = sha is not None
    exp = params.get("sha256")
    if not exp:
        vals = _injected_values(injected, params.get("from_injected", "sha256"))
        exp = vals[0] if vals else None
    if exp:
        ach = bool(present and sha == exp)
        ev = f"png={present} hash_match={ach}"
    else:
        ach = present
        ev = f"png present={present} ({n}B)"
    return {"ok": True, "achieved": ach, "evidence": ev, "detail": "clipboard:png"}


def _http_received(params: dict, injected: dict | None) -> dict:
    """mock 서버(netmock.MockServer)가 *실제로 받은* 요청을 읽어, 기대 전송이 일어났는지 대조.
    SUT 의 '보냈어요' 자기보고가 아니라 *서버가 받은 것*이 ground truth.
    params: record_path(MockServer 기록 JSONL) / method·path(선택 필터) / text·from_injected(body에 있어야 할 내용)."""
    rec_path = os.path.expanduser(params.get("record_path") or "")
    if not rec_path or not os.path.exists(rec_path):
        return {"ok": True, "achieved": False, "evidence": "기록 파일 없음(요청 0)", "detail": rec_path}
    reqs = []
    for line in open(rec_path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line:
            try:
                reqs.append(json.loads(line))
            except Exception:
                pass
    if not reqs:
        return {"ok": True, "achieved": False, "evidence": "수신 요청 0건", "detail": rec_path}
    want_method = (params.get("method") or "").upper()
    want_path = params.get("path") or ""
    expected = _expected_texts(params, injected)
    matched = 0
    for r in reqs:
        if want_method and (r.get("method") or "").upper() != want_method:
            continue
        if want_path and want_path not in (r.get("path") or ""):
            continue
        if expected:
            blob = (r.get("path") or "") + " " + (r.get("body") or "")
            if _judge(blob, expected).get("achieved") is not True:
                continue
        matched += 1
    return {"ok": True, "achieved": matched > 0,
            "evidence": f"수신 {len(reqs)}건·매칭 {matched} (method={want_method or '*'} path~{want_path or '*'} body기대 {len(expected)})",
            "detail": rec_path}
