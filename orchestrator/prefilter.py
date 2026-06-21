# orchestrator/prefilter.py
"""Stage 0 — pre-filter.

Tags an incoming task (type, complexity 1-5, risk, sensitive) using keyword
heuristics. P1 is heuristic-only; LLM-backed tagging (gemini-2.5-flash-lite /
mistral-small) will be wired in P2 once the Google adapter is available.
"""
from __future__ import annotations

import re

_ARCHITECTURE = {"architecture", "architect", "microservice", "distributed",
                 "infrastructure", "scalable", "schema", "system design"}
_CODE = {"code", "function", "class", "api", "endpoint", "implement", "debug",
         "refactor", "scaffold", "crud", "test", "unit", "lint", "build"}
_BATCH = {"batch", "bulk", "normalize", "extract", "transform", "classify",
          "tag", "label", "process"}
_REASONING = {"explain", "analyze", "why", "reason", "compare", "evaluate",
              "assess", "strategy", "decision", "think"}

_HIGH_COMPLEXITY = {"architecture", "distributed", "scalable", "microservice",
                    "security", "adversarial", "concurrent", "streaming"}
_HIGH_RISK = {"security", "auth", "authentication", "payment", "financial",
              "pii", "gdpr", "sensitive", "banking", "compliance"}
_MEDIUM_RISK = {"production", "deploy", "database", "migration", "delete",
                "remove", "drop"}

_PII_SIGNAL = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'  # email
    r'|\b(?:\+33\s?|0)[1-9](?:[\s.\-]?\d{2}){4}\b'        # French phone
    r'|\b\d{14}\b'                                          # SIRET (14 digits)
    r'|\b\d{9}\b',                                          # SIREN (9 digits)
    re.IGNORECASE,
)


def prefilter(task: dict, sensitive: bool = False) -> dict:
    """
    Return a new task dict with added keys: type, complexity, risk, sensitive.

    The description is passed through UNCHANGED — it is the actual task the worker
    must answer. (A prior version truncated it to 500 chars "to reduce tokens",
    which silently fed the worker a fragment and made every real prompt fail; the
    tagging below already reads the full text, so truncation only ever broke the task.)
    """
    description = task.get("description", "")
    desc_lower = description.lower()
    words = set(re.findall(r'\w+', desc_lower))

    # Sensitivity: caller flag OR PII pattern in description
    task_sensitive = sensitive or bool(_PII_SIGNAL.search(description))

    # Type: first matching keyword set wins
    if words & _ARCHITECTURE:
        task_type = "architecture"
    elif words & _BATCH:
        task_type = "batch"
    elif words & _REASONING:
        task_type = "reasoning"
    elif words & _CODE:
        task_type = "code"
    else:
        task_type = "code"  # safe default

    # Complexity 1-5: keyword-driven then word-count fallback
    if words & _HIGH_COMPLEXITY:
        complexity = 4
    elif len(description.split()) > 50:
        complexity = 3
    elif len(description.split()) > 20:
        complexity = 2
    else:
        complexity = 1

    # Risk: sensitive auto-upgrades to high
    if task_sensitive or (words & _HIGH_RISK):
        risk = "high"
    elif words & _MEDIUM_RISK:
        risk = "medium"
    else:
        risk = "low"

    return {
        **task,
        "description": description,
        "type": task_type,
        "complexity": complexity,
        "risk": risk,
        "sensitive": task_sensitive,
    }
