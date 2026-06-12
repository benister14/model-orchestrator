"""Stage 0 — pre-filter.  [implemented in P1]

Tags an incoming task (type, complexity 1-5, risk) and sets the SENSITIVE flag
that locks routing to the trusted lane. Runs on the cheapest model.
"""
from __future__ import annotations


def prefilter(task: dict) -> dict:
    raise NotImplementedError("prefilter lands in P1 (lean core)")
