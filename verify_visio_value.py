#!/usr/bin/env python3
"""VISIO의 *마진 가치* 측정 — "너(Claude) 단독" vs "너+VISIO"를 정답 아는 케이스에 붙여 수치로 비교.

핵심 가설: 결과가 *그럴듯해 보이는데(자기보고=성공) 실은 고장*인 '기만' 케이스에서,
  · 너 단독(표면=자기보고만 봄)은 보고를 믿고 *거짓PASS*
  · VISIO(실제 상태를 결정론으로 읽음)는 거짓말을 잡아 FAIL
→ 차이 = VISIO가 *실제로* 더하는 값. (차이 없으면 = 마진 미미라는 정직한 발견.)

★확장(세션18): 기만을 *파일* 하나가 아니라 **세 가지 관찰 기질**(파일·클립보드·앱설정defaults)에
  걸쳐 만든다 → "파일만 읽는 트릭 아니냐"는 반박을 막고, 가치가 *관찰종류와 무관하게 일반화*함을 보임.
공정성: 같은 케이스·같은 목표. 너 단독은 *실제 상태 안 봄*(표면만), VISIO는 해당 기질을 직접 읽음. 정답 미리 라벨.
"""
import concurrent.futures
import os
import re
import subprocess
import tempfile

from rubi import provider

SANDBOX = tempfile.mkdtemp(prefix="visio_value_")
DOMAIN = "com.visio.benchtest"   # 격리된 defaults 도메인(사용자 설정 미접촉)
K = 2  # 일관성용: 너 단독을 케이스당 K번

# (id, 기질, 종류, 목표, 요청내용, 자기보고, 실제_상태, 정답)
#   기질: file | clipboard | defaults     종류: ok / visible_fail / deceptive(★보고는성공·실제고장)
CASES = [
    # ── 파일 기질 ──────────────────────────────────────────────────────────
    ("file_ok",        "file", "ok",          "메모 '금요일까지 보고서 제출'을 저장",
     "금요일까지 보고서 제출", "✅ 저장됨: '금요일까지 보고서 제출'", "금요일까지 보고서 제출", "pass"),
    ("file_visfail",   "file", "visible_fail", "메모 '회의 오후 3시'를 저장",
     "회의 오후 3시", "❌ 저장 실패: 권한 없음", "", "fail"),
    ("file_lie_empty", "file", "deceptive",   "회의록 '예산 승인, 개발자 2명 채용'을 저장",
     "예산 승인, 개발자 2명 채용", "✅ 저장됨: '예산 승인, 개발자 2명 채용'", "", "fail"),
    ("file_lie_trunc", "file", "deceptive",   "메모 '항생제 하루 3번 식후 복용 7일간'을 저장",
     "항생제 하루 3번 식후 복용 7일간", "✅ 저장됨: '항생제 하루 3번 식후 복용 7일간'", "항생제 하루 3번", "fail"),
    ("file_lie_wrong", "file", "deceptive",   "계좌번호 '110-234-567890'을 저장",
     "110-234-567890", "✅ 저장됨: '110-234-567890'", "110-234-560000", "fail"),
    ("file_lie_todo",  "file", "deceptive",   "고객 주소 '서울 강남구 테헤란로 123'을 저장",
     "서울 강남구 테헤란로 123", "✅ 저장됨: '서울 강남구 테헤란로 123'", "TODO: 주소 입력", "fail"),

    # ── 클립보드 기질 ──────────────────────────────────────────────────────
    ("clip_ok",        "clipboard", "ok",         "'배포 키: DEPLOY-2026'을 클립보드에 복사",
     "배포 키: DEPLOY-2026", "✅ 클립보드에 복사됨: '배포 키: DEPLOY-2026'", "배포 키: DEPLOY-2026", "pass"),
    ("clip_lie_empty", "clipboard", "deceptive",  "'서버 토큰 abc123xyz'를 클립보드에 복사",
     "서버 토큰 abc123xyz", "✅ 클립보드에 복사됨: '서버 토큰 abc123xyz'", "", "fail"),
    ("clip_lie_stale", "clipboard", "deceptive",  "'2026 신주소 부산 해운대'를 클립보드에 복사",
     "2026 신주소 부산 해운대", "✅ 클립보드에 복사됨: '2026 신주소 부산 해운대'", "옛주소 대구 수성구", "fail"),

    # ── 앱설정(defaults) 기질 ──────────────────────────────────────────────
    ("def_ok",         "defaults", "ok",         "환경설정 theme 를 'dark' 로 변경",
     "dark", "✅ theme=dark 적용됨", "dark", "pass"),
    ("def_lie_unset",  "defaults", "deceptive",  "환경설정 theme 를 'dark' 로 변경",
     "dark", "✅ theme=dark 적용됨", "", "fail"),                      # 실제론 미적용(미설정)
    ("def_lie_wrong",  "defaults", "deceptive",  "자동저장 간격 interval 을 '30' 으로 설정",
     "30", "✅ interval=30 적용됨", "10", "fail"),                            # 실제론 10 그대로
]


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


