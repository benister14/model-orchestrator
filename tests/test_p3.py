import copy

import pytest

from orchestrator.config import load_config
from orchestrator.router import route


# ---- OpenAI adapter -------------------------------------------------------

def test_openai_adapter_registered():
    from orchestrator.adapters import get_adapter
    from orchestrator.adapters.openai import OpenAIAdapter

    assert isinstance(get_adapter("openai"), OpenAIAdapter)


def test_openai_adapter_uses_eu_endpoint_when_passed(monkeypatch):
    # Constructing a client needs *a* key string; use a dummy (no call is made).
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    from orchestrator.adapters.openai import OpenAIAdapter

    adapter = OpenAIAdapter()
    eu = "https://eu.api.openai.com/v1"
    eu_client = adapter._client_(eu)
    default_client = adapter._client_(None)
    assert "eu.api.openai.com" in str(eu_client.base_url)
    assert "eu.api.openai.com" not in str(default_client.base_url)


# ---- Long-context lane ----------------------------------------------------

def test_long_context_open_routes_to_gemini():
    cfg = load_config()
    task = {"description": "summarize a big doc", "type": "batch",
            "complexity": 2, "risk": "low", "sensitive": False,
            "context_tokens": 150_000}
    model_name, endpoint = route(task, cfg)
    assert model_name == "gemini-3.8-flash"
    assert "long_context_open" in cfg["models"][model_name]["roles"]
    assert endpoint is None


def test_long_context_sensitive_stays_in_trusted_lane():
    cfg = load_config()
    task = {"description": "summarize a confidential client doc", "type": "batch",
            "complexity": 2, "risk": "high", "sensitive": True,
            "context_tokens": 150_000}
    model_name, endpoint = route(task, cfg)
    assert model_name == "claude-sonnet-5"
    trusted = set(cfg["lanes"]["trusted"]["providers"])
    assert cfg["models"][model_name]["provider"] in trusted


# ---- Reasoning lane -------------------------------------------------------

def test_reasoner_open_routes_to_deepseek_pro():
    cfg = load_config()
    task = {"description": "reason carefully", "type": "reasoning",
            "complexity": 2, "risk": "low", "sensitive": False,
            "requires_cot": True}
    model_name, endpoint = route(task, cfg)
    assert model_name == "deepseek-v4-pro"
    assert endpoint is None


def test_reasoner_sensitive_routes_to_mistral_medium():
    cfg = load_config()
    task = {"description": "reason over client data", "type": "reasoning",
            "complexity": 2, "risk": "high", "sensitive": True,
            "requires_cot": True}
    model_name, endpoint = route(task, cfg)
    assert model_name == "mistral-medium-3-5"
    trusted = set(cfg["lanes"]["trusted"]["providers"])
    assert cfg["models"][model_name]["provider"] in trusted


# ---- Residency endpoint selection in the router ---------------------------

def _sensitive_openai_route(cfg):
    """Route a sensitive task to an OpenAI model and return (model, endpoint).

    openai IS in the trusted lane, so pinning the sensitive reasoner to it is
    lane-valid.
    """
    patched = copy.deepcopy(cfg)
    patched["roles"]["reasoner"]["sensitive"] = "gpt-5.6-terra"
    task = {"description": "reason over client data", "type": "reasoning",
            "complexity": 2, "risk": "high", "sensitive": True,
            "requires_cot": True}
    return route(task, patched)


def test_router_forwards_configured_residency_endpoint():
    """The router forwards whatever providers.openai.eu_endpoint holds.

    This asserts the MECHANISM, not the current policy value: residency routing
    is a config decision that has flipped before and may flip back.
    """
    cfg = load_config()
    cfg["providers"]["openai"]["eu_endpoint"] = "https://eu.api.openai.com/v1"
    model_name, endpoint = _sensitive_openai_route(cfg)
    assert model_name == "gpt-5.6-terra"
    assert endpoint == "https://eu.api.openai.com/v1"


def test_router_returns_no_endpoint_when_residency_unset():
    """Owner authorisation 2026-09-23: openai.eu_endpoint is null, so sensitive
    OpenAI traffic goes to the standard endpoint. A None endpoint makes the
    adapter fall back to the SDK default."""
    cfg = load_config()
    assert cfg["providers"]["openai"]["eu_endpoint"] is None, (
        "config changed: if residency was restored, this test should assert it"
    )
    model_name, endpoint = _sensitive_openai_route(cfg)
    assert model_name == "gpt-5.6-terra"
    assert endpoint is None


# ---- CLI dry-run exit criterion ------------------------------------------

def test_dry_run_long_context_routes_via_cli(capsys):
    from orchestrator.cli import main
    ret = main(["route", "summarize this huge document",
                "--context-tokens", "150000", "--dry-run"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "gemini-3.8-flash" in out
    assert "[dry-run]" in out


# ---- --worker must respect retirement ------------------------------------

def test_worker_pin_rejects_retired_model(capsys):
    """A retired roster entry must not be pinnable.

    Retired models stay in config.yaml for reference, but they no longer
    resolve at their providers. Before 2026-09-23 --worker validated against
    the raw registry, so pinning one passed validation and failed later as an
    opaque provider 404.
    """
    from orchestrator.cli import main
    ret = main(["route", "x", "--worker", "deepseek-v4-flash", "--dry-run"])
    assert ret == 1
    assert "retired" in capsys.readouterr().err


def test_worker_pin_accepts_active_model(capsys):
    from orchestrator.cli import main
    ret = main(["route", "x", "--worker", "gemini-3.8-flash", "--dry-run"])
    assert ret == 0
    assert "gemini-3.8-flash" in capsys.readouterr().out
