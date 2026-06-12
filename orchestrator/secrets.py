"""Secrets discovery. Loads .env (no dependency) and reports which provider
API keys are present. Never returns or logs the key values themselves."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: str | os.PathLike | None = None) -> None:
    """Minimal .env loader. Does not override variables already in the environment."""
    env_path = Path(path) if path else REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def key_status(cfg: dict) -> dict[str, bool]:
    """Return {provider: True/False} for whether its API key is set."""
    load_dotenv()
    out: dict[str, bool] = {}
    for name, p in cfg.get("providers", {}).items():
        env = p.get("env")
        out[name] = bool(env and os.environ.get(env))
    return out
