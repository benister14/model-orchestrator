import json
from datetime import date

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


def test_gate_raises_on_cost_ceiling_breach(tmp_path, monkeypatch):
    cfg = load_config()
    # Write a real log entry so session spend ($2.01) already exceeds the $2.00 ceiling.
    monkeypatch.setenv("ORCHESTRATOR_LOG_DIR", str(tmp_path))
    log_file = tmp_path / f"{date.today().isoformat()}.jsonl"
    log_file.write_text(
        json.dumps({"ts": "2026-06-12T00:00:00Z", "event": "route", "cost_usd": 2.01}) + "\n",
        encoding="utf-8",
    )
    # Zero-cost call should still be blocked because current_spend > ceiling.
    with pytest.raises(CostCeilingError):
        gate("output text", {"description": "anything"}, cfg)


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


def test_prefilter_compresses_long_description():
    long_desc = "word " * 200  # 1000-char description
    result = prefilter({"description": long_desc})
    assert len(result["description"]) <= 500
