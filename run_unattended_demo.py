#!/usr/bin/env python3
"""무인(unattended) 실연 — VISIO가 *사람 없이* 한 사이클을 돈다: 자극주입 → SUT 실행 →
결정론 판정(실제 파일 읽음, 자기보고 무시) → 리포트 기록 → *직전 결과와 회귀 비교*.

launchd(OS)가 이 스크립트를 *호출*한다(사람이 매번 안 누름). 기능 = '클립보드 텍스트 → 파일 저장'.
VISIO 코어 조립: triggers(주입) + 서브프로세스(실행) + probes(authoritative식 결정론 판정) + 회귀.
"""
import json
import os
import subprocess
import sys
import time

from sapphi import probes, triggers

HOME = os.path.dirname(os.path.abspath(__file__))
DIR = "/tmp/visio_unattended"
IN = f"{DIR}/input.txt"
OUT = f"{DIR}/note.txt"
REPORT = f"{DIR}/report.log"
STATE = f"{DIR}/last_verdict.json"
NOTE = "회의록: 예산 승인, 개발자 2명 채용, 출시 6월 30일"


def main() -> int:
    os.makedirs(DIR, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    # 1) 자극 주입(입력 파일에 노트) — ground truth = injected. 순수 파일(무세션 OK).
    inj = triggers.produce("file", {"dir": DIR, "name": "input.txt", "content": NOTE})

    # 2) SUT 실행(입력파일 → 출력파일). 사람 개입 0.
    env = {**os.environ, "PYTHONPATH": HOME, "VISIO_NOTE_IN": IN, "VISIO_NOTE_OUT": OUT}
    subprocess.run([sys.executable, "sut/note_saver.py"], cwd=HOME, env=env,
                   capture_output=True, text=True, timeout=30)

    # 3) 결정론 판정 — 출력 파일에 노트가 *실제로* 저장됐나(authoritative: 자기보고 무시, 파일 직접 읽음)
    r = probes.probe("file_contains", {"path": OUT, "from_injected": "content"}, inj["injected"])
    verdict = "PASS" if r.get("achieved") else "FAIL"

    # 4) 회귀 비교 — 직전 결과와 다르면 플래그(시간이 지나도 지켜봄)
    prev = None
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE)).get("verdict")
        except Exception:
            pass
    regr = f"  ⚠️ 회귀: 직전 {prev} → 이번 {verdict}" if (prev and prev != verdict) else ""
    json.dump({"verdict": verdict, "ts": ts}, open(STATE, "w"))

    # 5) 리포트 기록 — 사람 없이 남긴다(launchd가 부른 흔적)
    line = f"[{ts}] {verdict}  ({r.get('evidence')}){regr}\n"
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(line)
    print(line.strip())
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
