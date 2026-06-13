import os

from openai import OpenAI

from .base import Adapter

_BASE_URL = "https://api.mistral.ai/v1"


class MistralAdapter(Adapter):
    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=os.environ.get("MISTRAL_API_KEY"),
            base_url=_BASE_URL,
        )

    def complete(self, prompt: str, model: str, **kwargs) -> str:
        max_tokens: int = kwargs.get("max_tokens", 1024)
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
