"""Stage 1 — routing.  [implemented in P1]

route(task) -> resolves a role+lane to a concrete model from the active roster.
Sensitive tasks are constrained to the trusted lane (enforced at config load).
"""
from __future__ import annotations


def route(task: dict) -> str:
    raise NotImplementedError("router lands in P1 (lean core)")
