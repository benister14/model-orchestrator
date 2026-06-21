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


class Adapter(ABC):
    @abstractmethod
    def complete(self, prompt: str, model: str, **kwargs) -> str:
        """Send prompt to model and return the response text."""
        ...
