"""Observability — aggregate the JSONL run logs into a readable report.  [P4]

Reads the `route` and `judge` events written by the CLI (see runlog.py) and
summarizes cost by task type, judge pass / escalation rates, and the cheapest /
priciest calls. Task types whose escalation rate exceeds ESCALATION_FLAG_RATE are
flagged as debate candidates (P5).
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

# Task types escalating above this rate are candidates for adversarial debate (PLAN P4).
ESCALATION_FLAG_RATE = 0.30


def _log_dir() -> Path:
    return Path(
        os.environ.get("ORCHESTRATOR_LOG_DIR", Path.home() / ".orchestrator" / "logs")
    )


def load_events(days: int, today: date | None = None,
                log_dir: Path | None = None) -> list[dict]:
    """Return all JSONL records from the last `days` daily log files (inclusive of today)."""
    today = today or date.today()
    log_dir = log_dir or _log_dir()
    events: list[dict] = []
    for i in range(max(days, 0)):
        day = today - timedelta(days=i)
        f = log_dir / f"{day.isoformat()}.jsonl"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def build_report(events: list[dict]) -> dict:
    routes = [e for e in events if e.get("event") == "route"]
    judges = [e for e in events if e.get("event") == "judge"]

    # ---- cost by task type (from route events) ----
    by_type: dict[str, dict] = {}
    for e in routes:
        t = e.get("task_type") or "unknown"
        slot = by_type.setdefault(t, {"count": 0, "total_cost": 0.0})
        slot["count"] += 1
        slot["total_cost"] += float(e.get("cost_usd") or 0.0)
    for slot in by_type.values():
        slot["avg_cost"] = slot["total_cost"] / slot["count"] if slot["count"] else 0.0

    total_cost = sum(float(e.get("cost_usd") or 0.0) for e in routes)

    # ---- judge pass / escalation rates ----
    n_judged = len(judges)
    pass_count = sum(1 for e in judges if e.get("passed"))
    escalation_count = sum(1 for e in judges if e.get("escalated"))
    pass_rate = pass_count / n_judged if n_judged else None
    escalation_rate = escalation_count / n_judged if n_judged else None

    # ---- escalation by task type (flag debate candidates) ----
    esc_by_type: dict[str, dict] = {}
    for e in judges:
        t = e.get("task_type") or "unknown"
        slot = esc_by_type.setdefault(t, {"judged": 0, "escalated": 0})
        slot["judged"] += 1
        if e.get("escalated"):
            slot["escalated"] += 1
    for slot in esc_by_type.values():
        slot["rate"] = slot["escalated"] / slot["judged"] if slot["judged"] else 0.0
        slot["flagged"] = slot["rate"] > ESCALATION_FLAG_RATE

    # ---- cheapest / priciest call ----
    cheapest = priciest = None
    if routes:
        cheapest = min(routes, key=lambda e: float(e.get("cost_usd") or 0.0))
        priciest = max(routes, key=lambda e: float(e.get("cost_usd") or 0.0))

    return {
        "n_routes": len(routes),
        "n_judged": n_judged,
        "total_cost": total_cost,
        "by_task_type": by_type,
        "pass_rate": pass_rate,
        "escalation_rate": escalation_rate,
        "escalation_by_task_type": esc_by_type,
        "cheapest": cheapest,
        "priciest": priciest,
    }


def render(report: dict, days: int) -> str:
    L: list[str] = []
    L.append(f"orchestrate report - last {days} day(s)")
    L.append(f"  routes: {report['n_routes']}   judged: {report['n_judged']}   "
             f"worker cost: ${report['total_cost']:.6f}")
    L.append("")

    L.append("Cost by task type")
    if report["by_task_type"]:
        for t, s in sorted(report["by_task_type"].items(),
                           key=lambda kv: kv[1]["total_cost"], reverse=True):
            L.append(f"  {t:14} {s['count']:>4} calls   "
                     f"${s['total_cost']:.6f} total   ${s['avg_cost']:.6f} avg")
    else:
        L.append("  (no route events)")
    L.append("")

    L.append("Judge")
    if report["n_judged"]:
        L.append(f"  pass rate:       {report['pass_rate'] * 100:.0f}%")
        L.append(f"  escalation rate: {report['escalation_rate'] * 100:.0f}%")
        for t, s in sorted(report["escalation_by_task_type"].items(),
                           key=lambda kv: kv[1]["rate"], reverse=True):
            flag = "  (!) debate candidate >30%" if s["flagged"] else ""
            L.append(f"  {t:14} {s['escalated']}/{s['judged']} escalated  "
                     f"{s['rate'] * 100:.0f}%{flag}")
    else:
        L.append("  (no judge events - run a task with --judge)")
    L.append("")

    L.append("Extremes")
    if report["cheapest"]:
        c, p = report["cheapest"], report["priciest"]
        L.append(f"  cheapest: ${float(c.get('cost_usd') or 0):.6f}  "
                 f"{c.get('model')}  [{c.get('task_type')}]")
        L.append(f"  priciest: ${float(p.get('cost_usd') or 0):.6f}  "
                 f"{p.get('model')}  [{p.get('task_type')}]")
    else:
        L.append("  (no route events)")

    return "\n".join(L)
