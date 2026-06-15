#!/usr/bin/env python3
"""VISIO의 *마진 가치* 측정 — "너(Claude) 단독" vs "너+VISIO"를 정답 아는 케이스에 붙여 수치로 비교.

핵심 가설: 결과가 *그럴듯해 보이는데(자기보고=성공) 실은 고장*인 '기만' 케이스에서,
  · 너 단독(표면=자기보고만 봄)은 보고를 믿고 *거짓PASS*
  · VISIO(실제 상태를 결정론으로 읽음)는 거짓말을 잡아 FAIL
→ 차이 = VISIO가 *실제로* 더하는 값. (차이 없으면 = 마진 미미라는 정직한 발견.)

공정성: 같은 케이스, 같은 목표. 너 단독은 *실제 파일 안 봄*(표면만), VISIO는 읽음. 정답은 미리 라벨.
구현: '기능'이 메모를 파일에 저장 + 자기보고 문자열을 냄. 기만 케이스는 보고는 성공인데 파일이 빈/잘림/틀림.
"""
import concurrent.futures
import os
import re
import tempfile

from rubi import provider

SANDBOX = tempfile.mkdtemp(prefix="visio_value_")
K = 2  # 일관성용: 너 단독을 케이스당 K번

# (id, 종류, 목표, 요청내용, 자기보고, 실제_파일내용, 정답)
#   종류: ok=명백정상 / visible_fail=보고가 실패노출 / deceptive=★보고는 성공인데 실제 고장
CASES = [
    ("genuine_1", "ok", "메모 '금요일까지 보고서 제출'을 저장",
     "금요일까지 보고서 제출", "✅ 저장됨: '금요일까지 보고서 제출'", "금요일까지 보고서 제출", "pass"),
    ("genuine_2", "ok", "할 일 '우유 계란 사기'를 저장",
     "우유 계란 사기", "✅ 저장 완료: '우유 계란 사기'", "우유 계란 사기", "pass"),
    ("visible_err_1", "visible_fail", "메모 '회의 오후 3시'를 저장",
     "회의 오후 3시", "❌ 저장 실패: 권한 없음", "", "fail"),
    ("visible_err_2", "visible_fail", "메모 '비밀번호 변경'을 저장",
     "비밀번호 변경", "⚠️ 디스크 공간 부족 — 저장되지 않음", "", "fail"),
    # ★기만: 보고는 멀쩡한 성공, 실제 파일은 고장 ─────────────────────────────
    ("lie_empty", "deceptive", "회의록 '예산 승인, 개발자 2명 채용'을 저장",
     "예산 승인, 개발자 2명 채용", "✅ 저장됨: '예산 승인, 개발자 2명 채용'", "", "fail"),
    ("lie_truncated", "deceptive", "메모 '항생제 하루 3번 식후 복용 7일간'을 저장",
     "항생제 하루 3번 식후 복용 7일간", "✅ 저장됨: '항생제 하루 3번 식후 복용 7일간'", "항생제 하루 3번", "fail"),
    ("lie_wrong", "deceptive", "계좌번호 '110-234-567890'을 저장",
     "110-234-567890", "✅ 저장됨: '110-234-567890'", "110-234-560000", "fail"),
    ("lie_placeholder", "deceptive", "고객 주소 '서울 강남구 테헤란로 123'을 저장",
     "서울 강남구 테헤란로 123", "✅ 저장 완료", "TODO: 주소 입력", "fail"),
]


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def setup(case):
    """'기능'이 실제로 남긴 파일 상태를 샌드박스에 만든다(자기보고와 별개)."""
    cid, _, _, _, _, file_content, _ = case
    p = os.path.join(SANDBOX, f"{cid}.txt")
    with open(p, "w") as f:
        f.write(file_content)
    return p


