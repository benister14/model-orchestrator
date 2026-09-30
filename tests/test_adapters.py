"""Reasoning budget + --thinking (2026-09-30).

deepseek-v4-pro spent all 16 000 output tokens reasoning over a 4.5k-token batch prompt:
finish_reason=length, empty content. The adapter fell back to reasoning_content, so the
model's monologue was printed as the answer and the gate reported passed=True.
"""
from types import SimpleNamespace as NS

import pytest

from orchestrator.adapters.base import TruncatedOutputError, answer_text, thinking_params
from orchestrator.adapters.deepseek import DeepSeekAdapter
from orchestrator.adapters.openai import OpenAIAdapter
from orchestrator.cli import main


def _resp(content, reasoning="", finish="stop", completion_tokens=100):
    msg = NS(content=content, reasoning_content=reasoning)
    return NS(choices=[NS(message=msg, finish_reason=finish)],
              usage=NS(completion_tokens=completion_tokens))


# ---- answer_text ---------------------------------------------------------

def test_budget_spent_reasoning_raises_instead_of_printing_the_monologue():
    r = _resp("", reasoning="We need answer user. Need parse each entry...", finish="length",
              completion_tokens=16000)
    with pytest.raises(TruncatedOutputError, match="16000 output tokens reasoning"):
        answer_text(r)


def test_budget_spent_reasoning_with_none_content_also_raises():
    with pytest.raises(TruncatedOutputError):
        answer_text(_resp(None, reasoning="thinking...", finish="length"))


def test_normal_answer_is_returned():
    assert answer_text(_resp('[{"code": "9A115.a"}]', reasoning="thought")) == '[{"code": "9A115.a"}]'


def test_partial_answer_is_returned_with_a_warning(capsys):
    assert answer_text(_resp('[{"code": "9A1', finish="length")) == '[{"code": "9A1'
    assert "truncated" in capsys.readouterr().err


def test_finished_with_answer_only_in_reasoning_content_keeps_the_fallback():
    assert answer_text(_resp("", reasoning="the answer", finish="stop")) == "the answer"


# ---- thinking_params -----------------------------------------------------

def test_thinking_maps_per_provider():
    assert thinking_params("deepseek", None) == {}
    assert thinking_params("deepseek", "off") == {"extra_body": {"thinking": {"type": "disabled"}}}
    assert thinking_params("deepseek", "low") == {"reasoning_effort": "low"}
    assert thinking_params("openai", "high") == {"reasoning_effort": "high"}


@pytest.mark.parametrize("provider,level", [("openai", "off"), ("mistral", "low"), ("google", "off"),
                                            ("anthropic", "low")])
def test_thinking_unsupported_is_an_error_not_a_silent_no_op(provider, level):
    with pytest.raises(ValueError, match="not supported"):
        thinking_params(provider, level)


# ---- adapters send it ----------------------------------------------------

class _FakeCompletions:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _fake_client(response):
    comp = _FakeCompletions(response)
    return NS(chat=NS(completions=comp)), comp


def test_deepseek_adapter_sends_thinking_off():
    client, comp = _fake_client(_resp("ok"))
    a = DeepSeekAdapter()
    a._client = client
    assert a.complete("p", "deepseek-v4-pro", max_tokens=100, thinking="off") == "ok"
    assert comp.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_deepseek_adapter_default_sends_no_thinking_params():
    client, comp = _fake_client(_resp("ok"))
    a = DeepSeekAdapter()
    a._client = client
    a.complete("p", "deepseek-v4-pro", max_tokens=100)
    assert "extra_body" not in comp.kwargs and "reasoning_effort" not in comp.kwargs


def test_deepseek_adapter_raises_on_a_reasoning_only_truncation():
    client, _ = _fake_client(_resp("", reasoning="monologue", finish="length", completion_tokens=16000))
    a = DeepSeekAdapter()
    a._client = client
    with pytest.raises(TruncatedOutputError):
        a.complete("p", "deepseek-v4-pro", max_tokens=16000)


def test_openai_adapter_sends_reasoning_effort():
    client, comp = _fake_client(_resp("ok"))
    a = OpenAIAdapter()
    a._clients["__default__"] = client
    a.complete("p", "gpt-5.6-sol", max_tokens=100, thinking="low")
    assert comp.kwargs["reasoning_effort"] == "low"


# ---- CLI -----------------------------------------------------------------

def test_cli_rejects_thinking_on_an_unsupported_worker(capsys):
    assert main(["route", "x", "--worker", "mistral-small", "--thinking", "off", "--dry-run"]) == 1
    assert "not supported" in capsys.readouterr().err


def test_cli_dry_run_shows_the_thinking_level(capsys):
    assert main(["route", "x", "--worker", "deepseek-v4-pro", "--thinking", "off", "--dry-run"]) == 0
    assert "thinking:  off" in capsys.readouterr().out
