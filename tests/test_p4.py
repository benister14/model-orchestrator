import json
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from orchestrator.report import build_report, load_events, render


def _routes_and_judges():
    return [
        {"event": "route", "task_type": "code", "model": "deepseek-v4-flash", "cost_usd": 0.0002},
        {"event": "route", "task_type": "code", "model": "deepseek-v4-flash", "cost_usd": 0.0004},
        {"event": "route", "task_type": "batch", "model": "mistral-small", "cost_usd": 0.0001},
        {"event": "route", "task_type": "architecture", "model": "claude-sonnet-4-6", "cost_usd": 0.0150},
        # judges: code escalates 1/2 (50% > 30%), batch 0/1
        {"event": "judge", "task_type": "code", "passed": True, "escalated": False},
        {"event": "judge", "task_type": "code", "passed": False, "escalated": True},
        {"event": "judge", "task_type": "batch", "passed": True, "escalated": False},
    ]


def test_cost_by_task_type():
    r = build_report(_routes_and_judges())
    assert r["by_task_type"]["code"]["count"] == 2
    assert abs(r["by_task_type"]["code"]["total_cost"] - 0.0006) < 1e-9
    assert abs(r["by_task_type"]["code"]["avg_cost"] - 0.0003) < 1e-9
    assert abs(r["total_cost"] - 0.0157) < 1e-9


def test_judge_pass_and_escalation_rate():
    r = build_report(_routes_and_judges())
    assert r["n_judged"] == 3
    assert abs(r["pass_rate"] - 2 / 3) < 1e-9
    assert abs(r["escalation_rate"] - 1 / 3) < 1e-9


def test_high_escalation_task_type_flagged():
    r = build_report(_routes_and_judges())
    assert r["escalation_by_task_type"]["code"]["flagged"] is True   # 50% > 30%
    assert r["escalation_by_task_type"]["batch"]["flagged"] is False  # 0%


def test_cheapest_and_priciest():
    r = build_report(_routes_and_judges())
    assert r["cheapest"]["model"] == "mistral-small"
    assert r["priciest"]["model"] == "claude-sonnet-4-6"


def test_empty_report_is_safe():
    r = build_report([])
    assert r["n_routes"] == 0 and r["n_judged"] == 0
    assert r["pass_rate"] is None and r["escalation_rate"] is None
    assert r["cheapest"] is None
    # render must not raise on an empty report
    text = render(r, 7)
    assert "no route events" in text


def test_load_events_respects_days_window():
    log_dir = Path(tempfile.mkdtemp())
    try:
        today = date(2026, 6, 15)
        # in-window (today and 2 days ago) + out-of-window (10 days ago)
        (log_dir / f"{today.isoformat()}.jsonl").write_text(
            json.dumps({"event": "route", "task_type": "code", "cost_usd": 0.1}) + "\n",
            encoding="utf-8")
        (log_dir / f"{(today - timedelta(days=2)).isoformat()}.jsonl").write_text(
            json.dumps({"event": "route", "task_type": "code", "cost_usd": 0.2}) + "\n",
            encoding="utf-8")
        (log_dir / f"{(today - timedelta(days=10)).isoformat()}.jsonl").write_text(
            json.dumps({"event": "route", "task_type": "code", "cost_usd": 0.9}) + "\n",
            encoding="utf-8")
        events = load_events(7, today=today, log_dir=log_dir)
        costs = sorted(e["cost_usd"] for e in events)
        assert costs == [0.1, 0.2]  # the 10-day-old record is excluded
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)


def test_report_command_runs(capsys, monkeypatch):
    log_dir = tempfile.mkdtemp()
    try:
        monkeypatch.setenv("ORCHESTRATOR_LOG_DIR", log_dir)
        log_file = Path(log_dir) / f"{date.today().isoformat()}.jsonl"
        log_file.write_text(
            json.dumps({"event": "route", "task_type": "code",
                        "model": "deepseek-v4-flash", "cost_usd": 0.0003}) + "\n",
            encoding="utf-8")
        from orchestrator.cli import main
        ret = main(["report", "--days", "7"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Cost by task type" in out
        assert "Judge" in out
        assert "code" in out
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)
