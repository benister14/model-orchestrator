from __future__ import annotations

import os
import anthropic as _sdk
from .base import Adapter


class AnthropicAdapter(Adapter):
    def __init__(self) -> None:
        self._client = None

    def _client_(self):
        # Lazy: constructing an adapter must not require credentials, only calling it.
        if self._client is None:
            self._client = _sdk.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        return self._client

    def complete(self, prompt: str, model: str, **kwargs) -> str:
        max_tokens: int = kwargs.get("max_tokens", 1024)
        create_kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if kwargs.get("system"):
            create_kwargs["system"] = kwargs["system"]
        msg = self._client_().messages.create(**create_kwargs)
        return msg.content[0].text
