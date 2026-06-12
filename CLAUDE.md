# CLAUDE.md — model-orchestrator

You are building a Claude-orchestrated, cost-aware, adversarial multi-model router.
Read this file first every session. Full architecture and decisions are in **PLAN.md**.
Current config (roster, lanes, thresholds) is in **config.yaml**.

## What is built (P0 — complete)

- `orchestrator/config.py` — load + validate config; auto-fire retired/EOL models
- `orchestrator/secrets.py` — .env loader + key-presence report
- `orchestrator/runlog.py` — JSONL logging to `~/.orchestrator/logs/`
- `orchestrator/cli.py` — `orchestrate` CLI; `status` subcommand works end-to-end
- Stubs in place for: `prefilter.py`, `router.py`, `gate.py`, `judge.py`, `debate.py`, `roster.py`
- `tests/test_config.py` — 3 passing tests
- Exit verified: `orchestrate status` loads config, reports keys, logs a line

## What to build next — phases in order

Work one phase at a time. After each phase: run tests, run `orchestrate status`,
confirm the exit criterion below, then stop and report what was done.

### P1 — Lean core
Implement (replacing the stubs):

**`orchestrator/prefilter.py`**
- Call `gemini-2.5-flash-lite` (open) or `mistral-small` (if sensitive flag already set)
- Tag the task: `type` (code/reasoning/batch/long_context/architecture),
  `complexity` (1–5), `risk` (low/medium/high)
- Set `sensitive: true` if the task description contains PII signals or the caller
  passes `sensitive=True`
- Return a compressed task dict (strip junk, reduce tokens)

**`orchestrator/router.py`**
- `route(task, cfg) -> (model_name, endpoint)` using the role resolution in config.yaml
- Sensitive tasks → trusted lane only (raise if the resolved model is outside it)
- Routing rules (from PLAN.md):
  - type=architecture or risk=high → Claude Sonnet 4.6
  - context_tokens > 100K → long_context lane
  - requires_cot or complexity >= 4 → reasoner lane
  - volume > 100 and complexity <= 2 → worker (cheapest)
  - default → worker

**`orchestrator/adapters/`** (new directory)
- `base.py` — `Adapter` abstract class: `complete(prompt, model, **kwargs) -> str`
- `anthropic.py` — wraps `anthropic` SDK; uses `ANTHROPIC_API_KEY`
- `deepseek.py` — OpenAI-compatible endpoint (`api.deepseek.com`); uses `DEEPSEEK_API_KEY`
- Wire adapters into the router so a real call can be made

**`orchestrator/gate.py`**
- Rule-based only — no LLM
- PII scan: flag common patterns (email regex, phone, French SIRET/SIREN formats,
  names in `client_*` fields)
- Cost check: read cumulative session spend from the run log;
  if it would exceed `thresholds.cost_ceiling_per_session_usd`, raise `CostCeilingError`
- Return `{output, flags: [], cost_usd: float, passed: bool}`

**`orchestrator/cli.py`** — add `route` subcommand:
```
orchestrate route "describe your task here" [--sensitive] [--dry-run]
```
Dry-run: shows which model would be selected + estimated cost, no API call.

**`tests/test_p1.py`** — cover:
- Router resolves sensitive tasks to trusted-lane models only
- Router raises on untrusted model for sensitive task
- Gate catches a cost ceiling breach
- Gate flags a string containing an email address
- Dry-run route command prints model + cost without calling an API

Exit criterion: `orchestrate route "scaffold a CRUD API" --dry-run` prints the
selected model, lane, and estimated cost. A live call with a real key routes,
the gate scans, and cost is logged. Run for ~a week on real tasks.

---

### P2 — Judge
**`orchestrator/judge.py`**
- Call `gemini-3-flash` (always a different provider from the worker/author)
- Evaluate output on a rubric: correctness, completeness, logical consistency, risk
- Return `{score: float, passed: bool, reasoning: str}`
- If `score < thresholds.escalate_to_claude_when_judge_below`: escalate to
  Claude Sonnet 4.6 as meta-judge (log the escalation)
- Add `google.py` adapter to `orchestrator/adapters/`

Exit criterion: a task runs through worker → judge; low-confidence output escalates
to Claude; both paths logged with scores.

---

