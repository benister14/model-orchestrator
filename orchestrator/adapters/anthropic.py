from __future__ import annotations

import os
import anthropic as _sdk
from .base import Adapter


class AnthropicAdapter(Adapter):
    def __init__(self) -> None:
        self._client = _sdk.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def complete(self, prompt: str, model: str, **kwargs) -> str:
        max_tokens: int = kwargs.get("max_tokens", 1024)
        msg = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
