import os

from openai import OpenAI

from .base import Adapter, message_text

# Gemini exposes an OpenAI-compatible endpoint, so we reuse the openai SDK
# rather than add a google-genai dependency (see code conventions in CLAUDE.md).
_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class GoogleAdapter(Adapter):
    def __init__(self) -> None:
        self._client: OpenAI | None = None

    def _client_(self) -> OpenAI:
        # Lazy: constructing an adapter must not require credentials, only calling it.
        if self._client is None:
            self._client = OpenAI(
                api_key=os.environ.get("GEMINI_API_KEY"),
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
        return message_text(response.choices[0].message)
