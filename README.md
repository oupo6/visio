# VISIO — independent verification for agent-built Mac features

> A coding agent can *build* a macOS automation. VISIO checks whether it
> **actually works in the real environment** — by driving the live apps itself,
> reading the *actual resulting state*, and refusing to trust the agent's own
> "✅ done" report.

When an agent both writes a feature and tests it, the test is *confirmatory*:
it passes by construction (the same mind defines the spec and checks it). The
failure mode that survives is the **silent one** — the agent reports success,
but the real artifact is empty, truncated, or wrong. VISIO is a separate
*tester* whose only job is to catch that.

---

## The measured result (the point of the project)

An ablation benchmark (`verify_visio_value.py`) pits **Claude alone** (judging
from the surface / self-report) against **Claude + VISIO** (reading the actual
on-disk state) on 8 cases, 4 of them *deceptive* — the self-report says
"success" but the real output is empty / truncated / wrong / a placeholder.

| metric | Claude alone | + VISIO |
|---|---:|---:|
| **false-PASS (critical)** | **4** | **0** |
| false-FAIL | 0 | 0 |
| deceptive cases caught | **0 / 4** | **4 / 4** |
| consistency (flaky verdicts) | 1 | 0 (deterministic) |

VISIO's value is **not** "smarter than Claude." On obvious cases the two agree.
Its value is **discipline × automation**: it does not believe a self-report, it
reads the real state, and it does this with no human in the loop — every night,
every commit, at 3am.

Two more benches quantify the judging quality directly:
`verify_quality_lens.py` (text quality discrimination, 4/4) and
`verify_visual_quality.py` (a vision model judging rendered website design,
3/3).

---

## How it works

VISIO is three cooperating roles:

| role | name | job |
|---|---|---|
| **brain** | RUBI | plans edge-case scenarios, judges outcomes, runs the closed loop |
| **hands** | SAPPHI | observes (Accessibility-first → OCR → vision) and drives the live apps |
| **memory** | EMERI | stores verified pass/fail lessons, recalls them per task type for regression |

A test run is: **inject a stimulus → run the feature → read the real outcome →
judge → report → compare against last time.**

### The core judging principle: *read it directly when you can*

The naive design judges everything by screenshotting the screen and asking a
vision model. That is the right judge **only for purely visual results** (does
this website *look* well-designed?). For everything else VISIO reads the actual
state directly and lets a deterministic **oracle** decide:

- a saved note → read the plaintext back via AppleScript
- a file/clipboard result → read the bytes and hash them
- an app state → query Accessibility / `defaults` / the app's own scripting

This is what makes false-PASS ≈ 0: the verdict is grounded in the artifact, not
in a model's impression of a screenshot.

### Three safeguards against a *false* PASS

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

### What can be tested — the capability frontier

The gate is **observability = a trace the Mac can reach** — packets, files,
logs, timing, UI — *not* "is it visible on screen." Things that aren't visible
usually still leave a Mac-reachable trace, so they're testable with the right
probe (network → a local mock server; cache → a timing proxy; app state →
AppleScript). "No probe yet" is a tooling gap, **not** "impossible." The genuine
limits are narrow: state that lives only on a remote server with no local trace;
the safety wall (irreversible actions like sending money are exercised only up
to the commit gate, never executed); and subjective taste.

---

## Run it

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

---

## Repository layout

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
verify_visio_value.py # ablation benchmark — the measured result above
verify_*.py           # judging-quality benches
run_*.py              # standalone demos (notify→Notes, trust floor, unattended/launchd, ...)
```

---

## Honest limits

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

---

## Status & scope

This is a **research / portfolio** project, not a product. It exists to explore
one question — *can an independent agent trustworthily verify another agent's
real-environment actions?* — and to measure the answer (the false-PASS 4→0
result above). Code comments are primarily in Korean.
