#!/usr/bin/env python3
"""SUT(기능): 입력 파일의 텍스트를 출력 파일로 저장. (무인 실연용 — 순수 파일·GUI/세션 불요)

VISIO_BREAK=1 이면 *조용히 잘라서* 저장(회귀 버그 모사 — 보고는 성공인데 실제는 truncate).
경로: 입력=VISIO_NOTE_IN, 출력=VISIO_NOTE_OUT. (클립보드 아님 → launchd 등 무세션에서도 동작.)
"""
import os
import sys

IN = os.environ.get("VISIO_NOTE_IN", "/tmp/visio_unattended/input.txt")
OUT = os.environ.get("VISIO_NOTE_OUT", "/tmp/visio_unattended/note.txt")


def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    text = open(IN, encoding="utf-8").read() if os.path.exists(IN) else ""
    if os.environ.get("VISIO_BREAK"):
        text = text[:5]                      # ★조용한 회귀 버그: 5자만 저장
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[SUT] 저장 완료: '{text[:40]}'")  # 자기보고 — VISIO는 안 믿음
    return 0


if __name__ == "__main__":
    sys.exit(main())
