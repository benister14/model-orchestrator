"""Model Orchestrator — Claude-orchestrated, cost-aware, adversarial multi-model router.

P0 ships the skeleton: config loading, secrets discovery, run-logging, and the
`orchestrate` CLI. Model calls (prefilter, router, gate, judge, debate) are
stubbed and land in later phases — see PLAN.md.
"""

__version__ = "0.0.1"  # P0 skeleton
