"""VISIO CLI — 에이전트가 만든 맥 기능을 *실제 환경*에서 독립 검증하는 테스트 하네스.

  python -m rubi visio plan  "<기능 설명>" [--save]
  python -m rubi visio test  "<기능 설명>" [--mode rehearse] [--local-judge auto]
  python -m rubi visio rerun  --plan-file <json>      # 회귀 비교
  python -m rubi visio fixtures --plan-file <json>    # VISIO가 못 만드는 자극 → 픽스처 요청서
  python -m rubi selfcheck                            # 구조화 통합 selfcheck (무조작, LLM 0회)
  python -m rubi provider                             # LLM provider 상태
"""

from __future__ import annotations

import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="rubi",
        description="VISIO — 에이전트가 만든 기능을 실제 환경에서 독립 검증하는 테스트 하네스")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("selfcheck", help="SAPPHI/RUBI/EMERI 구조화 통합 selfcheck (무조작, LLM 0회)")
    sub.add_parser("provider", help="현재 LLM provider(claude/openai/local) 상태")

    vp = sub.add_parser("visio", help="VISIO — 에이전트가 만든 기능을 실제 환경에서 독립 검증")
    vsub = vp.add_subparsers(dest="visio_cmd")
    vpl = vsub.add_parser("plan", help="기능 설명 → 엣지케이스 테스트 계획 생성(실행 안 함)")
    vpl.add_argument("feature")
    vpl.add_argument("-n", type=int, default=6)
    vpl.add_argument("--judge-model", default=None)
    vpl.add_argument("--build-model", default=None)
    vpl.add_argument("--out", default="visio_out")
    vpl.add_argument("--routines", default="rubi_routines")
    vpl.add_argument("--offline", action="store_true", help="LLM 없이 fallback 케이스만")
    vpl.add_argument("--save", action="store_true", help="계획을 json으로 저장(재현/재실행용)")
    vte = vsub.add_parser("test", help="기능 설명 → 계획 생성 → 각 케이스 관찰·실행·판정 → 리포트")
    vte.add_argument("feature")
    vte.add_argument("--mode", choices=["plan", "rehearse", "live"], default="rehearse")
    vte.add_argument("-n", type=int, default=6)
    vte.add_argument("--judge-model", default=None)
    vte.add_argument("--build-model", default=None)
    vte.add_argument("--out", default="visio_out")
    vte.add_argument("--routines", default="rubi_routines")
    vte.add_argument("--offline", action="store_true", help="계획을 LLM 없이 fallback 케이스로")
    vte.add_argument("--local-vlm", action="store_true", help="환각 재검에 로컬 VLM 사용(발열↑)")
    vte.add_argument("--local-judge", choices=["off", "auto", "on"], default="auto",
                     help="판정을 로컬 Gemma4로(off/auto/on). auto=로컬우선·불확실시 클라우드 escalate")
    vte.add_argument("--save", action="store_true")
    vrr = vsub.add_parser("rerun", help="저장된 계획을 재실행해 회귀(regression) 비교")
    vrr.add_argument("--plan-file", required=True)
    vrr.add_argument("--mode", choices=["plan", "rehearse", "live"], default="rehearse")
    vrr.add_argument("--judge-model", default=None)
    vrr.add_argument("--build-model", default=None)
    vrr.add_argument("--out", default="visio_out")
    vrr.add_argument("--routines", default="rubi_routines")
    vrr.add_argument("--local-vlm", action="store_true")
    vrr.add_argument("--local-judge", choices=["off", "auto", "on"], default="auto")
    vfx = vsub.add_parser("fixtures", help="계획의 *픽스처 요청서*(VISIO가 못 만드는 자극) 덤프 — 에이전트가 작성")
    vfx.add_argument("--plan-file", required=True)
    vfx.add_argument("--judge-model", default=None)
    vfx.add_argument("--build-model", default=None)
    vlo = vsub.add_parser("loop", help="닫힌 루프 — 빌드→리뷰→fixer 자동수정→재확인, 수렴까지")
    vlo.add_argument("--plan-file", required=True, help="테스트 계획 json(cases 포함)")
    vlo.add_argument("--sut", required=True, help="fixer가 *편집할* SUT 파일 경로")
    vlo.add_argument("--max-iters", type=int, default=3)
    vlo.add_argument("--fixer-model", default=None, help="SUT 수정 모델(기본 build_model; judge와 달라야 독립)")
    vlo.add_argument("--judge-model", default=None)
    vlo.add_argument("--build-model", default=None)
    vlo.add_argument("--out", default="visio_out/loop")
    vlo.add_argument("--routines", default="rubi_routines")
    vlo.add_argument("--local-judge", choices=["off", "auto", "on"], default="off")
    vlo.add_argument("--randomize", action="store_true", help="매 iter 자극 랜덤화(하드코딩 게이밍 차단)")

    args = parser.parse_args(argv)

    if args.cmd == "provider":
        from . import provider as P
        cur = P.provider_name()
        print(f"현재 provider: {cur}  (SAPPHI_PROVIDER 환경변수로 전환)")
        for p in ("claude", "openai", "local"):
            ok, why = P.available(p)
            print(f"  {'✅' if ok else '❌'} {p}: {why}{' ← 현재' if p == cur else ''}")
        return 0

    if args.cmd == "selfcheck":
        from .selfcheck import main as selfcheck_main
        return selfcheck_main()

    if args.cmd == "visio":
        from . import visio
        judge = getattr(args, "judge_model", None) or visio.DEFAULT_JUDGE
        build = getattr(args, "build_model", None) or visio.DEFAULT_BUILD
        if args.visio_cmd == "plan":
            plan = visio.generate_test_plan(args.feature, judge_model=judge, build_model=build,
                                            n=args.n, offline=args.offline, routines_dir=args.routines)
            visio.print_plan(plan)
            if args.save:
                print(f"\n계획 저장: {visio.save_plan(plan, args.out)}")
            return 0
        if args.visio_cmd == "test":
            plan = visio.generate_test_plan(args.feature, judge_model=judge, build_model=build,
                                            n=args.n, offline=args.offline, routines_dir=args.routines)
            if args.save:
                visio.save_plan(plan, args.out)
            visio.print_plan(plan)
            rep = visio.run_test_plan(plan, mode=args.mode, out_dir=args.out,
                                      routines_dir=args.routines, local_vlm=args.local_vlm,
                                      local_judge=args.local_judge)
            visio.print_summary(rep)
            return 0 if (rep.failed == 0 and rep.errored == 0) else 1
        if args.visio_cmd == "rerun":
            plan = visio.load_plan(args.plan_file, judge_model=judge, build_model=build)
            rep = visio.run_test_plan(plan, mode=args.mode, out_dir=args.out,
                                      routines_dir=args.routines, local_vlm=args.local_vlm,
                                      local_judge=args.local_judge)
            visio.print_regression(rep)
            return 0 if not (rep.regression or {}).get("new_fails") else 1
        if args.visio_cmd == "fixtures":
            plan = visio.load_plan(args.plan_file, judge_model=judge, build_model=build)
            visio.print_fixture_requests(plan)
            return 0
        if args.visio_cmd == "loop":
            from . import visio_loop
            plan = visio.load_plan(args.plan_file, judge_model=judge, build_model=build)
            res = visio_loop.run_closed_loop(
                plan, args.sut, fixer_model=args.fixer_model, max_iters=args.max_iters,
                out_dir=args.out, routines_dir=args.routines, local_judge=args.local_judge,
                randomize_stimulus=args.randomize)
            print(f"\n{'='*56}\n닫힌 루프: ok={res['ok']}  iters={res['iters']}  ({res.get('reason','수렴')})")
            for h in res["history"]:
                print(f"  iter{h['iter']}: pass {h['pass']}/{h['total']}  accepted={h['accepted']}  "
                      f"신뢰닻={h['trust']}" + ("  ⚠️진전없음" if h.get("no_progress") else ""))
            return 0 if res["ok"] else 1
        print("visio plan|test|rerun|fixtures|loop 중 하나를 지정하세요.")
        return 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
