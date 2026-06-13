#!/usr/bin/env python3
"""
Ensemble criteria extraction harness — variance detection only.

Reads EU Dual-Use Annex I nodes from Supabase, runs K=3 extractions
across deepseek-v4-flash (x2) and mistral-small (x1), computes per-field
agreement, and outputs a divergence-ranked markdown report.

NO DB WRITES. Read-only against Supabase. Compare ensemble against the
stored criteria_json to surface stored-vs-consensus mismatches.

Usage:
    python scripts/ensemble_extractor.py [--nodes 2B001,1C010] [--max 30]
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

# ---------------------------------------------------------------------------
# Ensemble configuration
# ---------------------------------------------------------------------------
PRIORITY_NODES = ["2B001", "1C010", "6A004", "6A005"]
TARGET_CATEGORIES = {"1", "2", "3", "5", "6", "7", "9"}
DEFAULT_MAX_NODES = 30

# K=3: mistral-small (x2) + claude-haiku (x1)
# DeepSeek-v4-flash content-filters "Dual-Use / export control" prompts.
# Claude Haiku provides provider diversity (Anthropic vs Mistral architectures),
# handles very large nodes (supports 8192 output tokens vs mistral's 4096 cap),
# and costs ~$0.001 per node — negligible at this batch size.
ENSEMBLE_RUNS: list[tuple[str, str]] = [
    ("mistral",    "mistral-small"),
    ("mistral",    "mistral-small"),
    ("anthropic",  "claude-haiku-4-5-20251001"),
]

# For nodes with many stored thresholds mistral-small truncates its JSON output.
# Switch to haiku×3 when stored threshold count exceeds this.
# Empirically: mistral-small truncates at ~13k chars output, hitting nodes with >=20 thresholds.
_LARGE_NODE_THRESHOLD = 20
_LARGE_NODE_RUNS: list[tuple[str, str]] = [
    ("anthropic", "claude-haiku-4-5-20251001"),
    ("anthropic", "claude-haiku-4-5-20251001"),
    ("anthropic", "claude-haiku-4-5-20251001"),
]

# Haiku supports 8192 output tokens; mistral-small caps at 4096.
_MAX_TOKENS_BY_PROVIDER: dict[str, int] = {
    "mistral":   4096,
    "anthropic": 8192,
    "deepseek":  4096,
}
K = len(ENSEMBLE_RUNS)

# ---------------------------------------------------------------------------
# Credentials loader
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
    """Load Supabase creds and inject model keys into os.environ. Returns (url, key)."""
    ec_env = _load_env_file(EXPORT_COMPASS_DIR / ".env.local")
    orch_env = _load_env_file(MODEL_ORCH_DIR / ".env")

    url = ec_env.get("VITE_SUPABASE_URL", "").rstrip("/")
    key = ec_env.get("SUPABASE_SERVICE_ROLE_KEY", "")

    for var in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY"):
        if var in orch_env and var not in os.environ:
            os.environ[var] = orch_env[var]

    return url, key


# ---------------------------------------------------------------------------
# Supabase REST
# ---------------------------------------------------------------------------
def _sb_get(url: str, key: str, table: str, params: dict) -> list[dict]:
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}/rest/v1/{table}?{qs}"
    req = urllib.request.Request(
        full_url,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Range": "0-299",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"Supabase {e.code}: {body[:300]}") from e


_COLS = "code,title,description,source_text_html,criteria_json,curation_status"


def fetch_target_nodes(
    sb_url: str,
    sb_key: str,
    override_codes: list[str] | None,
    max_nodes: int,
) -> list[dict]:
    """Return up to max_nodes criteria-rich nodes, priority codes first."""

    priority_codes = override_codes if override_codes else PRIORITY_NODES
    seen: dict[str, dict] = {}

    # 1. Fetch priority nodes
    code_list = ",".join(priority_codes)
    rows = _sb_get(sb_url, sb_key, "classification_nodes", {
        "select": _COLS,
        "regime": "eq.EU_DU_ANNEX_I",
        "code": f"in.({code_list})",
    })
    for row in rows:
        thresh = (row.get("criteria_json") or {}).get("thresholds") or []
        if thresh:
            seen[row["code"]] = row

    if not override_codes and len(seen) < max_nodes:
        # 2. Fill from target categories (non-skeleton, criteria-rich nodes)
        cat_list = ",".join(sorted(TARGET_CATEGORIES))
        rows = _sb_get(sb_url, sb_key, "classification_nodes", {
            "select": _COLS,
            "regime": "eq.EU_DU_ANNEX_I",
            "category": f"in.({cat_list})",
            "curation_status": "in.(criteria_drafted,reviewed,verified)",
            "order": "code",
        })
        for row in rows:
            if row["code"] in seen:
                continue
            thresh = (row.get("criteria_json") or {}).get("thresholds") or []
            if thresh:
                seen[row["code"]] = row
            if len(seen) >= max_nodes:
                break

    # Sort: priority codes first, then by threshold count descending
    result = []
    for code in priority_codes:
        if code in seen:
            result.append(seen.pop(code))
    result.extend(
        sorted(seen.values(),
               key=lambda r: len((r.get("criteria_json") or {}).get("thresholds") or []),
               reverse=True)
    )
    return result[:max_nodes]


# ---------------------------------------------------------------------------
# Extraction prompt (mirrors ai-prefill.ts system prompt)
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are an EU export control classification specialist. Extract structured criteria from EU Dual-Use Regulation Annex I text.

Return ONLY valid JSON — no markdown, no code fences, no comments, no explanation.

Required structure (no id fields):
{
  "thresholds": [
    {
      "parameter": "snake_case_key_with_unit_suffix",
      "parameter_label": "Human Readable Label",
      "operator": ">= | > | <= | < | == | between",
      "value": 0,
      "unit": "string or null",
      "applies_when": "condition string or null",
      "source_quote": "VERBATIM phrase from source text"
    }
  ],
  "conditions": [
    {
      "text": "plain English statement",
      "type": "inclusion | exclusion | note",
      "source_quote": "VERBATIM phrase from source text"
    }
  ],
  "figures_of_merit": ["parameter_key"],
  "cross_references": [],
  "catch_all_indicators": []
}

RULES:
PARAMETER KEYS: lowercase snake_case, append unit abbreviation as suffix.
  Examples: thrust_kn, wavelength_nm, bandwidth_mhz, temperature_k, power_kw, range_km.
OPERATOR: >= "at least/not less than/or more"; > "exceeding/greater than/more than";
  <= "not exceeding/equal to or less than/or less"; < "less than/below/under";
  == "exactly/equal to" (rare); between "between X and Y" → value MUST be [min, max] array.
SOURCE_QUOTE: VERBATIM from source text. Never paraphrase. Required for every item.
FIGURES_OF_MERIT: 1-5 most important parameter keys for classification.
STRUCTURAL NODES: if header/section/"Not used", return all empty arrays.
CONDITIONS types: inclusion=explicitly controlled; exclusion=explicitly NOT controlled;
  note=definitions, measurement methods, technical clarifications.
CATCH_ALL: only explicit qualitative dual-use red-flag phrases verbatim in text. Leave [] if none.\
"""


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    for ent, ch in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&#160;", " ")]:
        text = text.replace(ent, ch)
    return re.sub(r"\s+", " ", text).strip()


