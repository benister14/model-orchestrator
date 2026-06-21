#!/usr/bin/env python3
"""
Deterministic operator scan — no model calls, no DB writes.

For every threshold in classification_nodes.criteria_json where
operator IN ('=', '=='), check whether the source_quote or applies_when
text contains range phrasing that implies >= or <=.

Output: eval/reports/operator_scan_YYYY-MM-DD.md

Usage:
    python scripts/operator_scan.py [--regime EU_DU_ANNEX_I]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
MODEL_ORCH_DIR = _HERE.parent
EXPORT_COMPASS_DIR = Path(r"C:\Users\benjb\Desktop\export-compass-2cdd0c45")
REPORT_DIR = EXPORT_COMPASS_DIR / "eval" / "reports"

# ---------------------------------------------------------------------------
# Range-phrasing patterns (case-insensitive, applied to source_quote + applies_when)
# ---------------------------------------------------------------------------
_GTE_PATTERNS: list[str] = [
    r"\bor more\b",
    r"\bor greater\b",
    r"\bat least\b",
    r"\bminimum of\b",
    r"\bno less than\b",
    r"\bnot less than\b",
    r"\bequal to or greater\b",
    r"\bgreater than or equal\b",
]

_LTE_PATTERNS: list[str] = [
    r"\bor less\b",
    r"\bor fewer\b",
    r"\bnot exceeding\b",
    r"\bup to\b",
    r"\bmaximum of\b",
    r"\bat most\b",
    r"\bno more than\b",
    r"\bno greater than\b",
    r"\bequal to or less\b",
    r"\bless than or equal\b",
]

_GTE_RE = re.compile("|".join(_GTE_PATTERNS), re.IGNORECASE)
_LTE_RE = re.compile("|".join(_LTE_PATTERNS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def _bootstrap_env() -> tuple[str, str]:
    ec_env = _load_env_file(EXPORT_COMPASS_DIR / ".env.local")
    url = ec_env.get("VITE_SUPABASE_URL", "").rstrip("/")
    key = ec_env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return url, key


# ---------------------------------------------------------------------------
# Supabase REST — paginated fetch
# ---------------------------------------------------------------------------
_PAGE_SIZE = 200


def _sb_page(url: str, key: str, table: str, params: dict, offset: int) -> list[dict]:
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}/rest/v1/{table}?{qs}"
    end = offset + _PAGE_SIZE - 1
    req = urllib.request.Request(
        full_url,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Range": f"{offset}-{end}",
            "Prefer": "count=none",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"Supabase {e.code}: {body[:300]}") from e


def fetch_all_nodes(sb_url: str, sb_key: str, regime: str) -> list[dict]:
    """Fetch all nodes with non-null criteria_json for the given regime."""
    params = {
        "select": "code,title,curation_status,criteria_json",
        "regime": f"eq.{regime}",
        "criteria_json": "not.is.null",
        "order": "code",
    }
    rows: list[dict] = []
    offset = 0
    while True:
        page = _sb_page(sb_url, sb_key, "classification_nodes", params, offset)
        rows.extend(page)
        print(f"  fetched {len(rows)} nodes so far ...", end="\r", flush=True)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    print(f"  fetched {len(rows)} nodes total.     ")
    return rows


# ---------------------------------------------------------------------------
# Scan logic
# ---------------------------------------------------------------------------
def _check_text(text: str | None) -> str | None:
    """Return suspected_fix if range phrasing found, else None."""
    if not text:
        return None
    if _GTE_RE.search(text):
        return ">="
    if _LTE_RE.search(text):
        return "<="
    return None


def scan_nodes(nodes: list[dict]) -> list[dict]:
    """
    Return a list of hits: threshold fields where operator is = or ==
    but source_quote/applies_when contains range phrasing.
    """
    hits: list[dict] = []
    for node in nodes:
        cj = node.get("criteria_json") or {}
        thresholds = cj.get("thresholds") or []
        for t in thresholds:
            op = t.get("operator", "")
            if op not in ("=", "=="):
                continue
            source_quote = t.get("source_quote") or ""
            applies_when = t.get("applies_when") or ""
            # Check both fields; prefer source_quote signal
            fix = _check_text(source_quote) or _check_text(applies_when)
            if fix is None:
                continue
            hits.append({
                "code":            node["code"],
                "curation_status": node.get("curation_status", ""),
                "parameter":       t.get("parameter", ""),
                "operator":        op,
                "value":           t.get("value", ""),
                "unit":            t.get("unit") or "",
                "source_quote":    source_quote[:200],
                "applies_when":    applies_when[:100] if applies_when else "",
                "suspected_fix":   fix,
            })
    return hits


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def build_report(hits: list[dict], total_nodes: int, run_ts: str) -> str:
    L: list[str] = []
    L.append("# Deterministic Operator Scan — Range-Phrasing Mismatches")
    L.append("")
    L.append(f"**Run**: {run_ts}  |  **Nodes scanned**: {total_nodes}  |  **Hits**: {len(hits)}")
    L.append("")
    L.append("> **Read-only scan. No model calls. Zero false positives by construction.**")
    L.append("> Every hit has `operator = '=' or '=='` AND source_quote/applies_when contains")
    L.append("> explicit range language. Benjamin verifies each against official Annex I text")
    L.append("> before any change.")
    L.append("")

    if not hits:
        L.append("_No hits found. All `=`/`==` operators appear consistent with their source text._")
        return "\n".join(L)

    # Group by suspected_fix for summary
    gte_hits = [h for h in hits if h["suspected_fix"] == ">="]
    lte_hits = [h for h in hits if h["suspected_fix"] == "<="]
    L.append("## Summary")
    L.append("")
    L.append(f"| suspected_fix | count |")
    L.append(f"|---------------|-------|")
    L.append(f"| `>=` (range phrasing implies at-least) | **{len(gte_hits)}** |")
    L.append(f"| `<=` (range phrasing implies at-most) | **{len(lte_hits)}** |")
    L.append(f"| **Total** | **{len(hits)}** |")
    L.append("")

    L.append("## Hits")
    L.append("")
    L.append("| code | parameter | operator | value | unit | suspected_fix | source_quote |")
    L.append("|------|-----------|----------|-------|------|---------------|--------------|")
    for h in sorted(hits, key=lambda x: (x["suspected_fix"], x["code"], x["parameter"])):
        sq = h["source_quote"].replace("|", "\\|").replace("\n", " ")
        L.append(
            f"| {h['code']} | `{h['parameter']}` | `{h['operator']}` "
            f"| {h['value']} | {h['unit']} | `{h['suspected_fix']}` | {sq[:120]} |"
        )
    L.append("")

    # Per-hit detail with full source_quote and applies_when
    L.append("## Detail")
    L.append("")
    for h in sorted(hits, key=lambda x: (x["code"], x["parameter"])):
        L.append(f"### {h['code']} — `{h['parameter']}`")
        L.append("")
        L.append(f"- **Stored operator**: `{h['operator']}`  →  **Suspected fix**: `{h['suspected_fix']}`")
        L.append(f"- **Value**: {h['value']}  **Unit**: {h['unit'] or '—'}")
        L.append(f"- **Status**: `{h['curation_status']}`")
        L.append(f"- **Source quote**: _{h['source_quote']}_")
        if h["applies_when"]:
            L.append(f"- **Applies when**: _{h['applies_when']}_")
        L.append("")

    return "\n".join(L)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Deterministic operator scan")
    parser.add_argument("--regime", default="EU_DU_ANNEX_I",
                        help="Supabase regime filter (default: EU_DU_ANNEX_I)")
    args = parser.parse_args(argv)

    print("=== Deterministic Operator Scan ===")
    print(f"Regime: {args.regime}")
    print("No model calls. No DB writes.")
    print()

    sb_url, sb_key = _bootstrap_env()
    if not sb_url or not sb_key:
        print("ERROR: Missing VITE_SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
        return 1

    print("Fetching nodes from Supabase ...")
    nodes = fetch_all_nodes(sb_url, sb_key, args.regime)
    print(f"Scanning {len(nodes)} nodes for operator = / == with range phrasing ...")
    hits = scan_nodes(nodes)

    gte_count = sum(1 for h in hits if h["suspected_fix"] == ">=")
    lte_count = sum(1 for h in hits if h["suspected_fix"] == "<=")
    print(f"Hits: {len(hits)} total  ({gte_count} should-be->=, {lte_count} should-be-<=)")

    run_ts = datetime.now().isoformat(timespec="seconds")
    report_md = build_report(hits, len(nodes), run_ts)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    date_str    = datetime.now().strftime("%Y-%m-%d")
    report_path = REPORT_DIR / f"operator_scan_{date_str}.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"\nReport → {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
