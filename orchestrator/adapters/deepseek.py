from __future__ import annotations

import os
from openai import OpenAI
from .base import Adapter

_BASE_URL = "https://api.deepseek.com/v1"


class DeepSeekAdapter(Adapter):
    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url=_BASE_URL,
        )

    def complete(self, prompt: str, model: str, **kwargs) -> str:
        max_tokens: int = kwargs.get("max_tokens", 1024)
        messages = []
        if kwargs.get("system"):
            messages.append({"role": "system", "content": kwargs["system"]})
        messages.append({"role": "user", "content": prompt})
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return response.choices[0].message.content
