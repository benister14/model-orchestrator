import json
from datetime import date
from pathlib import Path

import pytest

from orchestrator.config import load_config
from orchestrator.gate import gate, CostCeilingError


def test_gate_flags_email_in_output():
    cfg = load_config()
    output = "Contact us at john.doe@example.com for support."
    task = {"description": "test task"}
    result = gate(output, task, cfg)
    assert result["passed"] is False
    assert any("pii" in f for f in result["flags"])


def test_gate_passes_clean_output():
    cfg = load_config()
    output = "The API endpoint returns a 200 status code."
    task = {"description": "test task"}
    result = gate(output, task, cfg)
    assert result["passed"] is True
    assert result["flags"] == []


def test_gate_flags_pii_in_client_field():
    cfg = load_config()
    output = "Task completed successfully."
    task = {"description": "process records", "client_email": "ceo@corp.com"}
    result = gate(output, task, cfg)
    assert result["passed"] is False
    assert any("task.client_email" in f for f in result["flags"])


def test_gate_raises_on_cost_ceiling_breach(monkeypatch):
    import shutil
    import tempfile
    cfg = load_config()
    # Use tempfile to avoid pytest tmp_path permission issues on Windows.
    log_dir = tempfile.mkdtemp()
    try:
        monkeypatch.setenv("ORCHESTRATOR_LOG_DIR", log_dir)
        log_file = Path(log_dir) / f"{date.today().isoformat()}.jsonl"
        log_file.write_text(
            json.dumps({"ts": "2026-06-12T00:00:00Z", "event": "route", "cost_usd": 2.01}) + "\n",
            encoding="utf-8",
        )
        # Zero-cost call should still be blocked because current_spend > ceiling.
        with pytest.raises(CostCeilingError):
            gate("output text", {"description": "anything"}, cfg)
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)


from orchestrator.prefilter import prefilter


def test_prefilter_tags_code_task():
    result = prefilter({"description": "scaffold a CRUD API endpoint"})
    assert result["type"] == "code"
    assert 1 <= result["complexity"] <= 5
    assert result["risk"] in ("low", "medium", "high")
    assert result["sensitive"] is False


def test_prefilter_tags_architecture_task():
    result = prefilter({"description": "design a distributed microservice architecture"})
    assert result["type"] == "architecture"
    assert result["complexity"] >= 3


def test_prefilter_sets_sensitive_flag_on_caller_request():
    result = prefilter({"description": "process user data"}, sensitive=True)
    assert result["sensitive"] is True


def test_prefilter_sets_sensitive_flag_on_pii_signal():
    result = prefilter({"description": "send confirmation to jane.doe@corp.com"})
    assert result["sensitive"] is True


def test_prefilter_passes_full_description_through():
    # The worker must receive the WHOLE task. A prior 500-char truncation silently
    # fed the worker a fragment and made every real prompt fail.
    long_desc = "word " * 200  # 1000-char description
    result = prefilter({"description": long_desc})
    assert result["description"] == long_desc          # passed through unchanged
    assert result["complexity"] == 3                   # tagging still works on the full text


import copy
from orchestrator.router import route, LaneViolationError, RoutingError


def test_router_default_routes_to_open_worker():
    cfg = load_config()
    task = {"description": "scaffold a CRUD API", "type": "code",
            "complexity": 1, "risk": "low", "sensitive": False}
    model_name, endpoint = route(task, cfg)
    # Default (non-sensitive, low complexity) → worker open → deepseek-v4-flash
    assert model_name == "deepseek-v4-flash"
    assert endpoint is None


def test_router_sensitive_resolves_to_trusted_lane():
    cfg = load_config()
    task = {"description": "process client data", "type": "batch",
            "complexity": 1, "risk": "low", "sensitive": True}
    model_name, endpoint = route(task, cfg)
    trusted_providers = set(cfg["lanes"]["trusted"]["providers"])
    provider = cfg["models"][model_name]["provider"]
    assert provider in trusted_providers


def test_router_raises_lane_violation_for_bad_config():
    cfg = load_config()
    bad_cfg = copy.deepcopy(cfg)
    # Point the sensitive worker slot at deepseek (untrusted provider)
    bad_cfg["roles"]["worker"]["sensitive"] = "deepseek-v4-flash"
    task = {"description": "process client data", "type": "batch",
            "complexity": 1, "risk": "low", "sensitive": True}
    with pytest.raises(LaneViolationError):
        route(task, bad_cfg)


def test_router_high_risk_routes_to_orchestrator():
    cfg = load_config()
    task = {"description": "design auth system", "type": "architecture",
            "complexity": 4, "risk": "high", "sensitive": False}
    model_name, endpoint = route(task, cfg)
    assert model_name == "claude-sonnet-4-6"


def test_router_long_context_routes_to_long_context_lane():
    cfg = load_config()
    task = {"description": "summarize document", "type": "code",
            "complexity": 2, "risk": "low", "sensitive": False,
            "context_tokens": 150_000}
    model_name, endpoint = route(task, cfg)
    model_roles = cfg["models"][model_name]["roles"]
    assert any("long_context" in r for r in model_roles)


def test_router_sensitive_low_complexity_routes_to_mistral():
    cfg = load_config()
    # Sensitive batch task, complexity=1 — should go to worker.sensitive (mistral-small),
    # NOT to the orchestrator (claude-sonnet-4-6). The trusted lane handles safety;
    # orchestrator is reserved for architecture and high-complexity tasks.
    task = {"description": "normalise client contact records", "type": "batch",
            "complexity": 1, "risk": "high", "sensitive": True}
    model_name, endpoint = route(task, cfg)
    assert model_name == "mistral-small"


def test_router_sensitive_architecture_stays_in_trusted_lane():
    cfg = load_config()
    task = {"description": "design secure auth system", "type": "architecture",
            "complexity": 4, "risk": "high", "sensitive": True}
    model_name, endpoint = route(task, cfg)
    trusted_providers = set(cfg["lanes"]["trusted"]["providers"])
    provider = cfg["models"][model_name]["provider"]
    assert provider in trusted_providers


from orchestrator.cli import main


def test_dry_run_route_prints_model_and_cost_no_api_call(capsys):
    ret = main(["route", "scaffold a CRUD API", "--dry-run"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "model:" in out
    assert "lane:" in out
    assert "est. cost:" in out
    assert "[dry-run]" in out
