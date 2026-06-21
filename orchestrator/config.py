"""Load and validate config.yaml; resolve the effective (live) roster."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"


class ConfigError(Exception):
    """Raised when config.yaml is missing or violates an invariant."""


def load_config(path: str | os.PathLike | None = None) -> dict:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise ConfigError(f"config not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    providers = set(cfg.get("providers", {}))
    models = cfg.get("models", {})

    for lane, spec in cfg.get("lanes", {}).items():
        for p in spec.get("providers", []):
            if p not in providers:
                raise ConfigError(f"lane '{lane}' references unknown provider '{p}'")

    trusted = set(cfg["lanes"]["trusted"]["providers"])

    # Hard invariant: any 'sensitive' role resolution must live in the trusted lane.
    for role, spec in cfg.get("roles", {}).items():
        for key, model in _iter_role_models(spec):
            if "sensitive" not in key:
                continue
            prov = models.get(model, {}).get("provider")
            if prov and prov not in trusted:
                raise ConfigError(
                    f"role '{role}.{key}' -> '{model}' ({prov}) is outside the trusted lane"
                )


def _iter_role_models(spec):
    """Yield (key, model_name) pairs from a role spec, one or two levels deep."""
    if not isinstance(spec, dict):
        return
    for k, v in spec.items():
        if isinstance(v, str):
            yield k, v
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, str):
                    yield f"{k}.{k2}", v2


def api_model_of(name: str, cfg: dict) -> str:
    """The provider's API model id for a roster name.

    The roster key is a stable DISPLAY name; the provider's actual API model id
    can drift (Mistral/OpenAI ship `-latest` aliases). An optional `api_model`
    field on a model decouples the two so a stale id is a one-line config change,
    not a hard 400 on every call routed to it.
    """
    return cfg.get("models", {}).get(name, {}).get("api_model", name)


def effective_models(cfg: dict, today: date | None = None) -> dict:
    """Active roster: drops retired models and anything at/past its eol_date."""
    today = today or date.today()
    out: dict = {}
    for name, m in cfg.get("models", {}).items():
        if m.get("status") == "retired":
            continue
        eol = m.get("eol_date")
        if eol:
            eol_d = eol if isinstance(eol, date) else date.fromisoformat(str(eol))
            if today >= eol_d:
                continue
        out[name] = m
    return out
