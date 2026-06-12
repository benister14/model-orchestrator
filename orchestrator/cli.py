# orchestrator/cli.py
"""`orchestrate` command-line entry point."""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, effective_models, load_config
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

    task: dict = {"description": args.task}
    if args.sensitive:
        task["sensitive"] = True
    if args.context_tokens:
        task["context_tokens"] = args.context_tokens

    tagged = prefilter(task, sensitive=bool(args.sensitive))

    try:
        model_name, endpoint = route(tagged, cfg)
    except (LaneViolationError, RoutingError) as e:
        print(f"routing error: {e}", file=sys.stderr)
        return 1

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

    try:
        adapter = get_adapter(provider)
    except ValueError as e:
        print(f"adapter error: {e}", file=sys.stderr)
        return 1

    keys = key_status(cfg)
    if not keys.get(provider):
        print(f"error: {provider.upper()}_API_KEY is not set — cannot make a live call", file=sys.stderr)
        return 1

    print("\ncalling API ...")
    try:
        output = adapter.complete(prompt=tagged["description"], model=model_name)
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
    return 0 if result["passed"] else 1


def _stub(name: str, phase: str):
    def run(args) -> int:
        print(f"`{name}` is implemented in {phase}. P0 ships the skeleton only.")
        return 0
    return run


def main(argv=None) -> int:
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
    r.set_defaults(func=cmd_route)

    for name, phase in [("gate", "P1"), ("judge", "P2"),
                        ("debate", "P5"), ("report", "P4")]:
        sp = sub.add_parser(name, help=f"[{phase}] stub")
        sp.set_defaults(func=_stub(name, phase))

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
