# VISIO — independent verification for agent-built Mac features

> 코딩 에이전트는 macOS 자동화 기능을 만들 수 있다. VISIO는 그게 **실제 환경에서
> 정말 동작하는지**를 검증한다 — 실제 앱을 직접 구동하고, *진짜 결과 상태*를 읽고,
> 에이전트 자신의 "✅ 완료" 보고를 믿지 않으면서.

**언어: [한국어](#한국어) · [English](#english)**

---

<a name="한국어"></a>
## 한국어

에이전트가 기능을 *만들면서 동시에 테스트하면*, 그 테스트는 *확인용(confirmatory)*이 된다 —
같은 머리가 스펙을 정의하고 검증하니 구조적으로 통과한다. 그래서 살아남는 실패는 **조용한
실패**다: 보고는 "성공"인데 실제 산출물은 비어 있거나, 잘렸거나, 틀렸다. VISIO는 오직 그걸
잡는 것이 일인 별도의 **테스터**다.

### 측정한 결과 (이 프로젝트의 핵심)

절제 실험(`verify_visio_value.py`)이 **Claude 단독**(표면/자기보고로 판정) vs **Claude +
VISIO**(실제 상태를 읽고 판정)를 12개 케이스(파일·클립보드·앱설정 3기질)에 붙인다. 그중 8개는 *기만* 케이스 —
자기보고는 "성공"인데 실제 결과는 비었거나/잘렸거나/틀렸거나/플레이스홀더다.

| 지표 | Claude 단독 | + VISIO |
|---|---:|---:|
| **거짓 PASS (치명)** | **8** | **0** |
| 거짓 FAIL | 0 | 0 |
| 기만 케이스 적발 | **0 / 8** | **8 / 8** |
| 일관성 (흔들린 판정) | 1 | 0 (결정론) |

VISIO의 가치는 **"Claude보다 똑똑함"이 아니다.** 뻔한 케이스에선 둘이 똑같이 판정한다.
가치는 **규율 × 자동화**다 — 자기보고를 믿지 않고, 실제 상태를 읽고, 그걸 *사람 없이* 한다.
매일 밤, 커밋마다, 새벽 3시에.

판정 품질 자체를 재는 벤치도 둘 있다: `verify_quality_lens.py`(텍스트 품질 변별, 4/4),
`verify_visual_quality.py`(비전 모델이 렌더된 웹사이트 디자인을 판정, 3/3). 그리고 한 발 더 나아가
**VISIO 자신을 적대적으로 감사**하는 `verify_visio_adversarial.py`: VISIO의 판정자를 속이려는 공격
(프롬프트 인젝션·악의적 압축누락·숫자왜곡·부정뒤집기·환각)에 거짓PASS 0 — 결정론 오라클은 "검증통과 ✅"
거짓말을 박아넣어도 실제 바이트를 읽어 안 속는다.

그리고 위 셋이 *상태를 프로그램으로 깔아놓고* 판정자만 시험한 것과 달리, `verify_visio_live.py`는
**전체 라이브 파이프라인**을 잰다: VISIO가 진짜 알림을 쏘고 → 빌드된 SUT가 *실제 Apple Notes 앱*에
기록 → VISIO가 *그 실제 노트를 읽어*(authoritative 오라클) 판정. 정상 SUT=PASS, *본문 누락+거짓보고*
버그 SUT=FAIL(거짓PASS 0). 이 라이브 측정은 프로그램 벤치가 못 겪는 실환경 함정도 드러냈다 —
`notes_contains`가 휴지통('Recently Deleted') 노트까지 읽어 *삭제된 노트가 거짓PASS를 유발*하던
버그를 발견해 프로브를 하드닝했다(라이브로 우리 검사기를 더 단단히 만든 셈).

### 자율 학습 루프 — 스스로 실패에서 배워 다음 테스트를 개선 (측정)

VISIO는 테스트 실행에서 *스스로 교훈을 쌓고*(EMERI), 다음 테스트 *설계*에 그걸 반영한다. 이 루프를
우리 자신에게 적용해 측정하다 버그를 둘 잡았다 — ① real run이 교훈을 *안 쌓던* 열린 루프(write 미배선),
② 회상이 "이미 학습함, 중복 만들지 마라"로 주입돼 *약점을 건너뛰게* 하던 프레이밍 오류(약점은 정반대로
*더* 봐야 함). 둘 다 고쳤다. **end-to-end 증명(사람 개입 0):** 본문의 `<<RESET>>` 토큰 이후를 조용히
잘라먹는 버그 기능을 돌리면 → VISIO가 실패를 잡고 *자동으로 교훈 기록* → 다음 설계가 그 교훈을 회상해
`<<RESET>>` 케이스를 *스스로* 만든다. 기능 설명만으론 절대 못 떠올리는 임의 토큰을, 경험에서 배워 다음
설계가 타깃한다 — **콜드(기억 없음) 0/2 → 자동 기억 2/2.** (`verify_emeri*.py`)

### 어떻게 동작하나

VISIO는 협업하는 세 역할이다:

| 역할 | 이름 | 하는 일 |
|---|---|---|
| **두뇌** | RUBI | 엣지케이스 시나리오 계획, 결과 판정, 닫힌 루프 |
| **손** | SAPPHI | 관찰(Accessibility 우선 → OCR → 비전)하고 실제 앱을 조작 |
| **기억** | EMERI | 검증된 합/불 교훈 저장, 작업 유형별 회상으로 회귀 감지 |

테스트 한 번 = **자극 주입 → 기능 실행 → 진짜 결과 읽기 → 판정 → 리포트 → 직전과 비교.**

#### 핵심 판정 원칙: *읽을 수 있으면 직접 읽어라*

순진한 설계는 모든 걸 화면 스크린샷 찍어 비전 모델에 묻는다. 그건 **순수하게 시각적인
결과**(이 웹사이트가 보기에 잘 디자인됐나?)에만 옳은 판정이다. 그 외에는 VISIO가 실제
상태를 직접 읽고 결정론적 **오라클**이 판정한다:

- 저장된 노트 → AppleScript로 평문 되읽기
- 파일/클립보드 결과 → 바이트를 읽어 해시
- 앱 상태 → Accessibility / `defaults` / 앱 자체 스크립팅으로 질의

이게 거짓 PASS ≈ 0을 만드는 이유다: 판정이 *스크린샷에 대한 모델의 인상*이 아니라 실제
산출물에 근거한다.

#### *거짓* PASS를 막는 세 장치

1. **신뢰 바닥(오라클 거부권).** 결정론적 프로브가 실제 결과를 읽고 판정을 거부할 수 있다.
   OCR 교차검증은 일부러 **비대칭**이다: "환각" 주장은 OCR이 내용이 실재함을 확인하면 강등할
   수 있지만, "누락" 주장은 OCR-부재로 절대 통과시키지 않는다 (OCR은 비전 모델보다 약해서
   놓치는 게 있고 — 진짜 누락을 통과시키면 그게 거짓 PASS다).
2. **의미 고도(altitude).** 판정자는 의미와 결과를 채점하지, 글자 단위 전사를 보지 않는다.
   사소한 표기 차이나 조밀·무의미 문자열은 결함이 아니고, 증거로 신뢰성 있게 확인 못 하는
   축은 추측 대신 `uncertain`으로 둔다 (거짓 PASS도 FAIL도 안 만든다).
3. **무결성: 판정자 ≠ 빌더.** 프로브와 자극은 *테스터 자신의* 도구다. 기능 개발자는 VISIO가
   자극을 스스로 못 만들 때 *셋업 메커니즘(픽스처)*을 제공할 수 있지만, **시나리오 입력과 판정은
   테스터에게 남는다.** 판정자를 빌더에게 넘기면 무결성이 끝난다.

#### 무엇을 테스트할 수 있나 — 역량 경계

관문은 **관찰성 = 맥이 닿을 수 있는 흔적**이다 — 패킷·파일·로그·타이밍·UI — "화면에 보이나"가
아니다. 화면에 안 보이는 것도 대개 맥에 흔적을 남겨서, 맞는 프로브만 있으면 테스트된다
(네트워크 → 로컬 mock 서버; 캐시 → 타이밍 프록시; 앱 상태 → AppleScript). **"아직 프로브 없음"은
툴링 갭이지 "불가능"이 아니다.** 진짜 한계는 좁다: 원격 서버에만 있고 로컬 흔적이 전혀 없는 상태;
안전벽(송금 같은 비가역 행동은 커밋 직전까지만 실행, 실제 실행은 안 함); 주관적 취향.

### 실행

Python 3 + 로그인된 `claude` CLI 필요 (API 키 불필요; `SAPPHI_PROVIDER=openai` +
`OPENAI_API_KEY`로 GPT 사용 가능).

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 1) 구조 셀프체크 — 결정론, LLM 0회. 19 PASS / 0 FAIL 나와야 함.
./.venv/bin/python -m rubi selfcheck

