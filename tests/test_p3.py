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
    assert model_name == "gemini-3.5-flash"
    assert "long_context_open" in cfg["models"][model_name]["roles"]
    assert endpoint is None


def test_long_context_sensitive_stays_in_trusted_lane():
    cfg = load_config()
    task = {"description": "summarize a confidential client doc", "type": "batch",
            "complexity": 2, "risk": "high", "sensitive": True,
            "context_tokens": 150_000}
    model_name, endpoint = route(task, cfg)
    assert model_name == "claude-sonnet-4-6"
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


def test_reasoner_sensitive_routes_to_magistral():
    cfg = load_config()
    task = {"description": "reason over client data", "type": "reasoning",
            "complexity": 2, "risk": "high", "sensitive": True,
            "requires_cot": True}
    model_name, endpoint = route(task, cfg)
    assert model_name == "mistral-magistral-medium"
    trusted = set(cfg["lanes"]["trusted"]["providers"])
    assert cfg["models"][model_name]["provider"] in trusted


# ---- EU residency endpoint selection in the router ------------------------

def test_router_returns_eu_endpoint_for_sensitive_openai():
    cfg = load_config()
    bad = copy.deepcopy(cfg)
    # Point the sensitive reasoner at an OpenAI model (openai IS in the trusted
    # lane, so this is lane-valid). A sensitive route to it must carry the EU endpoint.
    bad["roles"]["reasoner"]["sensitive"] = "gpt-5.5-codex"
    task = {"description": "reason over client data", "type": "reasoning",
            "complexity": 2, "risk": "high", "sensitive": True,
            "requires_cot": True}
    model_name, endpoint = route(task, bad)
    assert model_name == "gpt-5.5-codex"
    assert endpoint == cfg["providers"]["openai"]["eu_endpoint"]
    assert endpoint and "eu.api.openai.com" in endpoint


# ---- CLI dry-run exit criterion ------------------------------------------

def test_dry_run_long_context_routes_via_cli(capsys):
    from orchestrator.cli import main
    ret = main(["route", "summarize this huge document",
                "--context-tokens", "150000", "--dry-run"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "gemini-3.5-flash" in out
    assert "[dry-run]" in out
