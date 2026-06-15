"""1인칭 프레임 → 장면 캡션 + 주요 객체 (온디바이스 VLM).

ground.py 가 쓰는 Qwen2.5-VL-3B(이미 캐시됨)를 *캡셔너*로 재사용한다. ground 의 GUI 그라운딩
상태와 섞이지 않게 자체 lazy 로더를 둔다(둘 다 같은 모델이라 동시 로드해도 한 벌만 메모리에 뜨면
이상적이지만, 분리 globals 로 안전하게). UI-TARS(7B)와는 *동시 로드 금지* — 여기선 안 쓴다.

출력: {"caption": str, "objects": [str, ...]} — 객체 기억(egomem)의 검색 키가 된다.
"""

from __future__ import annotations

import json
import re

MODEL = "Qwen/Qwen2.5-VL-3B-Instruct"
_MAXPIX = 360_000          # 1인칭 장면은 GUI보다 거칠어도 됨 → 픽셀 캡 낮춰 속도·발열↓

_model = None
_proc = None


def available() -> bool:
    try:
        import torch  # noqa: F401
        from transformers import Qwen2_5_VLForConditionalGeneration  # noqa: F401
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
        _proc = AutoProcessor.from_pretrained(MODEL, max_pixels=_MAXPIX)
        _model._dev = dev
    return _model, _proc


_PROMPT = (
    "너는 웨어러블 비서의 '눈'이다. 이 1인칭 사진을 보고 ONLY JSON으로만 답하라: "
    '{"caption": "<장면을 묘사하는 한 문장(한국어)>", '
    '"objects": ["<눈에 띄는 객체1>", "<객체2>", ...]}. '
    "객체는 검색가능한 구체적 한국어 명사로 최대 8개(예: '빨간 머그', '노트북', '문', '비상구 표지'). "
    "보이는 것만 사실대로, 지어내지 마라."
)


def caption(img_path: str) -> dict:
    """프레임 1장 → {caption, objects}. 실패 시 빈 구조(graceful)."""
    import torch
    from qwen_vl_utils import process_vision_info

    model, proc = _load()
    msg = [{"role": "user", "content": [
        {"type": "image", "image": img_path},
        {"type": "text", "text": _PROMPT}]}]
    txt = proc.apply_chat_template(msg, tokenize=False, add_generation_prompt=True)
    ii, vi = process_vision_info(msg)
    inp = proc(text=[txt], images=ii, videos=vi, padding=True, return_tensors="pt").to(model._dev)
    with torch.no_grad():
        g = model.generate(**inp, max_new_tokens=128, do_sample=False,
                           repetition_penalty=1.15, no_repeat_ngram_size=3)
    out = proc.batch_decode([gg[len(i):] for i, gg in zip(inp.input_ids, g)],
                            skip_special_tokens=True)[0]
    try:
        del inp, g
        if model._dev == "mps":
            torch.mps.empty_cache()
    except Exception:
        pass
    return _parse(out)


_VERIFY_PROMPT = (
    "이 사진을 *엄격하고 회의적으로* 다시 본다. 아래 각 항목이 이 사진에 **실제로 또렷이 보이는지** "
    "판단하라. 조금이라도 안 보이거나 애매하면 반드시 '아니오'(기본=의심). ONLY JSON 으로만 답하라: "
    '{"<항목>": "예|아니오|애매", ...}.\n항목: '
)


def verify_objects(img_path: str, names: list) -> dict:
    """주장된 객체들이 *정말 보이는지* 독립·회의적으로 재검(한 번의 VLM 호출). {name: 'yes'|'no'|'unsure'}.

    캡셔너와 같은 모델이지만 *회의적 grounded yes/no* 프레임(열린 생성보다 환각이 적음) — RUBI식
    '자기보고 불신, 독립 재검증'. (더 강한 독립성=다른 모델은 후속 업그레이드.)"""
    names = [n for n in dict.fromkeys([str(n).strip() for n in names]) if n]
    if not names:
        return {}
    import torch
    from qwen_vl_utils import process_vision_info

    model, proc = _load()
    msg = [{"role": "user", "content": [
        {"type": "image", "image": img_path},
        {"type": "text", "text": _VERIFY_PROMPT + ", ".join(names)}]}]
    txt = proc.apply_chat_template(msg, tokenize=False, add_generation_prompt=True)
    ii, vi = process_vision_info(msg)
    inp = proc(text=[txt], images=ii, videos=vi, padding=True, return_tensors="pt").to(model._dev)
    with torch.no_grad():
        g = model.generate(**inp, max_new_tokens=128, do_sample=False,
                           repetition_penalty=1.15, no_repeat_ngram_size=3)
    out = proc.batch_decode([gg[len(i):] for i, gg in zip(inp.input_ids, g)],
                            skip_special_tokens=True)[0]
    try:
        del inp, g
        if model._dev == "mps":
            torch.mps.empty_cache()
    except Exception:
        pass
    return _parse_votes(out, names)


def _parse_votes(text: str, names: list) -> dict:
    votes: dict = {}
    raw = {}
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e > s:
        try:
            raw = json.loads(text[s:e + 1])
        except json.JSONDecodeError:
            raw = {}
    for n in names:
        v = str(raw.get(n, "")).strip()
        if not v:                                  # 키 못 찾으면 항목 근처 텍스트로 근사
            m = re.search(re.escape(n) + r'"?\s*[:=]\s*"?\s*(예|아니오|애매|yes|no|unsure)', text)
            v = m.group(1) if m else ""
        votes[n] = _norm_vote(v)
    return votes


def _norm_vote(v: str) -> str:
    v = (v or "").strip().lower()
    if v in ("예", "yes", "y", "true"):
        return "yes"
    if v in ("아니오", "아니요", "no", "n", "false"):
        return "no"
    return "unsure"


def _parse(text: str) -> dict:
    # 1) 정상 JSON 우선
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e > s:
        try:
            d = json.loads(text[s:e + 1])
            cap = str(d.get("caption", "")).strip()
            objs = _dedup([str(o) for o in (d.get("objects") or [])])
            if cap or objs:
                return {"caption": cap, "objects": objs}
        except json.JSONDecodeError:
            pass
    # 2) 잘리거나 반복으로 깨진 출력도 건진다(정규식 살베지 — 반복은 dedup이 제거)
    cap_m = re.search(r'"caption"\s*:\s*"([^"]*)"', text)
    caption = cap_m.group(1).strip() if cap_m else ""
    objects: list = []
    obj_m = re.search(r'"objects"\s*:\s*\[(.*)', text, re.DOTALL)
    if obj_m:
        objects = _dedup(re.findall(r'"([^"]+)"', obj_m.group(1)))
    if not caption and not objects:           # 최후: 첫 문장만
        caption = re.split(r'[.\n]', re.sub(r"\s+", " ", text).strip())[0][:160]
    return {"caption": caption, "objects": objects}


def _dedup(items: list) -> list:
    seen, out = set(), []
    for o in items:
        o = str(o).strip()
        if o and o not in seen:
            seen.add(o)
            out.append(o)
        if len(out) >= 8:
            break
    return out