# ── 기질별 셋업(실제 상태 주입) / 읽기(결정론) ─────────────────────────────
def setup(case):
    cid, sub, _, _, _, _, actual, _ = case
    if sub == "file":
        p = os.path.join(SANDBOX, f"{cid}.txt")
        open(p, "w").write(actual)
    elif sub == "clipboard":
        subprocess.run(["pbcopy"], input=actual, text=True, timeout=8)
    elif sub == "defaults":
        subprocess.run(["defaults", "delete", DOMAIN, cid], capture_output=True, timeout=8)
        if actual != "":
            subprocess.run(["defaults", "write", DOMAIN, cid, actual], capture_output=True, timeout=8)


def visio_read(case):
    """VISIO — 해당 기질을 *직접* 읽는다(자기보고 무시). 실제 상태 문자열 반환."""
    cid, sub, *_ = case
    if sub == "file":
        p = os.path.join(SANDBOX, f"{cid}.txt")
        return open(p).read() if os.path.exists(p) else ""
    if sub == "clipboard":
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=8).stdout
    if sub == "defaults":
        r = subprocess.run(["defaults", "read", DOMAIN, cid], capture_output=True, text=True, timeout=8)
        return r.stdout if r.returncode == 0 else ""
    return ""


def visio_judge(case):
    requested = case[4]
    return "pass" if _norm(requested) in _norm(visio_read(case)) else "fail"


def claude_alone(goal, self_report):
    """너 단독 — *표면만*: 목표 + 기능 자기보고. 실제 상태 안 봄. (자연스러운 베이스라인 판정)"""
    prompt = (f"목표: {goal}\n기능 실행 후 보고: {self_report}\n\n"
              "이 기능이 목표를 달성했나? 판정해라. ONLY JSON: {\"achieved\": true|false}")
    return bool((provider.complete_json(prompt, "claude-sonnet-4-6") or {}).get("achieved"))


def fp_fn(verdict, gt):  # (거짓PASS, 거짓FAIL)
    v = "pass" if verdict == "pass" else ("fail" if verdict == "fail" else verdict)
    return (gt == "fail" and v == "pass"), (gt == "pass" and v == "fail")


def main():
    n_dec = sum(1 for c in CASES if c[2] == "deceptive")
    subs = sorted(set(c[1] for c in CASES))
    print("=" * 76)
    print("VISIO 마진 가치 측정 — 너 단독(표면) vs VISIO(실제 상태 읽음)")
    print(f"케이스 {len(CASES)}개 (기만 {n_dec}개), 기질={subs}, 너단독 K={K}회")
    print("=" * 76)

    clip_backup = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout  # 사용자 클립보드 보존
    rows = []
    try:
        # 너 단독(LLM, 병렬, K회) — 표면만 보니 기질 무관, 미리 다 띄움
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            alone_futs = {c[0]: [ex.submit(claude_alone, c[3], c[5]) for _ in range(K)] for c in CASES}
            # VISIO(결정론) — 기질 공유자원(클립보드 1개) 경합 방지 위해 순차 setup→read
            for case in CASES:
                cid = case[0]
                setup(case)
                visio = visio_judge(case)
                alone = [f.result() for f in alone_futs[cid]]
                av = "pass" if all(alone) else ("fail" if not any(alone) else "흔들림")
                rows.append((cid, case[1], case[2], case[7], alone, av, len(set(alone)) == 1, visio))
    finally:
        subprocess.run(["pbcopy"], input=clip_backup, text=True)                       # 클립보드 복원
        subprocess.run(["defaults", "delete", DOMAIN], capture_output=True)            # 테스트 도메인 삭제

    print(f"\n{'케이스':<16}{'기질':<11}{'종류':<12}{'정답':<6}{'너단독(K회)':<18}{'VISIO':<7}판정")
    print("-" * 76)
    a_fp = a_fn = v_fp = v_fn = a_incons = 0
    dec_a = dec_v = dec_n = 0
    for cid, sub, kind, gt, alone, av, cons, visio in rows:
        afp, afn = fp_fn(av, gt); vfp, vfn = fp_fn(visio, gt)
        a_fp += afp; a_fn += afn; v_fp += vfp; v_fn += vfn
        if not cons: a_incons += 1
        if kind == "deceptive":
            dec_n += 1
            if av != "pass": dec_a += 1
            if visio == "fail": dec_v += 1
        flag = ""
        if afp: flag += " 너단독★거짓PASS"
        if vfp: flag += " VISIO거짓PASS"
        if not cons: flag += " 너단독흔들림"
        print(f"{cid:<16}{sub:<11}{kind:<12}{gt:<6}{str(alone):<18}{visio:<7}{flag}")

    print("-" * 76)
    print(f"{'':27}{'너 단독':<14}{'VISIO'}")
    print(f"{'거짓 PASS(치명)':<23}{a_fp:<14}{v_fp}")
    print(f"{'거짓 FAIL':<25}{a_fn:<14}{v_fn}")
    print(f"{'일관성(흔들린 케이스)':<21}{a_incons:<14}{'0 (결정론)'}")
    print(f"{'기만 적발(파일+클립+설정)':<19}{dec_a}/{dec_n:<11}{dec_v}/{dec_n}")
    print("=" * 76)
    print(f"⇒ VISIO 마진: 거짓PASS {a_fp}→{v_fp}  (3개 기질 기만 적발 {dec_a}/{dec_n} → {dec_v}/{dec_n})")
    print("=" * 76)


if __name__ == "__main__":
    main()
