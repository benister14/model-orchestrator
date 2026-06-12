"""Cross-provider judge.  [implemented in P2]

Scores worker output on a rubric; escalates to Claude below the confidence floor.
Judge is always a different provider than the author/adversary (no in-group bias).
"""
from __future__ import annotations


def judge(output: dict, task: dict) -> dict:
    raise NotImplementedError("judge lands in P2")