### P3 — More lanes
- Add `mistral.py` adapter to `orchestrator/adapters/`
- Add `openai.py` adapter (EU residency endpoint for sensitive; standard otherwise)
- Wire long-context and reasoning lanes in the router
- Add `orchestrate route` flag `--context-tokens N` to force the long-context path

Exit criterion: a task with `--context-tokens 150000` routes to the long-context model.

---

### P4 — Observability
**`orchestrator/cli.py`** — add `report` subcommand:
```
orchestrate report [--days 7]
```
- Read JSONL logs from `~/.orchestrator/logs/`
- Print: cost by task type, judge pass rate, escalation rate, cheapest/priciest task
- Flag any task type with escalation rate > 30% (candidate for debate)

Exit criterion: `orchestrate report --days 7` produces a readable summary.

---

### P5 — Adversarial debate (conditional — build only if P4 logs justify it)
**`orchestrator/debate.py`**
- Red team selection (from config.yaml roles):
  - routine → `deepseek-v4-pro`
  - high-stakes code → `gpt-5.5-codex`
  - code + sensitive → `gpt-5.5-codex` via OpenAI EU residency endpoint
  - reasoning + sensitive → `mistral-magistral-medium`
- D3 budgeted stopping: max 2 rounds, stop when score gap < 0.10
- Skip entirely when complexity <= 2 or risk = low
- Return `{output, debate_log, rounds: int, converged: bool}`

Exit criterion: a complexity-4 code task runs max 2 debate rounds and converges;
a complexity-1 task skips debate entirely.

---

### P6 — MCP wrap (optional)
Expose `route`, `gate`, `judge` as MCP tools so Claude Code can call them natively
without going through the CLI. Only build this once P1–P4 are stable.

---

### P7 — Adaptive roster (built on P4 logs)
**`orchestrator/roster.py`** — replace stub with:
- `scorecard(cfg, days=30) -> dict` — reads logs, computes cost-per-accepted-output
  per model, plus failure rate, latency, escalation rate
- `propose_changes(scorecard) -> list[dict]` — surfaces candidates for
  promote / demote / fire based on thresholds; never auto-applies
- `shadow_test(new_model, task_type, n=20)` — runs a new model in trial mode
  alongside the incumbent; outputs a comparison

Hard rules (never skip):
- `propose_changes` only returns a list — it never writes config.yaml
- Any change to the trusted lane requires an explicit `--approve-trusted` flag
  from the human operator
- Retired and EOL-fired models are logged, not deleted from config.yaml
  (audit trail)

Exit criterion: `orchestrate report --days 30 --scorecard` shows per-model metrics
and any proposed changes, clearly labelled as proposals pending approval.

---

## Safety invariants — never violate these

1. **Sensitive flag → trusted lane only.** If a task is sensitive and the resolved
   model's provider is not in `lanes.trusted`, raise `LaneViolationError` before
   any API call.
2. **No OAuth token in the router.** The Max subscription token is for interactive
   Claude Code use only. Automated Claude calls use `ANTHROPIC_API_KEY`.
3. **Judge is always a third provider.** Never let the author/adversary provider
   also be the judge. Enforced at runtime: if the judge model's provider matches
   the worker's provider, raise.
4. **Trusted-lane changes are human-gated.** No code path auto-promotes a model
   into the trusted lane. The adaptive roster proposes only.
5. **Logs stay off the repo tree.** Write only to `~/.orchestrator/logs/` (or
   `ORCHESTRATOR_LOG_DIR`). Never log to a path inside the repo.

## Code conventions

- Python 3.10+, type hints throughout
- No external dependencies beyond `PyYAML` and provider SDKs
- Each adapter has the same interface (`base.Adapter`); the router never imports
  a provider SDK directly
- All new subcommands added to `orchestrator/cli.py` following the pattern there
- New tests go in `tests/` following `test_config.py` as the template
- After every phase: `pip install -e . && pytest -q`

## What the human handles (do not attempt)

- Adding API keys to `.env`
- Git operations (init, push to private repo)
- Compliance / trusted-lane sign-offs
- Approving adaptive roster proposals
- Decisions marked "human gate" in PLAN.md

## Global snippet (other projects)

Once P1 is working, paste the contents of `install/global-snippet.md` into
`~/.claude/CLAUDE.md` (Windows: `C:\Users\<you>\.claude\CLAUDE.md`).
That is the only thing needed to make other projects reach for the orchestrator.
Do not paste the full CLAUDE.md briefing globally — only the short snippet.