# 2) 가치 절제 실험 — Claude 단독 vs Claude+VISIO, 기만 케이스에서.
./.venv/bin/python verify_visio_value.py

# 3) 판정 품질 벤치.
./.venv/bin/python verify_quality_lens.py
./.venv/bin/python verify_visual_quality.py

# 4) 기능 라이브 풀 테스트 (실제 앱 구동 — 활성 GUI 세션 필요).
./.venv/bin/python -m rubi visio test "<기능 설명>" --mode live
```

`rubi visio` 하위 명령: `plan`(실행 없이 엣지케이스 계획 생성), `test`(계획→실행→판정→리포트),
`rerun`(저장된 계획 재실행 + 회귀 비교), `fixtures`(VISIO가 개발자에게 요청할 픽스처 덤프),
`loop`(빌드→리뷰→수정→재검증 닫힌 루프).

### 저장소 구조

```
rubi/                 # 테스트 두뇌(BRAIN)
  visio.py            #   계획→실행→판정 오케스트레이션 (+authoritative 오라클)
  verify_runner.py    #   정확성+품질 판정 1콜 병렬 / OCR 교차검증
  taskspec.py         #   목표 → 구조화된 작업 계약 + 렌즈 계약
  oracles.py          #   결정론 결과 오라클
  emeri.py            #   검증된 교훈 기억 + 회귀 회상
  records.py          #   검증 감사 로그
  selfcheck.py        #   19항목 구조 무결성 (LLM 0회)
  provider.py         #   LLM 백엔드: claude CLI(로그인) / OpenAI / local
