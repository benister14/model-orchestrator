from __future__ import annotations
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
