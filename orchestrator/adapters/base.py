from __future__ import annotations
import sys
from abc import ABC, abstractmethod


def message_text(message) -> str:
    """Plain answer text from an OpenAI-compatible chat message.

    Reasoning models break the naive `.content` access two ways: they return
    `content` as a LIST of blocks (→ a list, which crashes downstream str ops),
    or they leave `content` empty and put the answer in `reasoning_content`
    (→ "" empty output). Both were observed live (magistral list-crash;
    deepseek-v4-pro empty output). This normalises every case to visible text.
    """
    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(text)
        content = "".join(parts)
    if not content:
        content = getattr(message, "reasoning_content", "") or ""
    return content or ""


class TruncatedOutputError(RuntimeError):
    """The output budget ran out before the model wrote any answer."""


def answer_text(response) -> str:
    """The answer from an OpenAI-compatible chat completion — or a loud failure.

    A reasoning model's hidden reasoning counts against max_tokens. When the budget
    runs out mid-reasoning, `finish_reason` is "length" and `content` is empty. The
    old path fell back to `reasoning_content` and printed the model's monologue as if
    it were the answer — the gate then reported `passed: True` on a non-answer
    (observed 2026-09-30: deepseek-v4-pro spent all 16 000 tokens reasoning over a
    4.5k-token prompt). Raise instead, saying how to fix it.
    """
    choice = response.choices[0]
    finish = getattr(choice, "finish_reason", None)
    content = getattr(choice.message, "content", None)
    if isinstance(content, list) or content:
        text = message_text(choice.message)
        if finish == "length":
            print("warning: output truncated at --max-tokens; the answer may be incomplete",
                  file=sys.stderr)
        return text
    if finish == "length":
        usage = getattr(response, "usage", None)
        used = getattr(usage, "completion_tokens", None)
        raise TruncatedOutputError(
            f"the model used all {used if used is not None else 'its'} output tokens reasoning and wrote "
            f"no answer (finish_reason=length). Raise --max-tokens, or lower the reasoning "
            f"with --thinking low / --thinking off."
        )
    # Finished normally with an empty `content`: some providers put the answer in
    # `reasoning_content` (kept for compatibility).
    return message_text(choice.message)


# --thinking levels each provider accepts, and how they map onto its API.
THINKING_LEVELS = ("off", "low", "medium", "high")


def thinking_params(provider: str, level: str | None) -> dict:
    """Request parameters for a --thinking level (empty = the provider default)."""
    if level is None:
        return {}
    if provider == "deepseek":
        if level == "off":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {"reasoning_effort": level}
    if provider == "openai" and level in ("low", "medium", "high"):
        return {"reasoning_effort": level}
    raise ValueError(f"--thinking {level} is not supported for provider '{provider}'")


def content_blocks_text(message) -> str:
    """Visible answer text from an Anthropic Messages response.

    Claude Opus 5 runs adaptive thinking by DEFAULT (Opus 4.8 did not), so
    `content[0]` is a ThinkingBlock with no `.text` and the naive
    `content[0].text` raises AttributeError. Observed live 2026-09-22 on the
    4.8 -> 5 roster upgrade. Concatenate only the visible text blocks.
    """
    parts = []
    for block in getattr(message, "content", None) or []:
        if isinstance(block, dict):
            text = block.get("text") if block.get("type") == "text" else None
        elif getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
        else:
            text = None
        if text:
            parts.append(text)
    return "".join(parts)


class Adapter(ABC):
    @abstractmethod
    def complete(self, prompt: str, model: str, **kwargs) -> str:
        """Send prompt to model and return the response text."""
        ...
