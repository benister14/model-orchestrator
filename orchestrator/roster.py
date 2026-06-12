"""Adaptive roster helpers.

`effective_models` (live now, in config.py) auto-fires retired / past-EOL models.
Scorecards and promote/demote/fire proposals land in P7 and always require human
approval — trusted-lane changes are hard-gated.
"""
from __future__ import annotations

from .config import effective_models  # re-export for convenience  # noqa: F401


def scorecard(*args, **kwargs):
    raise NotImplementedError("roster scorecards land in P7 (adaptive roster)")
