#!/usr/bin/env python3
"""시각 디자인 품질 렌즈 *검증* — VLM이 웹페이지 디자인 품질을 제대로 변별하는지 두들긴다.
텍스트 품질 검증(`verify_quality_lens.py`)의 *시각* 버전.

검사 3종: ①변별력(못된 디자인 < 잘된 디자인) ②지어내기 가드(깔끔한데 문제 날조 안 함)
③일관성(같은 페이지 N회 동일).
파이프라인: 통제 HTML → Chrome 헤드리스 렌더(GUI 0) → VLM(클라우드 Claude 비전)이 스샷 보고 디자인 품질 판정.
"""
import os
import subprocess

from rubi import provider

OUT = "visio_out/visual_quality"
os.makedirs(OUT, exist_ok=True)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# ── 통제 픽스처: 같은 내용, 디자인 품질만 극명히 다르게 ──────────────────────────────
GOOD = """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;box-sizing:border-box;font-family:-apple-system,Helvetica,Arial,sans-serif}
body{background:#f6f7f9;color:#1a1d29;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#fff;max-width:560px;padding:56px 48px;border-radius:16px;box-shadow:0 8px 40px rgba(0,0,0,.06);text-align:center}
h1{font-size:34px;font-weight:700;line-height:1.25;margin-bottom:16px}
p{font-size:17px;color:#5b6172;line-height:1.6;margin-bottom:32px}
.btn{display:inline-block;background:#2f6df6;color:#fff;font-size:16px;font-weight:600;padding:14px 34px;border-radius:10px;text-decoration:none}
.muted{margin-top:18px;font-size:13px;color:#9aa0b0}
</style></head><body><div class="card">
<h1>한 번의 클릭으로 메모를 정리하세요</h1>
<p>흩어진 스크린샷과 메모를 자동으로 모아 깔끔한 노트로 만들어 드립니다.</p>
<a class="btn" href="#">무료로 시작하기</a>
<div class="muted">신용카드 불필요 · 30초면 설정 완료</div>
</div></body></html>"""

BAD = """<!doctype html><html><head><meta charset="utf-8"><style>
body{background:#12ff12;font-family:Times,serif;margin:0;padding:3px;line-height:1}
h1{color:#ff00ee;font-size:18px;display:inline}
p{color:#cfcfcf;font-size:11px;background:#ffff00;display:inline}
.b1{color:#00ffff;background:#ff0000;font-size:9px;border:3px dashed #0000ff;padding:1px;text-decoration:underline}
.b2{color:#fff;background:#333;font-size:30px;padding:0}
.tiny{color:#dcdcdc;font-size:8px}
</style></head><body>
<h1>한번의클릭으로!!!메모를정리하세요</h1><p>흩어진 스크린샷과 메모를 자동으로 모아 깔끔한 노트로 만들어 드립니다 지금 바로 가입하고 한정 할인 행사 더 많은 기능 사용 가능 서두르세요</p>
<a class="b1" href="#">무료로 시작하기</a><a class="b2" href="#">여기클릭</a><a href="#">또는여기</a><a class="b1" href="#">아니면이거</a>
<p class="tiny">약관 어쩌고 저쩌고 개인정보 어쩌고 끝없이 이어지는 작은 글씨 ...</p>
</body></html>"""

# 진짜 *완성된 좋은* 페이지(아이콘·명확 메시지·다음행동 버튼 다 있음) — don't-invent 테스트:
# 여기에 문제를 지어내거나 poor라 하면 = 과혹평(날조). 좋은 디자인은 good/fair여야.
SIMPLE = """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;box-sizing:border-box;font-family:-apple-system,Helvetica,Arial,sans-serif}
body{background:#f6f7f9;color:#1a1d29;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#fff;max-width:420px;padding:48px 40px;border-radius:16px;box-shadow:0 8px 40px rgba(0,0,0,.06);text-align:center}
.check{width:56px;height:56px;border-radius:50%;background:#e7f6ec;color:#1aa251;font-size:30px;line-height:56px;margin:0 auto 20px}
h1{font-size:24px;font-weight:700;margin-bottom:10px}
p{font-size:16px;color:#5b6172;line-height:1.6;margin-bottom:28px}
.btn{display:inline-block;background:#2f6df6;color:#fff;font-size:15px;font-weight:600;padding:12px 28px;border-radius:10px;text-decoration:none}
</style></head><body><div class="card">
<div class="check">✓</div>
<h1>저장되었습니다</h1>
<p>변경 사항이 모두 반영되었습니다.</p>
<a class="btn" href="#">대시보드로 돌아가기</a>
</div></body></html>"""


