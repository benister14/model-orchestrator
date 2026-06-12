"""Rule-based gate: PII scan + per-session cost ceiling. No LLM required."""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email",    re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')),
    ("phone_fr", re.compile(r'\b(?:\+33\s?|0)[1-9](?:[\s.\-]?\d{2}){4}\b')),
    ("siren",    re.compile(r'\b\d{9}\b')),
    ("siret",    re.compile(r'\b\d{14}\b')),
]


class CostCeilingError(Exception):
    pass


def _scan_pii(text: str) -> list[str]:
    """Return a list of pii:<label> flags for every PII pattern matched."""
    return [f"pii:{label}" for label, pat in _PII_PATTERNS if pat.search(text)]


def _session_spend_usd() -> float:
    """Sum cost_usd from today's log file. Reads ORCHESTRATOR_LOG_DIR from env at call time."""
    log_dir = Path(
        os.environ.get("ORCHESTRATOR_LOG_DIR", Path.home() / ".orchestrator" / "logs")
    )
    log_file = log_dir / f"{date.today().isoformat()}.jsonl"
    if not log_file.exists():
        return 0.0
    total = 0.0
    with open(log_file, encoding="utf-8") as f:
        for line in f:
            try:
                total += json.loads(line).get("cost_usd", 0.0)
            except json.JSONDecodeError:
                pass
    return total


def _estimate_cost(task: dict, cfg: dict) -> float:
    """Estimate cost from task["model"], task["in_tokens"], task["out_tokens"]."""
    model_name = task.get("model")
    if not model_name:
        return 0.0
    price = cfg.get("models", {}).get(model_name, {}).get("price", {})
    in_cost = task.get("in_tokens", 0) / 1_000_000 * price.get("in", 0.0)
    out_cost = task.get("out_tokens", 0) / 1_000_000 * price.get("out", 0.0)
    return in_cost + out_cost


def gate(output: str, task: dict, cfg: dict) -> dict:
    """
    Scan output for PII and check the per-session cost ceiling.

    Returns:
        {"output": str, "flags": list[str], "cost_usd": float, "passed": bool}

    Raises:
        CostCeilingError: if adding this call's cost would exceed the session ceiling.
    """
    flags: list[str] = []

    # PII scan on model output
    flags.extend(_scan_pii(output))

    # PII scan on client_* fields in the task
    for key, val in task.items():
        if key.startswith("client_") and isinstance(val, str):
            for flag in _scan_pii(val):
                flags.append(f"{flag}(task.{key})")

    cost_usd = _estimate_cost(task, cfg)

    # Cost ceiling check
    ceiling = cfg.get("thresholds", {}).get("cost_ceiling_per_session_usd", 2.0)
    current_spend = _session_spend_usd()
    if current_spend + cost_usd > ceiling:
        raise CostCeilingError(
            f"session spend ${current_spend:.4f} + this call ${cost_usd:.6f} "
            f"would exceed ceiling ${ceiling:.2f}"
        )

    return {"output": output, "flags": flags, "cost_usd": cost_usd, "passed": len(flags) == 0}
