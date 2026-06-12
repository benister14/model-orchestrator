# model-orchestrator

A Claude-orchestrated, cost-aware, adversarial multi-model router.

Claude Code plans and reviews; a routing layer dispatches the bulk work to the
cheapest capable model per task, a cross-provider judge validates output, and a
budgeted adversarial debate challenges high-stakes work before anything ships.
Sensitive (client / personal) data is locked to a trusted, EU-aware lane.

See **PLAN.md** for the full architecture, locked decisions, roster, costs, and
the phased build (P0–P7).

## Status: P0 — skeleton

What works now: config loading + validation, secrets discovery, run-logging, and
the `orchestrate` CLI. Model calls (prefilter, router, gate, judge, debate) are
stubs that land in later phases.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env          # fill in the API keys you have
orchestrate status            # shows lanes, key presence, active roster; writes a log line
```

`orchestrate status` is the P0 exit check: it loads the config, reports which
provider keys are present, prints the live roster (retired / past-EOL models are
auto-dropped), and writes a JSONL line to `~/.orchestrator/logs/`.

## Auth model

- **Orchestrator:** Claude Code on your **Max seat** (interactive, your own use).
  Do **not** wire a Max OAuth token into this router — that violates Anthropic's
  consumer terms. Automated Claude calls use `ANTHROPIC_API_KEY`.
- **Fan-out (workers, judge, adversary):** per-token **API keys**, one per provider.

## Layout

```
config.yaml            roster, lanes, thresholds, adaptive fields
orchestrator/
  config.py            load + validate; auto-fire retired/EOL models
  secrets.py           .env loader + key-presence report (no values)
  runlog.py            JSONL logging to ~/.orchestrator/logs
  prefilter.py  router.py  gate.py     [P1]
  judge.py             [P2]
  debate.py            [P5]
  roster.py            [P7] scorecards / promote-demote-fire
  cli.py               `orchestrate`
tests/
```