def render(name, html):
    hp = os.path.join(OUT, f"{name}.html")
    pp = os.path.join(OUT, f"{name}.png")
    with open(hp, "w") as f:
        f.write(html)
    url = "file://" + os.path.abspath(hp)
    for flag in ("--headless=new", "--headless"):
        try:
            subprocess.run([CHROME, flag, "--disable-gpu", "--hide-scrollbars",
                            f"--screenshot={pp}", "--window-size=1200,860", url],
                           capture_output=True, timeout=45)
        except Exception:
            pass
        if os.path.exists(pp) and os.path.getsize(pp) > 2000:
            return pp
    return None


def assess(png):
    """VLM이 *스샷을 보고* 디자인 사용성 판정(기능 아님). 구조화."""
    prompt = (
        f"웹페이지 스크린샷: `{os.path.abspath(png)}` — 읽어서(보고) 평가하라.\n\n"
        "이 페이지의 *디자인 사용성(품질)*만 평가하라(기능 동작 말고 — 사람이 보고 직관적·명확한가).\n"
        "고려: 시각적 위계(중요한 게 눈에 띄나)·여백/정렬·대비/가독성·색 조화·주요 행동(CTA)이 분명한가·전반적 깔끔함.\n"
        "문제가 없으면 *지어내지 마라*(좋으면 usability=good, issues=[]).\n"
        'ONLY JSON: {"usability":"good|fair|poor","issues":["<디자인 문제 한 줄>"]}')
    data = provider.complete_json(prompt, "claude-sonnet-4-6", image_path=png) or {}
    return (data.get("usability", "?"), [str(s)[:70] for s in (data.get("issues") or [])])


RANK = {"good": 2, "fair": 1, "poor": 0, "?": -1}
N = 2


def run(name, html):
    pp = render(name, html)
    if not pp:
        print(f"[{name}] ❌ 렌더 실패")
        return None
    res = [assess(pp) for _ in range(N)]
    us = [r[0] for r in res]
    print(f"[{name:6}] usability={us}  issues={len(res[0][1])}개  예:{(res[0][1][:1] or ['-'])[0]}")
    return res


print("=" * 64)
print("시각 디자인 품질 렌즈 검증 — 변별력 / 지어내기 가드 / 일관성")
print("=" * 64)
g = run("good", GOOD)
b = run("bad", BAD)
s = run("simple", SIMPLE)

if g and b and s:
    print("\n" + "-" * 64)
    disc = RANK[b[0][0]] < RANK[g[0][0]]
    print(f"① 변별력 (BAD < GOOD):            {'✅' if disc else '❌'}  (GOOD={g[0][0]} > BAD={b[0][0]})")
    # 날조 = *완성된 좋은* 페이지를 poor라 하거나 과혹평. 미세 제안 몇 개는 정상(시각=주관).
    invent = RANK[s[0][0]] < 1
    print(f"② 지어내기 가드 (좋은 페이지 → not poor): {'❌ 날조(poor)' if invent else '✅'}  (SIMPLE={s[0][0]}, 제안 {len(s[0][1])}개)")
    cons = all(len({r[0] == 'poor' for r in grp}) == 1 for grp in (g, b, s))
    strict = all(len({r[0] for r in grp}) == 1 for grp in (g, b, s))
    print(f"③ 일관성 strict(라벨):             {'✅' if strict else '⚠️ 미세 흔들림'}")
    print(f"③ 일관성 decision(poor-vs-not):   {'✅' if cons else '❌'}  ← 행동 갈리는 경계")
    passed = sum([disc, not invent, cons])
    print("-" * 64)
    print(f"시각 품질 렌즈 검증: {passed}/3 통과 (+ strict {'OK' if strict else '흔들림'})")
    print("=" * 64)
