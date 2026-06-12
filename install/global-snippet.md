# Model Orchestrator — global snippet
# Paste the block below into ~/.claude/CLAUDE.md (on Windows: C:\Users\<you>\.claude\CLAUDE.md)
# This makes the `orchestrate` CLI available to every project.
# Keep it short — it loads in every session. Full docs live in the orchestrator repo.

# -----------------------------------------------------------------------
# Paste from here:

## Model Orchestrator

An `orchestrate` CLI is installed globally. Use it for tasks where routing to a
cheaper or specialised model makes sense. Do not call provider APIs directly when
the orchestrator can handle it.

### When to reach for it

- Bulk / repetitive work (normalise data, batch transforms, tagging) → `orchestrate route`
- Long documents (>100K tokens) → `orchestrate route --context-tokens N`
- High-stakes code that needs adversarial review → `orchestrate route --debate`
- Validating any output before it ships → `orchestrate judge`
- Checking cost before a large run → `orchestrate route --dry-run`

### How to call it

```bash
# dry-run first — shows model, lane, estimated cost; no API call
orchestrate route "your task description" --dry-run

# live route
orchestrate route "your task description"

# flag sensitive (client / personal data) — locks to trusted lane
orchestrate route "your task description" --sensitive

# judge an existing output
orchestrate judge --input output.txt
```

### Safety rules (never skip)

- `--sensitive` must be set whenever a task involves client names, contact data,
  or anything personal. The flag locks routing to the approved trusted lane.
- Never pass a Claude Code Max OAuth token to the orchestrator. Automated calls
  use `ANTHROPIC_API_KEY` from the orchestrator's `.env`.
- The judge is always a different provider from the worker. Do not override this.
- Trusted-lane changes require human approval — never propose them autonomously.

### Where things live

- Repo + full docs: `C:\Projects\model-orchestrator` (adjust to your actual path)
- Run logs: `%USERPROFILE%\.orchestrator\logs\`
- Keys: `C:\Projects\model-orchestrator\.env` (gitignored, never committed)
- Roster + thresholds: `C:\Projects\model-orchestrator\config.yaml`

# End of paste
# -----------------------------------------------------------------------
