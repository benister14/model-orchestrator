#!/usr/bin/env python3
"""
Tier 3 corpus fidelity sweep driver.

Processes all EU Dual-Use Annex I threshold-bearing nodes with ≤17 stored
thresholds that were NOT already swept in Tier 1 (33 nodes) or Tier 2 (43 nodes).
Expected scope: ~1,182 nodes.

Methodology: K=3 (mistral-small×2 + haiku×1), same as Tier 1/2.
No large-node upgrade needed (Tier 3 nodes all have ≤17 thresholds → well within
mistral-small's output cap).

Output: eval/reports/tier3_fidelity_sweep_2026-06-15.md
Checkpoint: eval/reports/tier3_checkpoint.json (resumable)

Usage:
    python scripts/tier3_sweep.py --dry-run           # cost estimate only
    python scripts/tier3_sweep.py                     # run all batches
    python scripts/tier3_sweep.py --resume            # continue from checkpoint
    python scripts/tier3_sweep.py --batch-size 300    # nodes per save cycle
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
MODEL_ORCH_DIR = _HERE.parent
EXPORT_COMPASS_DIR = Path(r"C:\Users\benjb\Desktop\export-compass-2cdd0c45")
REPORT_DIR = EXPORT_COMPASS_DIR / "eval" / "reports"
CHECKPOINT_PATH = REPORT_DIR / "tier3_checkpoint.json"
OUTPUT_REPORT = REPORT_DIR / "tier3_fidelity_sweep_2026-06-15.md"

# ---------------------------------------------------------------------------
# Tier 1 and Tier 2 exclusion sets (already processed — do not re-sweep)
# ---------------------------------------------------------------------------
# Tier 1: 33 threshold-bearing nodes from the classification-session hot-set
TIER1_NODES: set[str] = {
    "1B001", "1C002", "1C010", "1C210",
    "2B001", "2B206", "2B350", "2B510", "2E503",
    "3A001.a.14.a.1", "3A001.a.14.a.3", "3A001.a.14.a.4",
    "3A002.h.1.d", "3A002.h.1.e",
    "3A501", "3B501", "3C005",
    "4A003",
    "5A002", "5D001", "5E001",
    "6A002.a.3.f", "6A003", "6A004", "6A006", "6A008", "6A108", "6A203",
    "6C004", "6D003",
    "7A003", "7A103",
    "9E003",
}

# Tier 2: 43 ever-shortlisted nodes (1–2 sessions, non-hot-set)
# Source: ensemble_divergence_2026-06-13.md (43 node headings confirmed)
TIER2_NODES: set[str] = {
    "0A001", "0B006",
    "1B101", "1C001", "1C011", "1C107", "1C107.f", "1C111",
    "1C116", "1C202", "1C216", "1E002",
    "2B002", "2B004", "2B109", "2B116",
    "3A001.a.14.a.2", "3A001.a.14.a.5", "3A001.a.5.a.3",
    "3A002.h.1.a", "3A002.h.1.b", "3A002.h.1.c",
    "3B001", "3C508", "3E003",
    "4A506", "4E001",
    "6A003.b.1.a.1", "6A003.b.1.a.2", "6A004.b", "6A205",
    "6C004.f", "6C005", "6D003.h.1", "6E003",
    "7A001", "7A002",
    "8A002",
    "9A010", "9A105", "9A105.b", "9A106", "9C110",
}

EXCLUDED: set[str] = TIER1_NODES | TIER2_NODES
MAX_THRESHOLDS = 17

# ---------------------------------------------------------------------------
# Ensemble config (mirrors ensemble_extractor.py)
# ---------------------------------------------------------------------------
ENSEMBLE_RUNS: list[tuple[str, str]] = [
    ("mistral",   "mistral-small"),
    ("mistral",   "mistral-small"),
    ("anthropic", "claude-haiku-4-5-20251001"),
]
K = len(ENSEMBLE_RUNS)
_MAX_TOKENS_BY_PROVIDER: dict[str, int] = {
    "mistral":   4096,
    "anthropic": 8192,
}

# Cost per node estimate for dry-run (USD, based on user's ~$3.40 / 1,195 nodes)
_COST_PER_NODE_USD = 0.00285

# ---------------------------------------------------------------------------
# Import shared logic from ensemble_extractor
# ---------------------------------------------------------------------------
sys.path.insert(0, str(_HERE))
from ensemble_extractor import (   # noqa: E402
    _bootstrap_env,
    _extract_once,
    compute_agreement,
    build_report,
)

_COLS = "code,title,description,source_text_html,criteria_json,curation_status"


# ---------------------------------------------------------------------------
# Paginated Supabase REST fetch
# ---------------------------------------------------------------------------
def _sb_get_page(url: str, key: str, table: str, params: dict, range_hdr: str) -> list[dict]:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{url}/rest/v1/{table}?{qs}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Range": range_hdr,
            "Prefer": "count=none",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"Supabase {e.code}: {body[:300]}") from e


def fetch_tier3_nodes(sb_url: str, sb_key: str) -> list[dict]:
    """
    Return all EU_DU_ANNEX_I threshold-bearing nodes with 1–17 stored thresholds,
    excluding Tier 1 and Tier 2 nodes.
    """
    params = {
        "select": _COLS,
        "regime": "eq.EU_DU_ANNEX_I",
        "curation_status": "in.(criteria_drafted,reviewed,verified)",
        "order": "code",
    }
    all_rows: list[dict] = []
    page_size = 299
    offset = 0

    print("Fetching nodes from Supabase (paginated) ...", flush=True)
    while True:
        batch = _sb_get_page(sb_url, sb_key, "classification_nodes", params,
                             f"{offset}-{offset + page_size - 1}")
        all_rows.extend(batch)
        print(f"  fetched {len(all_rows)} so far ...", flush=True)
        if len(batch) < page_size:
            break
        offset += page_size

    # Filter: must have thresholds, within MAX_THRESHOLDS, not already swept
    result: list[dict] = []
    for row in all_rows:
        thresholds = (row.get("criteria_json") or {}).get("thresholds") or []
        tc = len(thresholds)
        if tc < 1 or tc > MAX_THRESHOLDS:
            continue
        if row["code"] in EXCLUDED:
            continue
        result.append(row)

    return result


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------
def _load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    return {"completed_codes": [], "node_results": []}


def _save_checkpoint(completed_codes: list[str], node_results: list[dict]) -> None:
    CHECKPOINT_PATH.write_text(
        json.dumps({"completed_codes": completed_codes, "node_results": node_results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Tier 3 corpus fidelity sweep")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show node count and cost estimate without making API calls")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint (tier3_checkpoint.json)")
    parser.add_argument("--batch-size", type=int, default=250,
                        help="Save checkpoint every N nodes (default 250)")
    args = parser.parse_args(argv)

    print("=== Tier 3 Corpus Fidelity Sweep ===")
    print(f"Models  : {', '.join(f'{p}/{m}' for p, m in ENSEMBLE_RUNS)}  (K={K})")
    print(f"Excluded: {len(TIER1_NODES)} Tier 1 + {len(TIER2_NODES)} Tier 2 = {len(EXCLUDED)} nodes")
    print(f"Max thresholds per node: {MAX_THRESHOLDS}")
    print()

    # Bootstrap credentials
    sb_url, sb_key = _bootstrap_env()
    if not sb_url or not sb_key:
        print("ERROR: Missing Supabase credentials in export-compass .env.local",
              file=sys.stderr)
        return 1

    if not args.dry_run:
        needed_keys = {"ANTHROPIC_API_KEY", "MISTRAL_API_KEY"}
        for var in sorted(needed_keys):
            if not os.environ.get(var):
                print(f"ERROR: {var} not set in model-orchestrator .env", file=sys.stderr)
                return 1

    # Fetch target nodes
    nodes = fetch_tier3_nodes(sb_url, sb_key)
    print(f"\n  Tier 3 target: {len(nodes)} nodes (after exclusions)")

    if args.dry_run:
        estimated_cost = len(nodes) * _COST_PER_NODE_USD
        estimated_calls = len(nodes) * K
        print()
        print("=== DRY-RUN ESTIMATE ===")
        print(f"  Nodes to process : {len(nodes)}")
        print(f"  API calls (K={K}) : {estimated_calls}")
        print(f"  Estimated cost   : ~${estimated_cost:.2f} USD")
        print(f"  Wall-clock (est) : ~{len(nodes) * 1.5 / 3600:.1f}–{len(nodes) * 2.5 / 3600:.1f} h")
        print(f"  Output report    : {OUTPUT_REPORT}")
        print()
        print("Tier 3 node sample (first 10):")
        for n in nodes[:10]:
            tc = len((n.get("criteria_json") or {}).get("thresholds") or [])
            print(f"  {n['code']:25}  {n['curation_status']:22}  {tc} thresholds")
        if len(nodes) > 10:
            print(f"  ... and {len(nodes) - 10} more")
        return 0

    # Load checkpoint if resuming
    checkpoint = _load_checkpoint() if args.resume else {"completed_codes": [], "node_results": []}
    completed = set(checkpoint["completed_codes"])
    node_results: list[dict] = checkpoint["node_results"]

    pending = [n for n in nodes if n["code"] not in completed]
    if completed:
        print(f"\nResuming: {len(completed)} done, {len(pending)} remaining")
    print()

    # Load adapters
    sys.path.insert(0, str(MODEL_ORCH_DIR))
    from orchestrator.adapters import get_adapter  # noqa: PLC0415
    adapters = {
        "anthropic": get_adapter("anthropic"),
        "mistral":   get_adapter("mistral"),
    }

    # Run ensemble
    run_start  = datetime.now().isoformat(timespec="seconds")
    t0         = time.time()
    total_calls = len(completed) * K  # already-completed calls
    total_nodes = len(nodes)

    for idx, node in enumerate(pending, start=len(completed) + 1):
        code = node["code"]
        stored_tc = len((node.get("criteria_json") or {}).get("thresholds") or [])
        print(f"[{idx}/{total_nodes}] {code}  ({stored_tc} stored thresholds → mistral×2+haiku×1)",
              flush=True)

        runs: list[dict] = []
        run_errors: list[str] = []

        for run_i, (provider, model) in enumerate(ENSEMBLE_RUNS):
            print(f"  run {run_i+1}  {provider}/{model} ... ", end="", flush=True)
            result = _extract_once(node, adapters[provider], model, provider)
            total_calls += 1
            if result.get("_error"):
                snippet = result.get("_raw_snippet", "")
                print(f"ERROR — {result['_error'][:80]}")
                if snippet:
                    print(f"         raw: {snippet[:120]!r}")
                run_errors.append(f"run{run_i+1}({provider}): {result['_error'][:80]}")
            else:
                print(f"ok  ({len(result.get('thresholds') or [])} thresholds)")
            runs.append(result)
            time.sleep(0.4)

        stored_cj  = node.get("criteria_json") or {}
        node_score, field_rows = compute_agreement(code, runs, stored_cj)

        div = sum(1 for f in field_rows if f["verdict"] == "REVIEW")
        sm  = sum(1 for f in field_rows if f["stored_mismatch"])
        print(f"  → score={node_score:.2f}  fields={len(field_rows)}  divergent={div}  stored_mismatches={sm}")

        node_results.append({
            "code":            code,
            "title":           node.get("title", ""),
            "curation_status": node.get("curation_status", ""),
            "node_score":      node_score,
            "field_rows":      field_rows,
            "run_errors":      run_errors,
        })
        completed.add(code)

        # Save checkpoint every BATCH_SIZE nodes
        if idx % args.batch_size == 0:
            print(f"\n  --- Checkpoint saved ({idx}/{total_nodes}) ---\n", flush=True)
            _save_checkpoint(list(completed), node_results)

    elapsed = time.time() - t0
    print(f"\n{total_calls} API calls  {elapsed:.0f}s elapsed")

    # Build and write final report
    report_md = build_report(node_results, run_start, elapsed, total_calls)

    # Prepend Tier 3 header
    tier3_header = (
        "# Tier 3 Fidelity Sweep — Export Compass Corpus Quality\n"
        f"**Date**: {datetime.now().strftime('%Y-%m-%d')}  \n"
        "**Tier**: 3 (all threshold-bearing nodes NOT in Tier 1 or 2, ≤17 stored thresholds)  \n"
        f"**Models**: {', '.join(f'{p}/{m}' for p, m in ENSEMBLE_RUNS)}  (K={K})  \n"
        f"**Excluded**: {len(TIER1_NODES)} Tier 1 nodes + {len(TIER2_NODES)} Tier 2 nodes  \n"
        "**Hard rule**: NO DB writes. Consensus = REVIEW PRIORITY, not truth. Benjamin sets truth.\n\n"
        "---\n\n"
    )
    # Strip the default header from build_report and prepend Tier 3 header
    body = report_md.lstrip("# ").split("\n", 1)[1] if report_md.startswith("# ") else report_md
    full_report = tier3_header + "## Ensemble Summary\n\n" + body

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_REPORT.write_text(full_report, encoding="utf-8")
    print(f"\nReport → {OUTPUT_REPORT}")

    # Cleanup checkpoint on success
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("Checkpoint cleaned up.")

    # Console summary
    total_fields  = sum(len(r["field_rows"]) for r in node_results)
    divergent     = sum(1 for r in node_results for f in r["field_rows"] if f["verdict"] == "REVIEW")
    insufficient  = sum(1 for r in node_results for f in r["field_rows"] if f["verdict"] == "insufficient_data")
    mismatches    = sum(1 for r in node_results for f in r["field_rows"] if f["stored_mismatch"])
    print(f"Nodes {len(node_results)}  |  Fields {total_fields}  |  "
          f"Divergent {divergent}  |  Insufficient {insufficient}  |  Stored mismatches {mismatches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
