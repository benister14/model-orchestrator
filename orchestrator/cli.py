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

    for name, phase in [("route", "P1"), ("gate", "P1"), ("judge", "P2"),
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
