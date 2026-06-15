"""성능/시간 측정 — '기능이 너무 느리면 못 써먹는다'를 결정론적으로 검증.

타이밍은 *맥에서 관찰 가능*(실행 wall-clock). 단 비결정적(부하·캐시에 흔들림) → *N회 median + 임계값*으로
판정해야 flaky 안 함(단발 측정 금지). under(measurement, max_ms) 가 too-slow 게이트.
efficiency_cost 품질축의 *객관 앵커*로도 쓰임(LLM '느린 것 같다' 의견 대신 실측 ms).
"""
from __future__ import annotations

import statistics
import subprocess
import time


def measure(cmd, *, runs: int = 3, timeout: int = 120, cwd=None, env=None) -> dict:
    """명령(list)을 runs회 실행해 각 wall-clock(ms) 측정 → {median_ms,min_ms,max_ms,runs,samples}.
    첫 회는 콜드(캐시 미적중)일 수 있어 median 이 단발보다 안정적."""
    samples = []
    for _ in range(max(1, runs)):
        t0 = time.perf_counter()
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd, env=env)
        except subprocess.TimeoutExpired:
            samples.append(timeout * 1000.0)        # 타임아웃 = 임계 초과로 취급
            continue
        samples.append((time.perf_counter() - t0) * 1000.0)
    return {"median_ms": round(statistics.median(samples), 1),
            "min_ms": round(min(samples), 1), "max_ms": round(max(samples), 1),
            "runs": len(samples), "samples": [round(s, 1) for s in samples]}


def under(measurement: dict, max_ms: float) -> dict:
    """median 이 max_ms 이하면 통과(=쓸 만한 속도). 초과면 too-slow → achieved=False.
    반환 {achieved, evidence, median_ms} — probe 와 같은 모양이라 verdict에 그대로 꽂힘."""
    med = measurement.get("median_ms", float("inf"))
    ok = med <= max_ms
    return {"ok": True, "achieved": ok, "median_ms": med,
            "evidence": f"median {med}ms {'≤' if ok else '>'} 한도 {max_ms}ms (samples={measurement.get('samples')})",
            "detail": f"{measurement.get('runs')}회 측정"}
