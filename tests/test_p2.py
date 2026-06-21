import pytest

from orchestrator.config import load_config
from orchestrator import judge as judge_mod
from orchestrator.judge import judge, JudgeProviderError


class FakeAdapter:
    """Returns a canned judge response regardless of prompt."""

    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, prompt: str, model: str, **kwargs) -> str:
        return self._response


def test_google_adapter_registered():
    from orchestrator.adapters import get_adapter
    from orchestrator.adapters.google import GoogleAdapter

    assert isinstance(get_adapter("google"), GoogleAdapter)


def test_judge_passes_high_score(monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(
        judge_mod, "get_adapter",
        lambda provider: FakeAdapter('{"score": 0.95, "reasoning": "complete and correct"}'),
    )
    task = {"description": "scaffold a CRUD API", "model": "deepseek-v4-flash"}
    result = judge("def create(): ...", task, cfg)
    assert result["passed"] is True
    assert result["escalated"] is False
    assert result["meta"] is None
    assert result["score"] == 0.95
    assert result["final_score"] == 0.95


def test_judge_escalates_low_score_to_meta(monkeypatch):
    cfg = load_config()
    calls: list[str] = []

    def fake_get_adapter(provider):
        calls.append(provider)
        if provider == "google":  # primary judge returns low confidence
            return FakeAdapter('{"score": 0.40, "reasoning": "incomplete"}')
        return FakeAdapter('{"score": 0.82, "reasoning": "acceptable on review"}')

    monkeypatch.setattr(judge_mod, "get_adapter", fake_get_adapter)
    task = {"description": "scaffold a CRUD API", "model": "deepseek-v4-flash"}
    result = judge("partial output", task, cfg)

    assert result["escalated"] is True
    assert result["score"] == 0.40            # primary
    assert result["meta"]["score"] == 0.82    # meta-judge
    assert result["final_score"] == 0.82
    assert result["passed"] is True           # meta verdict clears the floor
    # primary (google) ran, then the configured meta-judge provider ran
    meta_provider = cfg["models"][cfg["roles"]["judge"]["meta"]]["provider"]
    assert "google" in calls and meta_provider in calls


def test_judge_raises_when_provider_matches_worker(monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(
        judge_mod, "get_adapter",
        lambda provider: FakeAdapter('{"score": 0.9, "reasoning": "x"}'),
    )
    # Worker is gemini-3.5-flash (google); primary judge is also google -> must raise.
    task = {"description": "summarize a long doc", "model": "gemini-3.5-flash"}
    with pytest.raises(JudgeProviderError):
        judge("output", task, cfg)


def test_judge_handles_markdown_fenced_json(monkeypatch):
    cfg = load_config()
    fenced = '```json\n{"score": 0.88, "reasoning": "fine"}\n```'
    monkeypatch.setattr(judge_mod, "get_adapter", lambda provider: FakeAdapter(fenced))
    task = {"description": "do a thing", "model": "deepseek-v4-flash"}
    result = judge("output", task, cfg)
    assert result["score"] == 0.88
    assert result["passed"] is True
