# orchestrator/cli.py
"""`orchestrate` command-line entry point."""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, api_model_of, effective_models, load_config
from .runlog import LOG_DIR, log_event
from .secrets import key_status


def cmd_status(args) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1

    models = effective_models(cfg)
    keys = key_status(cfg)

    print(f"model-orchestrator {__version__}")
    print(f"config version {cfg.get('version')}")
    print()

    print("Lanes")
    for lane, spec in cfg["lanes"].items():
        print(f"  {lane:8} {', '.join(spec['providers'])}")
    print()

    print("Provider keys")
    for prov, present in keys.items():
        print(f"  {prov:10} {'set' if present else 'MISSING'}")
    print()

    print(f"Active models ({len(models)})")
    for name, m in models.items():
        roles = ", ".join(m.get("roles", [])) or "-"
        price = m["price"]
        print(f"  {name:26} {m['provider']:9} ${price['in']}/{price['out']:<6} [{roles}]")
    print()

    path = log_event("status", active_models=len(models), keys_present=sum(keys.values()))
    print(f"logged -> {path}")
    print(f"log dir  {LOG_DIR}")
    return 0


def cmd_route(args) -> int:
    from .prefilter import prefilter
    from .router import route, LaneViolationError, RoutingError

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1

    # Big multi-line prompts get mangled by shell arg-tokenisation (embedded quotes,
    # newlines). Reading the task from stdin when it is "-" sidesteps that entirely:
    #   echo "..." | orchestrate route - --judge   (or  $task | orchestrate route -)
    task_text = sys.stdin.read() if args.task == "-" else args.task

    task: dict = {"description": task_text}
    if args.sensitive:
        task["sensitive"] = True
    if args.context_tokens:
        task["context_tokens"] = args.context_tokens
    if args.requires_cot:
        task["requires_cot"] = True

    tagged = prefilter(task, sensitive=bool(args.sensitive))

    try:
        model_name, endpoint = route(tagged, cfg)
    except (LaneViolationError, RoutingError) as e:
        print(f"routing error: {e}", file=sys.stderr)
        return 1

    # --worker pins a specific roster model (e.g. to get a cross-PROVIDER second
    # opinion the router wouldn't pick). Still honours the trusted-lane invariant
    # for sensitive tasks; recomputes the EU endpoint for a sensitive OpenAI worker.
    if args.worker:
        if args.worker not in cfg.get("models", {}):
            print(f"error: --worker '{args.worker}' is not in the roster", file=sys.stderr)
            return 1
        model_name = args.worker
        wprov = cfg["models"][model_name].get("provider")
        if args.sensitive and wprov not in set(cfg["lanes"]["trusted"]["providers"]):
            print(f"error: --worker '{model_name}' (provider '{wprov}') is outside the "
                  f"trusted lane; drop --sensitive or choose a trusted worker", file=sys.stderr)
            return 1
        endpoint = (cfg.get("providers", {}).get("openai", {}).get("eu_endpoint")
                    if (args.sensitive and wprov == "openai") else None)

    model_cfg = cfg.get("models", {}).get(model_name, {})
    price = model_cfg.get("price", {})
    provider = model_cfg.get("provider", "")
    trusted_providers = set(cfg["lanes"]["trusted"]["providers"])
    lane = "trusted" if provider in trusted_providers else "open"

    # Estimate cost at 1 000 in + 500 out tokens (rough single-call estimate)
    est_cost = (1_000 / 1_000_000 * price.get("in", 0) +
                500 / 1_000_000 * price.get("out", 0))

    print(f"model:     {model_name}")
    print(f"provider:  {provider}")
    print(f"lane:      {lane}")
    print(f"type:      {tagged.get('type')}")
    print(f"complexity: {tagged.get('complexity')}")
    print(f"risk:      {tagged.get('risk')}")
    print(f"sensitive: {tagged.get('sensitive', False)}")
    if endpoint:
        print(f"endpoint:  {endpoint}")
    print(f"est. cost: ${est_cost:.6f}  (1 000 in + 500 out tokens)")

    if args.dry_run:
        print("\n[dry-run] no API call made")
        return 0

    # ---- Live call ----
    from .adapters import get_adapter
    from .gate import gate, CostCeilingError

    # load_dotenv() is called inside key_status; must happen before adapter init
    # so that os.environ is populated when the SDK client reads the key.
    keys = key_status(cfg)
    if not keys.get(provider):
        print(f"error: {provider.upper()}_API_KEY is not set — cannot make a live call", file=sys.stderr)
        return 1

    try:
        adapter = get_adapter(provider)
    except ValueError as e:
        print(f"adapter error: {e}", file=sys.stderr)
        return 1

    print("\ncalling API ...")
    try:
        # endpoint is non-None only for sensitive OpenAI calls (EU data residency);
        # adapters that ignore it (deepseek/mistral/google/anthropic) accept **kwargs.
        output = adapter.complete(prompt=tagged["description"],
                                  model=api_model_of(model_name, cfg),
                                  endpoint=endpoint, max_tokens=args.max_tokens)
    except Exception as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1

    # Inject token counts into task so gate can price the call
    tagged["model"] = model_name
    tagged["in_tokens"] = len(tagged["description"].split()) * 4   # rough estimate
    tagged["out_tokens"] = len(output.split()) * 4

    try:
        result = gate(output, tagged, cfg)
    except CostCeilingError as e:
        print(f"cost ceiling: {e}", file=sys.stderr)
        return 1

    log_event(
        "route",
        model=model_name,
        lane=lane,
        task_type=tagged.get("type"),
        cost_usd=result["cost_usd"],
        flags=result["flags"],
        passed=result["passed"],
    )

    print(f"\n--- output ---\n{output}")
    print(f"\n--- gate ---")
    print(f"passed: {result['passed']}")
    if result["flags"]:
        print(f"flags:  {', '.join(result['flags'])}")
    print(f"cost:   ${result['cost_usd']:.6f}")

    # ---- Judge (P2; opt-in via --judge) ----
    if args.judge:
        from .judge import judge as run_judge, JudgeProviderError, JudgeResponseError
        print("\njudging ...")
        try:
            verdict = run_judge(output, tagged, cfg)
        except (JudgeProviderError, JudgeResponseError) as e:
            print(f"judge error: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"judge API error: {e}", file=sys.stderr)
            return 1

        log_event(
            "judge",
            worker_model=model_name,
            task_type=tagged.get("type"),
            judge_model=verdict["judge_model"],
            score=verdict["score"],
            escalated=verdict["escalated"],
            meta_model=(verdict["meta"] or {}).get("model"),
            final_score=verdict["final_score"],
            passed=verdict["passed"],
        )

        print(f"\n--- judge ---")
        print(f"judge:      {verdict['judge_model']}")
        print(f"score:      {verdict['score']:.2f}")
        print(f"reasoning:  {verdict['reasoning']}")
        if verdict["escalated"]:
            m = verdict["meta"]
            print(f"escalated:  yes -> {m['model']} (score {m['score']:.2f})")
            print(f"meta says:  {m['reasoning']}")
        else:
            print("escalated:  no")
        print(f"final:      {verdict['final_score']:.2f}  passed={verdict['passed']}")
        return 0 if (result["passed"] and verdict["passed"]) else 1

    return 0 if result["passed"] else 1


def cmd_report(args) -> int:
    from .report import load_events, build_report, render
    events = load_events(args.days)
    print(render(build_report(events), args.days))
    return 0


def _stub(name: str, phase: str):
    def run(args) -> int:
        print(f"`{name}` is implemented in {phase}. P0 ships the skeleton only.")
        return 0
    return run


def main(argv=None) -> int:
    # Windows consoles default to cp1252; reconfigure to UTF-8 so model output
    # containing em-dashes, curly quotes, etc. doesn't crash the CLI.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # stdin too: piped prompts (`... | orchestrate route -`) with non-ASCII bytes
    # otherwise decode to lone surrogates that crash the adapter's UTF-8 encode.
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(
        prog="orchestrate",
        description="Claude-orchestrated, cost-aware, adversarial multi-model router.",
    )
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="show config, keys, active roster; write a log line")
    s.set_defaults(func=cmd_status)

    r = sub.add_parser("route", help="[P1] route a task; --dry-run to skip the API call")
    r.add_argument("task", help="task description")
    r.add_argument("--sensitive", action="store_true", help="force sensitive/trusted lane")
    r.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="show routing decision without making an API call")
    r.add_argument("--context-tokens", type=int, default=0, dest="context_tokens",
                   help="token count hint for long-context routing")
    r.add_argument("--requires-cot", action="store_true", dest="requires_cot",
                   help="force the reasoning lane (chain-of-thought reasoner)")
    r.add_argument("--max-tokens", type=int, default=2048, dest="max_tokens",
                   help="max output tokens for the worker call (default 2048; raise for long analyses)")
    r.add_argument("--worker", default=None,
                   help="pin a specific roster model as the worker (e.g. for a cross-provider second opinion)")
    r.add_argument("--judge", action="store_true",
                   help="[P2] cross-provider judge scores the output; escalates if low")
    r.set_defaults(func=cmd_route)

    rep = sub.add_parser("report", help="[P4] summarize cost + judge stats from the logs")
    rep.add_argument("--days", type=int, default=7,
                     help="how many days back to include (default 7)")
    rep.set_defaults(func=cmd_report)

    for name, phase in [("gate", "P1"), ("debate", "P5")]:
        sp = sub.add_parser(name, help=f"[{phase}] stub")
        sp.set_defaults(func=_stub(name, phase))

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
