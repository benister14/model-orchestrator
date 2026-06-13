# orchestrator/router.py
"""Stage 1 — routing.

route(task, cfg) -> (model_name, endpoint_url | None)

Resolves a tagged task to a concrete model via config.yaml role resolution.
Sensitive tasks are constrained to the trusted lane; violation raises LaneViolationError.
"""
from __future__ import annotations


class LaneViolationError(Exception):
    """Raised when a sensitive task would be routed to an untrusted provider."""


class RoutingError(Exception):
    """Raised when no model can be resolved for the given task."""


def route(task: dict, cfg: dict) -> tuple[str, str | None]:
    """
    Returns (model_name, endpoint_url).
    endpoint_url is None unless an EU residency endpoint is required.
    """
    sensitive = task.get("sensitive", False)
    task_type = task.get("type", "code")
    complexity = task.get("complexity", 1)
    risk = task.get("risk", "low")
    context_tokens = task.get("context_tokens", 0)
    requires_cot = task.get("requires_cot", False)

    # ---- Role selection (rules in priority order) ----
    # Architecture always needs the orchestrator model.
    # High risk alone does NOT force orchestrator — that's what the trusted lane
    # is for. Only escalate on high risk when the task is also complex (>=3),
    # e.g. a security design. Simple sensitive tasks (batch, tagging) go to the
    # trusted worker (mistral-small) which is far cheaper.
    if task_type == "architecture" or (risk == "high" and complexity >= 3):
        role, variant = "orchestrator", "default"
    elif context_tokens > 100_000:
        role = "long_context"
        variant = "sensitive" if sensitive else "open"
    elif requires_cot or complexity >= 4:
        role = "reasoner"
        variant = "sensitive" if sensitive else "open"
    else:
        role = "worker"
        variant = "sensitive" if sensitive else "open"

    # ---- Resolve model from config ----
    role_spec = cfg.get("roles", {}).get(role, {})
    model_name = role_spec.get(variant) or role_spec.get("default")
    if not model_name:
        raise RoutingError(
            f"no model configured for role='{role}', variant='{variant}'"
        )

    if model_name not in cfg.get("models", {}):
        raise RoutingError(
            f"resolved model '{model_name}' not found in models registry"
        )

    # ---- Lane enforcement for sensitive tasks ----
    if sensitive:
        trusted_providers = set(cfg["lanes"]["trusted"]["providers"])
        provider = cfg.get("models", {}).get(model_name, {}).get("provider")
        if provider not in trusted_providers:
            raise LaneViolationError(
                f"sensitive task → model '{model_name}' (provider '{provider}') "
                f"is outside the trusted lane {sorted(trusted_providers)}"
            )

    # ---- Endpoint selection (EU residency for sensitive OpenAI calls) ----
    endpoint: str | None = None
    provider = cfg.get("models", {}).get(model_name, {}).get("provider")
    if sensitive and provider == "openai":
        endpoint = cfg.get("providers", {}).get("openai", {}).get("eu_endpoint")

    return model_name, endpoint
