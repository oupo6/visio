#!/usr/bin/env python3
"""VISIO 라이브 e2e (역할 분리): 에이전트가 만든 '카톡 자기채팅 요약→Notes' 기능을 VISIO가 자율 검증.

  VISIO(SAPPHI 손=테스터) = *모든 맥 조작*: 카톡 열기·자기채팅 열기(더블클릭)·독립 ground-truth 읽기·
                            Notes 열기·그 노트 띄우기·판정. SUT를 안 믿고 다 제 손으로.
  SUT(기능)            = 보이는 대화 요약→Notes 조용히 저장. 네비·띄우기 0.
  사용자는 루프에 없음(요청만). 네비(1~5)=로컬·결정론 / VISIO 검증읽기(6)+판단(7)=Claude(신뢰 오라클).
  ★비용: 클릭경로는 AX/OCR 로컬(이미지 클라우드 0). 검증 때만 이미지 1장 Claude(루프 아님·소액)로 천장 회피.
"""
import os
import subprocess
import time

from sapphi import act, perceive
from rubi import provider, read

OUT = "visio_out/kakao_e2e"
os.makedirs(OUT, exist_ok=True)
SELF_CHAT = os.environ.get("VISIO_SELF_CHAT", "나와의 채팅")   # 자기채팅 목록 라벨(환경변수로 본인 라벨 지정)


def cleanup_notes():
    subprocess.run(["osascript", "-e",
        'tell application "Notes"\n repeat 20 times\n set m to (notes whose name is "📥 카톡 요약")\n'
        ' if (count of m) is 0 then exit repeat\n delete item 1 of m\n end repeat\nend tell'],
        capture_output=True)


def visio_open_chat(name):
    """VISIO(손)가 카톡 열고 그 채팅을 더블클릭해 연다 — 테스터의 네비게이션."""
    perceive.open_app("KakaoTalk")
    perceive.focus_app("KakaoTalk")
    time.sleep(1.2)
    click = act._smart_click(name)          # OCR로 목록서 채팅명 찾아 클릭(선택)
    bb = getattr(click, "bbox", None)
    if bb:
        act._quartz_double_click(int(bb[0]), int(bb[1]))   # 더블클릭=대화 열기
    time.sleep(2.0)
    return getattr(click, "ok", False), bb


print("=" * 60)
print("VISIO 라이브 e2e — 검증: 에이전트의 '카톡 자기채팅 요약→Notes'")
print("=" * 60)
cleanup_notes()

print("\n▶ [VISIO/손] 카톡 + 자기채팅 열기 (테스터가 드라이브)")
ok, bb = visio_open_chat(SELF_CHAT)
print(f"  채팅 열기 ok={ok} bbox={bb}")

print("\n▶ [SUT/기능] 열린 대화 요약→Notes (네비·띄우기 0)")
sut = subprocess.run([".venv/bin/python", "sut/kakao_to_notes.py"], capture_output=True, text=True,
                     timeout=200, env={**os.environ, "PYTHONPATH": os.getcwd()})
print("  " + (sut.stdout or "").strip()[-400:])
if sut.stderr.strip():
    print("  [stderr]", sut.stderr.strip()[-200:])

print("\n▶ [VISIO/손] (6) 독립 ground-truth 읽기 = Claude 클라우드 비전 (신뢰 오라클·gemma 천장 회피)")
gt = read.read_content("KakaoTalk", sensitive=False, force_vision=True,
                       instruction="이 카톡 대화 메시지를 옮겨라. 보이는 것만.")
gt_read = gt.get("text") or ""        # Claude가 정확히 읽은 ground-truth
print(f"  gt[Claude]({len(gt_read)}자): {gt_read[:180]}")

print("\n▶ [VISIO/손] Notes에서 그 노트 *내용 직접 읽어*(결정론·plaintext)")
r = subprocess.run(["osascript", "-e",
    'tell application "Notes"\n set m to (notes whose name is "📥 카톡 요약")\n'
    ' if (count of m) > 0 then\n  return plaintext of item 1 of m\n else\n  return ""\n end if\nend tell'],
    capture_output=True, text=True)
note_text = (r.stdout or "").strip()
print(f"  노트 내용({len(note_text)}자): {note_text[:160]}")

print("\n▶ [VISIO/판정] verify_faithfulness — *일반 재사용* 판정자(의미고도+OCR대칭검증+판정불가+품질)")
from rubi import verify_runner
FLOOR = 0.2
# 산출물(노트 plaintext) vs 독립 원본(Claude 읽기) — 결정론 OCR(gt['ocr'])로 대칭 교차검증.
verdict = verify_runner.verify_faithfulness(note_text, gt_read, gt.get("ocr") or "",
                                            floor=FLOOR, quality=True)
def _fmt(items, why=False):   # 재사용 함수의 구조화 결과 → 출력용 한 줄씩
    out = [f"{i.get('type')}:{(i.get('evidence') or '')[:22]}" + (f"({i.get('why')})" if why and i.get('why') else "")
           for i in items]
    return out or '없음'
verdict["major_issues"] = _fmt(verdict.get("major_issues") or [])
verdict["downgraded"] = _fmt(verdict.get("downgraded") or [], why=True)
if os.environ.get("VISIO_KEEP_NOTE"):
    print("  ※ VISIO_KEEP_NOTE=1 — 노트 '📥 카톡 요약' 보존(직접 확인용). 다음 런 시작 시 정리됨.")
else:
    cleanup_notes()

print("\n" + "=" * 60)
if verdict.get("abstain"):
    print(f"VISIO 판정 [정확성]: ⚠️ 판정불가 — {verdict.get('abstain')}")
else:
    print(f"VISIO 판정 [정확성]: achieved={verdict.get('achieved')}  "
          f"(grounded={verdict.get('grounded')} ≥{FLOOR}, veto={verdict.get('veto')}, judge_ok={verdict.get('judge_ok')})")
    print(f"  남은 major 결함(진짜): {verdict.get('major_issues') or '없음'}")
    print(f"  강등(환각만 OCR 교차검증): {verdict.get('downgraded') or '없음'}")
    print(f"  판정불가(대조 불가 — 결함 아님): {verdict.get('unverifiable') or '없음'}")
    print(f"VISIO 평가 [품질/사용성]: {verdict.get('usability')}  "
          f"(정확성과 별개·자문)  제안: {verdict.get('suggestions') or '없음'}")
    if verdict.get("quality_flags"):
        print(f"  ⚠️ 품질 결정론 교차검증 flag: {verdict.get('quality_flags')}")
print("=" * 60)
