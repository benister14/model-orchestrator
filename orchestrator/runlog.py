"""Structured run logging.

Logs are written as JSONL to ~/.orchestrator/logs/ (NOT inside the repo) so that
task snippets — which may contain sensitive client data — never land in a tree
you might commit or sync. Override with ORCHESTRATOR_LOG_DIR.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(
    os.environ.get("ORCHESTRATOR_LOG_DIR", Path.home() / ".orchestrator" / "logs")
)


def log_event(event: str, **fields) -> Path:
    """Append one JSONL record and return the file path written to."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    rec = {"ts": now.isoformat(), "event": event, **fields}
    path = LOG_DIR / f"{now.strftime('%Y-%m-%d')}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return path