_SOURCE_TEXT_LIMIT = 8_000  # chars; keeps total prompt ≤ ~3 000 tokens


def _user_prompt(node: dict) -> str:
    source = _strip_html(node.get("source_text_html") or node.get("description") or "")
    if len(source) > _SOURCE_TEXT_LIMIT:
        source = source[:_SOURCE_TEXT_LIMIT] + "\n[... truncated ...]"
    return (
        f"Extract criteria_json for EU Dual-Use Annex I entry:\n\n"
        f"Code: {node['code']}\n"
        f"Title: {node['title']}\n\n"
        f"Source text:\n{source}"
    )


# ---------------------------------------------------------------------------
# Single extraction run
# ---------------------------------------------------------------------------
def _extract_once(node: dict, adapter, model: str, provider: str) -> dict:
    """Run extraction once. Returns parsed dict; sets _error key on failure."""
    raw: str = ""
    try:
        raw = adapter.complete(
            prompt=_user_prompt(node),
            model=model,
            max_tokens=_MAX_TOKENS_BY_PROVIDER.get(provider, 4096),
            system=_SYSTEM_PROMPT,
        ) or ""
        if not raw.strip():
            return {"_error": "empty response (content filter?)", "_raw_snippet": "",
                    "thresholds": [], "conditions": [], "figures_of_merit": []}
        clean = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip())
        clean = re.sub(r"\n?```\s*$", "", clean)
        parsed = json.loads(clean)
        for key in ("thresholds", "conditions", "figures_of_merit",
                    "cross_references", "catch_all_indicators"):
            parsed.setdefault(key, [])
        return parsed
    except json.JSONDecodeError as exc:
        return {"_error": f"JSON parse: {exc}", "_raw_snippet": raw[:300],
                "thresholds": [], "conditions": [], "figures_of_merit": []}
    except Exception as exc:
        return {"_error": str(exc)[:200], "_raw_snippet": raw[:300],
                "thresholds": [], "conditions": [], "figures_of_merit": []}