sapphi/               # 손(HANDS — macOS 관찰 + 실행)
  agent.py act.py     #   smart_click 캐스케이드: Accessibility → OCR → 비주얼 그라운딩
  perceive.py ocr.py  #   화면 지각, OCR
  triggers.py         #   결정론 자극 주입기 (file/notification/clipboard/...)
  probes.py           #   결정론 결과 리더 (11개 프로브)
  netmock.py perf.py  #   localhost mock 서버(네트워크 프로브) + 타이밍 측정
sut/                  # 테스트 대상 샘플 기능 ("개발자" 쪽)
verify_visio_value.py # 절제 벤치 — 위 측정 결과 (파일·클립보드·설정)
verify_visio_adversarial.py  # 적대적 자기감사(레드팀) — VISIO 판정자를 속이려는 공격
verify_visio_live.py  # 라이브 앱 경로 — 진짜 Notes 앱 구동→실제 노트 읽어 회귀 적발
verify_emeri.py       # 기억(EMERI) 회상 품질 측정
verify_emeri_value.py # 기억이 설계를 개선하나 (콜드 vs 기억)
verify_emeri_e2e.py   # 자율 학습루프 end-to-end (실패→자동기록→자동회상→잡음)
verify_*.py           # 판정 품질 벤치
run_*.py              # 독립 데모 (notify→Notes, 신뢰 바닥, 무인/launchd, ...)
```

### 정직한 한계

- 판정자는 결과 유형별 **프로브**만큼만 강하다. 프로브가 아직 안 쓰여진 결과 유형은 추가해야
  한다 (프로브는 관찰 종류별이라 재사용되지만, 롱테일은 존재한다).
- **무인(launchd) 실행**은 파일/헤드리스 작업엔 깨끗하지만, 클립보드/GUI 작업은 세션 종속이라
  LaunchAgent에서 행(hang)이 걸린다 — GUI 없는 기능이거나 활성 Aqua 세션이 필요하다.
- **읽기 천장**: 극도로 조밀하거나 무의미한 내용은 사람보다 덜 신뢰성 있게 읽힌다 (그런 축은
  추측 대신 `uncertain`으로 둔다).
- 라이브 풀 테스트는 여전히 활성 macOS GUI 세션이 필요하다 — 순수 헤드리스 CI 도구가 아니다.

### 범위 & 상태

이건 **연구 / 포트폴리오** 프로젝트이지 제품이 아니다. 한 질문 — 독립적인 에이전트가 다른
에이전트의 실환경 행동을 신뢰성 있게 검증할 수 있는가? — 을 탐구하고 그 답을 측정하기 위해
존재한다 (위의 거짓 PASS 8→0 결과). 코드 주석은 주로 한국어다.

---

<a name="english"></a>
## English

When an agent both writes a feature and tests it, the test is *confirmatory*:
it passes by construction (the same mind defines the spec and checks it). The
failure mode that survives is the **silent one** — the agent reports success,
but the real artifact is empty, truncated, or wrong. VISIO is a separate
*tester* whose only job is to catch that.

### The measured result (the point of the project)

An ablation benchmark (`verify_visio_value.py`) pits **Claude alone** (judging
from the surface / self-report) against **Claude + VISIO** (reading the actual
real state) on 12 cases across files, clipboard, and settings — 8 of them *deceptive* — the self-report says
"success" but the real output is empty / truncated / wrong / a placeholder.

| metric | Claude alone | + VISIO |
|---|---:|---:|
| **false-PASS (critical)** | **8** | **0** |
| false-FAIL | 0 | 0 |
| deceptive cases caught | **0 / 8** | **8 / 8** |
| consistency (flaky verdicts) | 1 | 0 (deterministic) |

VISIO's value is **not** "smarter than Claude." On obvious cases the two agree.
Its value is **discipline × automation**: it does not believe a self-report, it
reads the real state, and it does this with no human in the loop — every night,
every commit, at 3am.

Two more benches quantify the judging quality directly:
`verify_quality_lens.py` (text quality discrimination, 4/4) and
`verify_visual_quality.py` (a vision model judging rendered website design,
3/3). A third bench goes further and *adversarially audits VISIO itself*
(`verify_visio_adversarial.py`): attempts to fool VISIO's own judge —
prompt-injection, malicious omission, subtle distortion, negation,
hallucination — yield **0 false-PASS**, and the deterministic oracle is
unfoolable (it reads the real bytes even when the output embeds a fake
"verified ✅" claim).

Unlike the three above (which set up state *programmatically* and test only the
judge), `verify_visio_live.py` measures the **full live pipeline**: VISIO posts a
real notification → the built SUT writes to the *real Apple Notes app* → VISIO
reads *that actual note back* (authoritative oracle) to judge. Correct SUT = PASS,
a *body-dropping + "saved ✅"-lying* bug SUT = FAIL (0 false-PASS). This live run
also surfaced a real-world pitfall the programmatic benches never hit —
`notes_contains` was reading notes in *Recently Deleted*, so a deleted note's
content could cause a false-PASS; the probe was hardened to exclude the trash
(live testing made our own checker stronger).

### Autonomous learning loop — learns from its own failures to sharpen the next test (measured)

VISIO accumulates lessons from its runs (EMERI) and feeds them into the *design* of the next test.
Applying this loop to VISIO itself surfaced two bugs — (1) real runs didn't *store* lessons (the
write side was unwired), and (2) recall was injected as "already learned, don't duplicate," which
made the designer *skip* known weak spots (the opposite of what you want — weak spots should be
tested *more*). Both fixed. **End-to-end proof (zero human in the loop):** run a buggy feature that
silently drops everything after a `<<RESET>>` token → VISIO catches the failure and *automatically
records the lesson* → the next design recalls it and *generates the `<<RESET>>` case on its own*. An
arbitrary token no designer could guess from the spec, learned from experience and targeted by the
next design: **cold (no memory) 0/2 → auto-memory 2/2.** (`verify_emeri*.py`)

### How it works

VISIO is three cooperating roles:

| role | name | job |
|---|---|---|
| **brain** | RUBI | plans edge-case scenarios, judges outcomes, runs the closed loop |
| **hands** | SAPPHI | observes (Accessibility-first → OCR → vision) and drives the live apps |
| **memory** | EMERI | stores verified pass/fail lessons, recalls them per task type for regression |

A test run is: **inject a stimulus → run the feature → read the real outcome →
judge → report → compare against last time.**

#### The core judging principle: *read it directly when you can*

The naive design judges everything by screenshotting the screen and asking a
vision model. That is the right judge **only for purely visual results** (does
this website *look* well-designed?). For everything else VISIO reads the actual
state directly and lets a deterministic **oracle** decide:

- a saved note → read the plaintext back via AppleScript
- a file/clipboard result → read the bytes and hash them
- an app state → query Accessibility / `defaults` / the app's own scripting

This is what makes false-PASS ≈ 0: the verdict is grounded in the artifact, not
in a model's impression of a screenshot.

#### Three safeguards against a *false* PASS

1. **Trust floor (oracle veto).** A deterministic probe reads the real outcome
   and can *veto* the verdict. The OCR cross-check is deliberately
   **asymmetric**: a "hallucination" claim can be downgraded if OCR confirms the
   content is really there, but an "omission" claim is *never* waved through on
   OCR-absence (OCR is weaker than the vision model and misses things — letting
   a real omission pass would be a false-PASS).
2. **Semantic altitude.** The judge scores *meaning and outcome*, not
   character-level transcription. Trivial formatting differences or dense
   meaningless strings are not defects; an axis that cannot be reliably verified
   is left `uncertain` rather than guessed (no false PASS *or* FAIL).
3. **Integrity: judge ≠ builder.** The probe and the stimulus are the *tester's*
   own tools. The feature's developer may supply a *setup mechanism* (a fixture)
   when VISIO genuinely cannot create a stimulus itself — but the **scenario
   inputs and the verdict stay with the tester**. Handing the judge to the
   builder ends the integrity.

#### What can be tested — the capability frontier

The gate is **observability = a trace the Mac can reach** — packets, files,
logs, timing, UI — *not* "is it visible on screen." Things that aren't visible
usually still leave a Mac-reachable trace, so they're testable with the right
probe (network → a local mock server; cache → a timing proxy; app state →
AppleScript). "No probe yet" is a tooling gap, **not** "impossible." The genuine
limits are narrow: state that lives only on a remote server with no local trace;
the safety wall (irreversible actions like sending money are exercised only up
to the commit gate, never executed); and subjective taste.

### Run it

Requires Python 3 and a logged-in `claude` CLI (no API key needed; set
`SAPPHI_PROVIDER=openai` + `OPENAI_API_KEY` to use GPT instead).

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 1) Structural self-check — deterministic, 0 LLM calls. Should print 19 PASS / 0 FAIL.
./.venv/bin/python -m rubi selfcheck

# 2) The value ablation — Claude-alone vs Claude+VISIO on deceptive cases.
./.venv/bin/python verify_visio_value.py

# 3) Judging-quality benches.
./.venv/bin/python verify_quality_lens.py
./.venv/bin/python verify_visual_quality.py

# 4) A full live test of a feature (drives real apps — needs an active GUI session).
./.venv/bin/python -m rubi visio test "<feature description>" --mode live
```

