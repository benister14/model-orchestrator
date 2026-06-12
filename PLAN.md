# PLAN.md — Adversarial Multi-Model Orchestration

A configurable orchestration layer where **Claude Code orchestrates** a fleet of
cheaper models: workers execute, a cross-provider judge validates, and a budgeted
adversarial debate challenges high-stakes output before it ships. The expensive
machinery is **opt-in per task**, not always-on.

## Design principles

1. **Role separation** — the orchestrator plans and reviews; it never does bulk grind.
2. **Cheap-by-default routing** — expensive models only touch what needs them.
3. **Cross-provider checking** — author, adversary, and judge come from different
   labs so there is no in-group bias anywhere in the loop.
4. **Configurable, not mandatory** — debate and extra layers fire only where stakes
   (and, later, the logs) justify them.
5. **Sensitivity is a routing input** — client/personal data is flagged at intake
   and locked to the trusted lane.
6. **The roster is a managed portfolio** — models earn their slot and can lose it.

## Locked decisions

| # | Decision | Locked value |
|---|----------|--------------|
| 1 | Language | Python |
| 2 | Roster | see below |
| 3 | Trusted lane | Mistral + Claude + Gemini + OpenAI |
| 4 | Thresholds | defaults below (tune from logs) |
| 5 | Repo | private, `model-orchestrator` |

Orchestrator runs on the user's **Claude Code Max seat** (interactive). Fan-out
models use **per-token API keys**. The Max OAuth token is never wired into the
router (Anthropic consumer-terms tripwire / ban risk).

## Lanes

- **Trusted** (sensitive ok): Mistral (EU-native), Claude, Gemini, OpenAI.
  OpenAI sensitive calls use the **EU data-residency endpoint** with a
  residency-eligible model (gpt-5.5-codex).
- **Open** (non-sensitive only): adds DeepSeek.
- DeepSeek and any China-hosted model never touch the trusted lane on the direct
  API. Adding any provider to the trusted lane requires human compliance sign-off.

## Roster (USD per 1M tokens, in → out; ~June 2026, confirm before relying)

| Role | Model | In → Out | Lane |
|------|-------|----------|------|
| Orchestrator (default) | Claude Sonnet 4.6 | 3.00 → 15.00 | Trusted |
| Orchestrator (hard / meta-judge) | Claude Opus 4.8 | 5.00 → 25.00 | Trusted |
| Pre-filter / router | Gemini 2.5 Flash-Lite | 0.10 → 0.40 | Open |
| Worker — bulk | DeepSeek V4 Flash | 0.14 → 0.28 | Open only |
| Worker — sensitive | Mistral Small | ~0.15 → 0.60 | Trusted (EU) |
| Long-context | Gemini 3 Flash (1M) | 0.50 → 3.00 | Open |
| Long-context — sensitive | Claude Sonnet 4.6 (1M) | 3.00 → 15.00 | Trusted |
| Reasoner — routine red team | DeepSeek V4 Pro | 1.74 → 3.48 | Open only |
| Reasoner — sensitive | Mistral Magistral Medium | 2.00 → 5.00 | Trusted (EU) |
| Red team — high-stakes code | GPT-5.5-Codex | 1.75 → 14.00 | Open* |
| Judge (always third provider) | Gemini 3 Flash | 0.50 → 3.00 | Open* |

\* If the task is sensitive, the judge and code adversary must also sit in the
trusted lane (Gemini cleared; OpenAI via EU residency endpoint).

**Panel:** Claude (Anthropic) authors → tiered red team (DeepSeek routine /
Codex high-stakes code / Magistral for sensitive reasoning) → Gemini judges.
Never let one provider be both adversary and judge.

## Thresholds (defaults — tune from logs after the P1 week)

- Skip cheap path **and** debate when complexity ≤ 2 or risk = low.
- Debate: max 2 rounds; stop when score gap < 0.10 (D3 budgeted stopping).
- Escalate to Claude when judge confidence < 0.75.
- Sensitive flag → trusted lane only.
- Per-session cost ceiling: $2 placeholder.

## Realistic costs (per-token fan-out; orchestrator on Max)

| Example | Path | ~Cost |
|---------|------|-------|
| Tag/normalize 50 leads (batch) | prefilter → worker | ~$0.01 |
| Scaffold endpoint + tests (code) | orchestrate → worker → judge | ~$0.03 |
| Security-sensitive code + Codex debate (2 rounds) | full pipeline | ~$0.15–0.25 |
| Extract from a 200-page PDF | long-ctx → judge | ~$0.05 open / ~$1 sensitive |

~500 mixed tasks/month ≈ **$5–15/month** in fan-out tokens, on top of the Max seat.
Cost drivers: the Codex debate (high-stakes code only) and sensitive long-context.

## Phased build

- **P0 — Skeleton (this).** Repo, config, secrets, logging, `orchestrate` CLI.
  *Exit:* `orchestrate status` runs, loads config + keys, writes a log line.
- **P1 — Lean core.** Prefilter (tag + sensitive flag), router, Claude + one
  worker adapter, PII/cost gate. *Exit:* a real task routes, is scanned, logs
  cost — then run on real work for ~a week.
- **P2 — Judge.** Cross-provider review + escalate-to-Claude on low confidence.
- **P3 — More lanes.** Long-context + reasoning adapters, by trigger.
- **P4 — Observability.** `orchestrate report`: cost + judge-rejection by task type.
- **P5 — Debate (conditional).** Only on task types the logs show need it.
- **P6 — MCP wrap (optional).** Expose the router as native Claude Code tools.
- **P7 — Adaptive roster.** Self-tuning; see below.

Each phase ships and is measured before the next. After the P1 week the deciding
numbers are cost per task type, judge rejection rate, and escalation rate — if the
cheap lanes are reliable, P3–P5 earn their place; if not, we stop smaller.

## P7 — Adaptive roster (learn for improvements)

The roster is not static. Each model carries a `status`
(`trial → active → demoted → retired`) and an `eol_date`, and moves on evidence —
never silently.

- **Trial (shadow mode):** a new/cheaper model runs alongside the incumbent and is
  scored on the same tasks, but its output isn't shipped until it proves out.
- **Promote:** consistently cheap + reliable → a more central/default role.
- **Demote:** rejection rate or cost-per-accepted-output worsens (or a better
  option appears) → keep it, but only for narrower / lower-stakes use.
- **Fire:** deprecated, EOL-dated, compliance-revoked, or persistently
  underperforming → out. `eol_date` auto-fires forced retirements on schedule.

**Two signals.** (1) *Internal* — the P4 logs; deciding metric is
**cost per accepted output** (token cost to produce something the judge passed),
plus guardrails: failure rate, latency, escalation rate. (2) *External* — a
periodic re-check of market prices / capabilities / deprecations.

**Hard safety rule.** The loop *proposes*; the human *approves*. Anything touching
the trusted lane is always a human gate — a model can never be auto-promoted into
the sensitive lane without sign-off, so the learning loop can't quietly route
client data somewhere it shouldn't go.

Built late (on the P4 logs) because a self-tuning roster is meaningless until
there's real data to learn from. The `status` and `eol_date` fields exist from P0
so nothing needs reshaping later.