# ---------------------------------------------------------------------------
# Agreement computation
# ---------------------------------------------------------------------------
_UNIT_ALIASES: dict[str, str] = {
    # Greek mu variants (U+03BC vs U+00B5) and common symbol spellings
    "μ": "u",   # μ → u
    "µ": "u",   # µ → u
    "²": "2",   # superscript 2 → 2
    "³": "3",   # superscript 3 → 3
    "°": "deg", # ° → deg
    "Ω": "ohm", # Ω → ohm
    "λ": "lambda",  # λ → lambda
}


def _norm_unit(s: str) -> str:
    """Normalize unicode variants in unit strings to ASCII-safe equivalents."""
    for ch, repl in _UNIT_ALIASES.items():
        s = s.replace(ch, repl)
    return s.strip().lower()


def _norm_val(v: Any) -> str:
    if isinstance(v, list):
        parts = [str(round(float(x), 8)) if isinstance(x, (int, float)) else str(x) for x in v]
        return "[" + ",".join(parts) + "]"
    if isinstance(v, (int, float)):
        return str(round(float(v), 8))
    return str(v or "").strip().lower()


def _param_key(t: dict) -> str:
    return (t.get("parameter") or "").lower().strip()


def compute_agreement(
    code: str,
    runs: list[dict],
    stored_cj: dict,
    node_runs: list[tuple[str, str]] | None = None,
) -> tuple[float, list[dict]]:
    """
    Per-threshold-parameter agreement across K runs.
    Scope gates (applies_when parent-level conditions) are included in extraction
    but EXCLUDED from agreement scoring per task spec — only numeric thresholds scored.
    Returns (node_agreement_score, field_rows).
    """
    # Union of all parameter names extracted across valid runs
    all_params: set[str] = set()
    for run in runs:
        if run.get("_error"):
            continue
        for t in run.get("thresholds") or []:
            p = _param_key(t)
            if p:
                all_params.add(p)

    # Group stored thresholds by param key — preserves ALL duplicates (e.g. 1C010
    # has two dma_tg_k entries for different sub-items). A plain dict comprehension
    # would silently keep only the last, causing false stored-vs-ensemble mismatches.
    from collections import defaultdict
    stored_by_param: dict[str, list[dict]] = defaultdict(list)
    for t in (stored_cj.get("thresholds") or []):
        k = _param_key(t)
        if k:
            stored_by_param[k].append(t)

    field_rows: list[dict] = []
    stable_count = 0

    for param in sorted(all_params):
        # Collect comparable tuple (op, val, unit) from each run
        effective_runs = node_runs if node_runs is not None else ENSEMBLE_RUNS
        run_tuples: list[dict | None] = []
        run_labels: list[str] = []
        for i, (provider, model) in enumerate(effective_runs):
            run = runs[i]
            if run.get("_error"):
                run_tuples.append(None)
                run_labels.append(f"{provider}: ERROR")
                continue
            match = next((t for t in (run.get("thresholds") or []) if _param_key(t) == param), None)
            if match is None:
                run_tuples.append(None)
                run_labels.append(f"{provider}: MISSING")
            else:
                tup = {
                    "op":   match.get("operator", ""),
                    "val":  _norm_val(match.get("value")),
                    "unit": _norm_unit(match.get("unit") or ""),
                }
                run_tuples.append(tup)
                run_labels.append(
                    f"{provider}: op={tup['op']} val={tup['val']} unit={tup['unit'] or '-'}"
                )

        non_null = [t for t in run_tuples if t is not None]
        # Need ≥2 valid runs to make any agreement claim.
        # "insufficient_data" = only 1 valid run (e.g. model output limit hit) — not a
        # disagreement signal, just untestable.  Only "REVIEW" counts as divergent.
        enough_runs = len(non_null) >= 2
        all_agree = (
            enough_runs
            and len({json.dumps(t, sort_keys=True) for t in non_null}) == 1
        )
        if all_agree:
            stable_count += 1

        # Compare ensemble runs against stored value(s).
        # Rules:
        # - A param may have multiple stored entries (different applies_when contexts).
        # - stored_mismatch = True ONLY when EVERY valid run disagrees with EVERY stored
        #   candidate for this param.  A single run confirming any stored candidate is
        #   not a mismatch — cheap models often hallucinate == for "or more" phrasing,
        #   so majority-vote consensus is not reliable enough to override stored.
        stored_candidates = stored_by_param.get(param, [])
        stored_mismatch = False
        stored_label = "not in stored"
        if stored_candidates:
            stored_tups = [
                {
                    "op":   c.get("operator", ""),
                    "val":  _norm_val(c.get("value")),
                    "unit": _norm_unit(c.get("unit") or ""),
                }
                for c in stored_candidates
            ]
            s0 = stored_tups[0]
            stored_label = f"op={s0['op']} val={s0['val']} unit={s0['unit'] or '-'}"
            if len(stored_tups) > 1:
                stored_label += f" (+{len(stored_tups)-1} dup)"
            if non_null:
                any_confirms = any(
                    run_tup == stored_tup
                    for run_tup in non_null
                    for stored_tup in stored_tups
                )
                stored_mismatch = not any_confirms

        field_rows.append({
            "node_code":       code,
            "field":           param,
            "run_values":      run_labels,
            "agreement_score": round(1.0 if all_agree else len(non_null) / K, 3),
            "verdict":         ("stable"             if all_agree
                                else "insufficient_data" if not enough_runs
                                else "REVIEW"),
            "stored_mismatch": stored_mismatch,
            "stored_label":    stored_label,
        })

    node_score = round(stable_count / len(all_params), 3) if all_params else 1.0
    return node_score, field_rows


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def build_report(
    node_results: list[dict],
    run_start: str,
    elapsed_s: float,
    total_calls: int,
) -> str:
    total_nodes   = len(node_results)
    total_fields  = sum(len(r["field_rows"]) for r in node_results)
    divergent     = sum(1 for r in node_results for f in r["field_rows"] if f["verdict"] == "REVIEW")
    insufficient  = sum(1 for r in node_results for f in r["field_rows"] if f["verdict"] == "insufficient_data")
    mismatches    = sum(1 for r in node_results for f in r["field_rows"] if f["stored_mismatch"])

    review_queue = sorted(
        [f for r in node_results for f in r["field_rows"] if f["verdict"] == "REVIEW"],
        key=lambda f: (f["agreement_score"], 0 if f["stored_mismatch"] else 1),
    )[:20]

    # stored mismatches even in insufficient_data nodes are still valuable
    mismatch_queue = [
        f for r in node_results for f in r["field_rows"]
        if f["stored_mismatch"] and f["verdict"] == "insufficient_data"
    ]

    L: list[str] = []

    L.append("# Ensemble Criteria Extraction — Divergence Report")
    L.append("")
    L.append(f"**Run**: {run_start}  |  **Elapsed**: {elapsed_s:.0f}s  |  **API calls**: {total_calls}")
    L.append(f"**Models**: {', '.join(f'{p}/{m}' for p,m in ENSEMBLE_RUNS)}  (K={K})")
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append(f"| metric | value |")
    L.append(f"|--------|-------|")
    L.append(f"| Nodes processed | **{total_nodes}** |")
    L.append(f"| Threshold fields evaluated | **{total_fields}** |")
    div_pct = 100 * divergent / max(total_fields, 1)
    L.append(f"| Divergent fields (REVIEW) | **{divergent}** ({div_pct:.1f}%) |")
    L.append(f"| Insufficient data (model limit, 1 valid run) | **{insufficient}** |")
    L.append(f"| Stored vs consensus mismatches | **{mismatches}** ← likely existing errors |")
    L.append("")
    L.append("> **Hard rule**: consensus sets REVIEW PRIORITY, never truth.")
    L.append("> Stable = lower queue; REVIEW = top of Benjamin's verification queue.")
    L.append("> `insufficient_data` = only 1 valid run (model output limit hit) — not a disagreement signal.")
    L.append("> Stored mismatches are the highest-value signal — probable extraction errors already committed.")
    L.append("")

    # Node summary table, worst first
    L.append("## Node Summary (worst agreement first)")
    L.append("")
    L.append("| node_code | curation_status | thresholds | agreement | divergent | stored_mismatches |")
    L.append("|-----------|-----------------|------------|-----------|-----------|-------------------|")
    for r in sorted(node_results, key=lambda x: x["node_score"]):
        div   = sum(1 for f in r["field_rows"] if f["verdict"] == "REVIEW")
        insuf = sum(1 for f in r["field_rows"] if f["verdict"] == "insufficient_data")
        sm    = sum(1 for f in r["field_rows"] if f["stored_mismatch"])
        sm_s  = f"**{sm} ⚠️**" if sm else "0"
        errs  = f" _(run errors: {len(r['run_errors'])}, {insuf} fields insufficient_data)_" if r["run_errors"] else ""
        L.append(
            f"| {r['code']} | {r['curation_status']} | "
            f"{len(r['field_rows'])} | {r['node_score']:.2f} | {div} | {sm_s} |{errs}"
        )
    L.append("")

    # Top-20 review queue
    L.append("## Top-20 Review Queue")
    L.append("")
    L.append("Fields ranked by divergence severity (stored mismatches surfaced first).")
    L.append("")
    if review_queue:
        L.append("| node_code | field | agreement | verdict | stored_mismatch |")
        L.append("|-----------|-------|-----------|---------|-----------------|")
        for f in review_queue:
            sm_s = "**⚠️ stored≠consensus**" if f["stored_mismatch"] else "—"
            L.append(
                f"| {f['node_code']} | `{f['field']}` | "
                f"{f['agreement_score']:.2f} | {f['verdict']} | {sm_s} |"
            )
    else:
        L.append("_All fields stable across all runs._")
    L.append("")

    # Per-node detail
    L.append("## Per-Node Field Detail")
    L.append("")
    for r in sorted(node_results, key=lambda x: x["node_score"]):
        title_short = r.get("title", "")[:80]
        L.append(f"### {r['code']} — {title_short}")
        L.append(f"**Status**: `{r['curation_status']}` | **Agreement**: {r['node_score']:.2f}")
        if r["run_errors"]:
            L.append("")
            L.append(f"> Run errors: {'; '.join(r['run_errors'])}")
        L.append("")
        if not r["field_rows"]:
            L.append("_No thresholds extracted by any run._")
            L.append("")
            continue
        r1_label = f"run 1 ({ENSEMBLE_RUNS[0][0]})"
        r2_label = f"run 2 ({ENSEMBLE_RUNS[1][0]})"
        r3_label = f"run 3 ({ENSEMBLE_RUNS[2][0]})"
        sep = "|".join(["-" * (len(l) + 2) for l in ["field", r1_label, r2_label, r3_label, "agree", "verdict", "stored"]])
        L.append(f"| field | {r1_label} | {r2_label} | {r3_label} | agree | verdict | stored |")
        L.append(f"|{sep}|")
        for f in sorted(r["field_rows"], key=lambda x: (x["agreement_score"], x["field"])):
            rv = f["run_values"]
            r1 = rv[0].split(": ", 1)[-1] if rv else "—"
            r2 = rv[1].split(": ", 1)[-1] if len(rv) > 1 else "—"
            r3 = rv[2].split(": ", 1)[-1] if len(rv) > 2 else "—"
            sm_s = "⚠️" if f["stored_mismatch"] else "✓" if f["stored_label"] != "not in stored" else "new"
            L.append(
                f"| `{f['field']}` | {r1[:40]} | {r2[:40]} | {r3[:40]} "
                f"| {f['agreement_score']:.2f} | {f['verdict']} | {sm_s} |"
            )
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
    parser = argparse.ArgumentParser(description="Ensemble criteria extraction harness")
    parser.add_argument("--nodes", default=None,
                        help="Comma-separated node codes to target (overrides default priority list)")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_NODES,
                        help=f"Max nodes to process (default {DEFAULT_MAX_NODES})")
    args = parser.parse_args(argv)

    override_codes = [c.strip() for c in args.nodes.split(",")] if args.nodes else None

    print("=== Ensemble Criteria Extraction Harness ===")
    print(f"Models  : {', '.join(f'{p}/{m}' for p,m in ENSEMBLE_RUNS)}  (K={K})")
    print(f"Max nodes: {args.max}")
    print()

    # Bootstrap credentials
    sb_url, sb_key = _bootstrap_env()
    if not sb_url or not sb_key:
        print("ERROR: Missing VITE_SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in export-compass .env.local",
              file=sys.stderr)
        return 1
    needed_keys = {"ANTHROPIC_API_KEY", "MISTRAL_API_KEY"}
    for var in sorted(needed_keys):
        if not os.environ.get(var):
            print(f"ERROR: {var} not set in model-orchestrator .env", file=sys.stderr)
            return 1

    # Load adapters from model-orchestrator package
    sys.path.insert(0, str(MODEL_ORCH_DIR))
    from orchestrator.adapters import get_adapter  # noqa: PLC0415
    adapters = {
        "anthropic": get_adapter("anthropic"),
        "mistral":   get_adapter("mistral"),
    }

    # Fetch nodes
    print("Fetching nodes from Supabase ...", flush=True)
    nodes = fetch_target_nodes(sb_url, sb_key, override_codes, args.max)
    if not nodes:
        print("ERROR: no nodes returned — check Supabase connection and category filter", file=sys.stderr)
        return 1
    print(f"  {len(nodes)} nodes fetched:")
    for n in nodes:
        tc = len((n.get("criteria_json") or {}).get("thresholds") or [])
        print(f"    {n['code']:12}  {n['curation_status']:22}  {tc} stored thresholds")
    print()

    # Run ensemble
    run_start  = datetime.now().isoformat(timespec="seconds")
    t0         = time.time()
    node_results: list[dict] = []
    total_calls = 0

    for idx, node in enumerate(nodes, 1):
        code = node["code"]
        stored_tc = len((node.get("criteria_json") or {}).get("thresholds") or [])
        node_runs = _LARGE_NODE_RUNS if stored_tc > _LARGE_NODE_THRESHOLD else ENSEMBLE_RUNS
        ensemble_label = f"haiku×{len(node_runs)}" if node_runs is _LARGE_NODE_RUNS else f"mistral×2+haiku×1"
        print(f"[{idx}/{len(nodes)}] {code}  ({stored_tc} stored thresholds → {ensemble_label})", flush=True)
        runs: list[dict] = []
        run_errors: list[str] = []

        for run_i, (provider, model) in enumerate(node_runs):
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
        node_score, field_rows = compute_agreement(code, runs, stored_cj, node_runs)

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

    elapsed = time.time() - t0
    print(f"\n{total_calls} API calls  {elapsed:.0f}s elapsed")

    # Write report
    report_md = build_report(node_results, run_start, elapsed, total_calls)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    date_str     = datetime.now().strftime("%Y-%m-%d")
    report_path  = REPORT_DIR / f"ensemble_divergence_{date_str}.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"\nReport → {report_path}")

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
