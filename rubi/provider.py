"""LLM provider 추상화 — claude CLI(기본, 로그인·무료) ↔ OpenAI API(대체, 키·과금) ↔ local.

이 프로젝트의 모든 LLM 호출은 결국 *프롬프트(+선택적 이미지) → JSON dict* 다.
그 단일 형태를 여기 한 곳에서 provider 별로 구현한다. 호출처(brain·verify·emeri·taskspec…)는
이 모듈만 알면 되고, claude/openai 차이(인증·이미지 인코딩·JSON 강제)는 전부 여기서 흡수한다.

선택:
  SAPPHI_PROVIDER = "claude"(기본) | "openai" | "local"
  claude 가 한도/오류로 막히면 SAPPHI_PROVIDER=openai 로 바꾸면 코드 변경 없이 전부 GPT 로.

모델 별칭(claude 모델명을 그대로 넘겨도 openai 로 매핑):
  claude-sonnet-* / -opus-*  → OPENAI_MODEL(기본 gpt-4o)        # 추론·비전 본체
  claude-haiku-*             → OPENAI_MODEL_MINI(기본 gpt-4o-mini) # 값싼 단계
  (env 로 덮어쓰기 가능)

인증(키)은 *코드가 만들지 않는다* — 사용자가 OPENAI_API_KEY 환경변수로 넣는다.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess


def provider_name() -> str:
    return (os.environ.get("SAPPHI_PROVIDER") or "claude").strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
# 공통 JSON 추출 — 모델이 ```json 펜스/잡설을 섞어도 객체 하나를 건진다.
# ─────────────────────────────────────────────────────────────────────────────
def extract_json(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        text = text[s : e + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# claude CLI (기본) — 로그인 세션, 키 불필요. 이미지는 --add-dir 로 경로 전달.
# ─────────────────────────────────────────────────────────────────────────────
def _claude_complete(prompt: str, model: str, image_path: str | None,
                     timeout: int, retries: int,
                     session_id: str | None = None, resume: bool = False) -> dict:
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json"]
    # ★세션: session_id 가 있으면 *유상태*로 — 첫 콜은 --session-id 로 만들고, 이후 --resume 로 이어간다.
    #   거대한 시스템 프롬프트를 매번 새로 안 보내고(캐시 재사용), 워커가 이전 화면·행동을 기억한다.
    #   session_id 가 없으면(=세션 비활성) 기존처럼 비저장 1회성 호출.
    if session_id and resume:
        cmd += ["--resume", session_id]
    elif session_id:
        cmd += ["--session-id", session_id]
    else:
        cmd += ["--no-session-persistence"]
    cwd = None
    if image_path and os.path.exists(image_path):
        d = os.path.dirname(os.path.abspath(image_path))
        cmd += ["--add-dir", d, "--allowedTools", "Read"]
        cwd = d
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
            if proc.returncode != 0:
                raise RuntimeError(f"claude CLI 실패(rc={proc.returncode}): {proc.stderr[:300]}")
            return extract_json(json.loads(proc.stdout).get("result", ""))
        except subprocess.TimeoutExpired as e:
            last_err = e
            print(f"      ⏳ claude 호출 {timeout}s 초과 — 재시도 {attempt + 1}/{retries}", flush=True)
            continue
    raise last_err if last_err else subprocess.TimeoutExpired(cmd, timeout)


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI API (대체) — 키·과금. 이미지는 base64 data URL 로 인라인. JSON 강제.
# ─────────────────────────────────────────────────────────────────────────────
def _openai_model_for(model: str) -> str:
    m = (model or "").lower()
    if "haiku" in m:
        return os.environ.get("OPENAI_MODEL_MINI", "gpt-4o-mini")
    return os.environ.get("OPENAI_MODEL", "gpt-4o")


def _openai_complete(prompt: str, model: str, image_path: str | None,
                     timeout: int, retries: int) -> dict:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY 환경변수가 없다 — 키를 export 한 뒤 다시 시도하라.")
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError(f"openai 패키지 미설치({e}) — .venv/bin/pip install openai") from e

    client = OpenAI(timeout=timeout, max_retries=retries)
    content: list[dict] = [{"type": "text", "text": prompt}]
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})
    resp = client.chat.completions.create(
        model=_openai_model_for(model),
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},   # JSON 강제(파싱 안정)
        temperature=0,
    )
    return extract_json(resp.choices[0].message.content or "")


# ─────────────────────────────────────────────────────────────────────────────
# Local LLM — 민감 데이터 전용. Ollama 우선, mlx-lm 폴백.
# ─────────────────────────────────────────────────────────────────────────────
def _ollama_host() -> str:
    host = (os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434").strip()
    if not host.startswith("http"):
        host = "http://" + host
    return host.rstrip("/")


def _ollama_models(timeout: float = 1.5) -> set[str] | None:
    """현재 ollama 서버가 보유한 모델명 집합. 서버 미실행/연결불가면 None."""
    import urllib.request

    try:
        with urllib.request.urlopen(_ollama_host() + "/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None
    names: set[str] = set()
    for m in data.get("models") or []:
        n = m.get("name") or m.get("model") or ""
        if n:
            names.add(n)
            names.add(n.split(":", 1)[0])   # base name 도 매칭 허용(gemma3:4b → gemma3)
    return names


def _ollama_has_model(model: str, models: set[str] | None) -> bool:
    if not models:
        return False
    return model in models or model.split(":", 1)[0] in models


def _ollama_generate(model: str, prompt: str, image_path: str | None, timeout: int) -> str:
    """Ollama HTTP /api/generate — *멀티모달*(이미지 base64) 지원. Gemma 4 같은 비전 로컬모델용."""
    import urllib.request

    payload = {"model": model, "prompt": prompt, "stream": False}
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            payload["images"] = [base64.b64encode(f.read()).decode()]
    req = urllib.request.Request(
        _ollama_host() + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    return (data.get("response") or "").strip()


def complete_local(prompt: str, model: str | None = None, timeout: int = 120,
                   image_path: str | None = None) -> str:
    model = model or os.environ.get("SAPPHI_LOCAL_MODEL", "gemma4")
    ollama = shutil.which("ollama")
    if ollama:
        # 서버/모델 실재를 먼저 확인 — 없는 모델로 호출하면 수 GB를 *자동 다운로드*해 작업이 멈춘다.
        # 미실행/미설치는 빠르게 실패시켜 명확히 알린다.
        models = _ollama_models()
        if models is None:
            raise RuntimeError("ollama 서버 미실행 — `ollama serve` 를 먼저 실행하라(민감 데이터는 클라우드로 안 보냄).")
        if not _ollama_has_model(model, models):
            raise RuntimeError(f"로컬 모델 '{model}' 미설치 — `ollama pull {model}` 하거나 "
                               f"SAPPHI_LOCAL_MODEL 을 설치된 모델로 지정하라.")
        if image_path:
            # 비전 입력 → HTTP API(멀티모달). 텍스트 전용은 기존 `ollama run` 경로 유지.
            out = _ollama_generate(model, prompt, image_path, timeout)
            if out:
                return out
            raise RuntimeError(f"로컬 멀티모달 모델 '{model}' 빈 응답 — 비전 지원 모델인지 확인(예: gemma4).")
        proc = subprocess.run([ollama, "run", model, prompt], capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return (proc.stdout or "").strip()
        raise RuntimeError((proc.stderr or proc.stdout or "ollama local model failed")[:400])
    if image_path:
        raise RuntimeError("로컬 이미지 입력은 ollama(멀티모달 모델)가 필요하다 — ollama 설치 후 gemma4 pull.")

    # mlx-lm CLI/module fallback. The user can set SAPPHI_LOCAL_MODEL to a local
    # MLX model path or repo id.
    py = os.environ.get("SAPPHI_PYTHON") or "python"
    mlx_model = os.environ.get("SAPPHI_MLX_MODEL") or os.environ.get("SAPPHI_LOCAL_MODEL_MLX") or model
    cmd = [py, "-m", "mlx_lm.generate", "--model", mlx_model, "--prompt", prompt, "--max-tokens", "512"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode == 0 and (proc.stdout or "").strip():
        return (proc.stdout or "").strip()
    raise RuntimeError((proc.stderr or proc.stdout or "no local LLM backend available")[:400])


def _local_complete_json(prompt: str, model: str, timeout: int, retries: int,
                         image_path: str | None = None) -> dict:
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            return extract_json(complete_local(prompt, model, timeout, image_path=image_path))
        except Exception as e:
            last = e
    raise last or RuntimeError("local provider failed")


# ─────────────────────────────────────────────────────────────────────────────
# 단일 진입점 — 호출처는 이것만 쓴다.
# ─────────────────────────────────────────────────────────────────────────────
def complete_json(prompt: str, model: str, image_path: str | None = None,
                  timeout: int = 240, retries: int = 2,
                  session_id: str | None = None, resume: bool = False,
                  provider: str | None = None) -> dict:
    """프롬프트(+선택 이미지) → JSON dict. provider 는 SAPPHI_PROVIDER 로 고른다.
    provider= 로 *이번 호출만* override 가능(예: VISIO가 민감 케이스 판정만 local Gemma4로).
    session_id/resume: claude 세션 재사용(유상태+캐시). openai 는 세션 미지원이라 무시.
    ★local 은 이제 *멀티모달*(Gemma4 등 비전모델) — 이미지 입력 지원(Ollama /api/generate)."""
    p = (provider or provider_name())
    if p == "openai":
        return _openai_complete(prompt, model, image_path, timeout, retries)
    if p == "local":
        return _local_complete_json(prompt, model, timeout, retries, image_path=image_path)
    return _claude_complete(prompt, model, image_path, timeout, retries, session_id, resume)


def available(provider: str | None = None) -> tuple[bool, str]:
    """현재(또는 지정) provider 가 호출 가능한지 + 사유."""
    p = (provider or provider_name())
    if p == "local":
        model = os.environ.get("SAPPHI_LOCAL_MODEL", "gemma4")
        if shutil.which("ollama"):
            models = _ollama_models()
            if models is None:
                return False, f"ollama 설치됨이나 서버 미실행 — `ollama serve` (모델: {model})"
            if _ollama_has_model(model, models):
                return True, f"ollama ({model}) 준비됨"
            return False, f"ollama 실행중이나 '{model}' 미설치 — `ollama pull {model}`"
        try:
            import mlx_lm  # noqa: F401
        except ImportError:
            return False, "local LLM 없음(ollama 없음, mlx-lm 미설치)"
        return True, f"mlx-lm ({os.environ.get('SAPPHI_MLX_MODEL') or os.environ.get('SAPPHI_LOCAL_MODEL_MLX') or 'model env 필요'})"
    if p == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY 없음"
        try:
            import openai  # noqa: F401
        except ImportError:
            return False, "openai 패키지 미설치"
        return True, f"openai ({os.environ.get('OPENAI_MODEL', 'gpt-4o')})"
    if shutil.which("claude") is None:
        return False, "claude CLI 없음"
    return True, "claude CLI (로그인)"


def _cli_json(prompt: str, model: str, image_path: str | None = None,
              provider: str | None = None) -> dict:
    """프롬프트(+선택 이미지) → JSON dict. 호출처 단일 진입점(구 sapphi_runner._cli_json 이전)."""
    return complete_json(prompt, model, image_path=image_path, provider=provider)
