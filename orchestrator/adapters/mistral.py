import os

from openai import OpenAI

from .base import Adapter, answer_text

_BASE_URL = "https://api.mistral.ai/v1"


class MistralAdapter(Adapter):
    def __init__(self) -> None:
        self._client: OpenAI | None = None

    def _client_(self) -> OpenAI:
        # Lazy: constructing an adapter must not require credentials, only calling it.
        if self._client is None:
            self._client = OpenAI(
                api_key=os.environ.get("MISTRAL_API_KEY"),
                base_url=_BASE_URL,
            )
        return self._client

    def complete(self, prompt: str, model: str, **kwargs) -> str:
        max_tokens: int = kwargs.get("max_tokens", 1024)
        messages = []
        if kwargs.get("system"):
            messages.append({"role": "system", "content": kwargs["system"]})
        messages.append({"role": "user", "content": prompt})
        response = self._client_().chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return answer_text(response)