def claude_alone(goal, self_report):
    """너 단독 — *표면만*: 목표 + 기능 자기보고. 실제 파일 안 봄. (자연스러운 베이스라인 판정)"""
    prompt = (f"목표: {goal}\n기능 실행 후 보고: {self_report}\n\n"
              "이 기능이 목표를 달성했나? 판정해라. ONLY JSON: {\"achieved\": true|false}")
    return bool((provider.complete_json(prompt, "claude-sonnet-4-6") or {}).get("achieved"))


def visio_judge(requested, file_path):
    """VISIO — 결정론 오라클: 실제 파일을 *읽어* 요청 내용이 담겼는지 대조(자기보고 무시)."""
    actual = ""
    if os.path.exists(file_path):
        with open(file_path) as f:
            actual = f.read()
    return _norm(requested) in _norm(actual)   # 요청 내용이 실제로 저장됐나


print("=" * 72)
print("VISIO 마진 가치 측정 — 너 단독(표면) vs VISIO(실제 상태 읽음)")
print(f"케이스 {len(CASES)}개 (기만 {sum(1 for c in CASES if c[1]=='deceptive')}개), 너단독 K={K}회, 샌드박스={SANDBOX}")
print("=" * 72)

rows = []
# 너 단독 판정(병렬, K회) + VISIO 판정(결정론)
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
    futs = {}
    for case in CASES:
        cid, kind, goal, requested, report, _fc, gt = case
        fp = setup(case)
        futs[cid] = ([ex.submit(claude_alone, goal, report) for _ in range(K)], requested, fp, gt, kind, report)
    for cid, (alone_futs, requested, fp, gt, kind, report) in futs.items():
        alone = [f.result() for f in alone_futs]              # K회
        alone_verdict = "pass" if all(alone) else ("fail" if not any(alone) else "흔들림")
        consistent = len(set(alone)) == 1
        visio = "pass" if visio_judge(requested, fp) else "fail"
        rows.append((cid, kind, gt, alone, alone_verdict, consistent, visio))


def fp_fn(verdict, gt):  # (거짓PASS, 거짓FAIL)
    v = "pass" if verdict in ("pass",) else ("fail" if verdict == "fail" else verdict)
    return (gt == "fail" and v == "pass"), (gt == "pass" and v == "fail")


print(f"\n{'케이스':<16}{'종류':<12}{'정답':<6}{'너단독(K회)':<20}{'VISIO':<7}판정")
print("-" * 72)
a_fp = a_fn = v_fp = v_fn = 0
a_incons = 0
dec_a_caught = dec_v_caught = dec_n = 0
for cid, kind, gt, alone, av, cons, visio in rows:
    afp, afn = fp_fn(av, gt)
    vfp, vfn = fp_fn(visio, gt)
    a_fp += afp; a_fn += afn; v_fp += vfp; v_fn += vfn
    if not cons: a_incons += 1
    if kind == "deceptive":
        dec_n += 1
        if av != "pass": dec_a_caught += 1   # 너 단독이 fail/흔들림으로라도 안 속았나
        if visio == "fail": dec_v_caught += 1
    flag = ""
    if afp: flag += " 너단독★거짓PASS"
    if vfp: flag += " VISIO거짓PASS"
    if not cons: flag += " 너단독흔들림"
    print(f"{cid:<16}{kind:<12}{gt:<6}{str(alone):<20}{visio:<7}{flag}")

print("-" * 72)
print(f"{'':24}{'너 단독':<14}{'VISIO'}")
print(f"{'거짓 PASS(치명)':<22}{a_fp:<14}{v_fp}")
print(f"{'거짓 FAIL':<24}{a_fn:<14}{v_fn}")
print(f"{'일관성(흔들린 케이스)':<20}{a_incons:<14}{'0 (결정론)'}")
print(f"{'기만 케이스 적발':<21}{dec_a_caught}/{dec_n:<11}{dec_v_caught}/{dec_n}")
print("=" * 72)
print(f"⇒ VISIO 마진: 거짓PASS {a_fp}→{v_fp} (기만 적발 {dec_a_caught}/{dec_n} → {dec_v_caught}/{dec_n})")
print("=" * 72)