`rubi visio` subcommands: `plan` (generate an edge-case test plan without
running it), `test` (plan → execute → judge → report), `rerun` (re-run a saved
plan and diff for regressions), `fixtures` (dump the fixture requests VISIO
needs the developer to provide), `loop` (closed build→review→fix→re-verify).

### Repository layout

```
rubi/                 # the test BRAIN
  visio.py            #   plan → execute → judge orchestration (+ authoritative oracle)
  verify_runner.py    #   correctness + quality judging in one parallel call; OCR cross-check
  taskspec.py         #   goal → structured task contract + lens contracts
  oracles.py          #   deterministic outcome oracles
  emeri.py            #   verified-lesson memory + regression recall
  records.py          #   verification audit log
  selfcheck.py        #   19-check structural integrity (no LLM)
  provider.py         #   LLM backend: claude CLI (login) / OpenAI / local
sapphi/               # the HANDS (macOS observe + execute)
  agent.py act.py     #   smart_click cascade: Accessibility → OCR → visual grounding
  perceive.py ocr.py  #   screen perception, OCR
  triggers.py         #   deterministic stimulus injectors (file/notification/clipboard/...)
  probes.py           #   deterministic outcome readers (11 probes)
  netmock.py perf.py  #   localhost mock server (network probe) + timing measurement
sut/                  # sample features-under-test (the "developer" side)
verify_visio_value.py # ablation benchmark — measured result (file/clipboard/settings)
verify_visio_adversarial.py  # adversarial self-audit (red-team) — try to fool VISIO's judge
verify_visio_live.py  # live app path — drives real Notes app, reads the real note
verify_emeri.py       # memory (EMERI) recall-quality measurement
verify_emeri_value.py # does memory improve design (cold vs memory)
verify_emeri_e2e.py   # autonomous learning loop, end-to-end
verify_*.py           # judging-quality benches
run_*.py              # standalone demos (notify→Notes, trust floor, unattended/launchd, ...)
```

### Honest limits

- The judge is only as good as the **probe** for a given outcome type. Outcome
  types whose probe isn't written yet need one added (probes are per
  *observation-type*, so they're reused — but the long tail exists).
- **Unattended (launchd) runs** are clean for file/headless work but
  clipboard/GUI operations are session-bound and hang under a LaunchAgent — a
  no-GUI feature, or an active Aqua session, is required.
- **Reading ceiling**: extremely dense or meaningless content is read less
  reliably than a human would (the judge marks such axes `uncertain` rather than
  guessing).
- Live full-feature tests still need an active macOS GUI session; this is not a
  pure-headless CI tool.

### Status & scope

This is a **research / portfolio** project, not a product. It exists to explore
one question — *can an independent agent trustworthily verify another agent's
real-environment actions?* — and to measure the answer (the false-PASS 8→0
result above). Code comments are primarily in Korean.
