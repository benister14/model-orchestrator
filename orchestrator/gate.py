"""Rule-based gate.  [implemented in P1]

PII / risk scan + per-session cost ceiling. No LLM required.
"""
from __future__ import annotations


def gate(output: dict, task: dict) -> dict:
    raise NotImplementedError("gate lands in P1 (lean core)")
