#!/usr/bin/env python3
"""SUT(버그 심은 기능) — 본문을 파일로 저장하되 '<<RESET>>' 토큰 *이후를 버린다*(조용한 데이터 손실).

end-to-end 학습 체인 확인용: VISIO가 이 실패를 잡아 *자동으로 교훈을 기록*하고, 다음 설계가 그걸
회상해 '<<RESET>>' 케이스를 *스스로* 설계하는지 본다. 자기보고는 항상 '성공'(거짓).
입력: VISIO_INJECTED(env, JSON {content|notifications}) / 출력: VISIO_NOTE_OUT 경로.
"""
import json
import os
import sys

raw = os.environ.get("VISIO_INJECTED", "").strip()
if not raw and not sys.stdin.isatty():
    raw = sys.stdin.read().strip()
try:
    p = json.loads(raw) if raw else {}
except json.JSONDecodeError:
    p = {}

content = p.get("content")
if content is None:
    notes = p.get("notifications") or [{}]
    content = (notes[0].get("body", "") if isinstance(notes[0], dict) else str(notes[0]))
content = content or ""

out = os.environ.get("VISIO_NOTE_OUT", "/tmp/reset_out.txt")
saved = content.split("<<RESET>>")[0] if "<<RESET>>" in content else content   # ★버그: 토큰 이후 폐기
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    f.write(saved)
print(f"[SUT] 저장 완료 ✅ ({len(saved)}자)")   # 자기보고 — VISIO는 안 믿음
