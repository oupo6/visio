"""RUBI — perception(인지) 검증자.

RUBI의 본질은 *워커의 자기보고를 안 믿고 독립적으로 "진짜 맞아?"를 검증*하는 것. 여기서 워커는
egocentric 캡셔너(VLM)다. VLM은 환각한다(예: '비상구 표지' → '비상수류탄'). 그 환각이 그대로
기억에 박히면 비서가 *자신있게 틀린다.* 그래서 각 주장 객체를 독립 신호로 교차검증해 신뢰도를
매기고, 근거 없는 것은 flag/배제한다. = perception의 trust layer.

신호(독립적일수록 좋음):
  ① OCR 교차검증 — 장면의 *실제 렌더 텍스트*(reliable ground truth)에 객체가 나오나.
  ② 회의적 재검(VLM grounded yes/no) — "정말 보여?"에 기본=의심으로 재판단(egocap.verify_objects).
  ③ 캡션 일치 — 캡션 문장에 객체가 언급되나(약한 자기일관성 신호).
(향후 ④ 교차프레임 corroboration, ⑤ *다른* 모델 검증으로 독립성 강화.)
"""

from __future__ import annotations


# 객체 신뢰도: OCR(강) > 재검'예' > 캡션. 재검'아니오'=환각.
def _confidence(in_ocr: bool, in_caption: bool, vote: str) -> float:
    if in_ocr:
        return 0.9                       # 장면 텍스트에 실재 = 가장 신뢰
    if vote == "no":
        return 0.1                       # 독립 재검이 부정 = 환각
    if vote == "yes":
        return 0.85 if in_caption else 0.75
    # vote == 'unsure'
    return 0.5 if in_caption else 0.35


def verify_keyframe(image_path: str, caption: str, objects: list, ocr_text: str,
                    recheck: bool = True, keep_thresh: float = 0.6) -> dict:
    """한 keyframe의 인지 주장을 검증한다.

    반환: {"objects":[{name,confidence,in_ocr,vote}], "verified":[name..], "flags":[name..],
           "caption_trust": float}.
    verified = keep_thresh 이상(기억의 *검색 키*로 쓸 신뢰 객체). flags = 환각(재검 부정).
    """
    ocr_low = (ocr_text or "").lower()
    cap_low = (caption or "").lower()
    names = [n for n in dict.fromkeys([str(o).strip() for o in (objects or [])]) if n]

    votes = {}
    if recheck and names:
        try:
            from sapphi import egocap
            if egocap.available():
                votes = egocap.verify_objects(image_path, names)
        except Exception:
            votes = {}

    detail, verified, flags = [], [], []
    for n in names:
        toks = [t for t in n.lower().split() if t]
        in_ocr = bool(toks) and all(t in ocr_low for t in toks) or n.lower() in ocr_low
        in_cap = n.lower() in cap_low
        vote = votes.get(n, "unsure" if recheck else "skipped")
        v = vote if vote in ("yes", "no", "unsure") else "unsure"
        conf = _confidence(in_ocr, in_cap, v) if (recheck or in_ocr or in_cap) else 0.5
        detail.append({"name": n, "confidence": round(conf, 2), "in_ocr": in_ocr, "vote": vote})
        if conf >= keep_thresh:
            verified.append(n)
        elif v == "no":
            flags.append(n)

    trust = round(sum(d["confidence"] for d in detail) / len(detail), 2) if detail else (
        0.7 if ocr_text else 0.4)
    return {"objects": detail, "verified": verified, "flags": flags, "caption_trust": trust}
