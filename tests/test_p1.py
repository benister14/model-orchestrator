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
