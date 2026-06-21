import os

from openai import OpenAI

from .base import Adapter, message_text


class OpenAIAdapter(Adapter):
    """Native OpenAI adapter.

    Unlike the other OpenAI-compatible adapters (deepseek/mistral/google), this one
    has no fixed base URL: it uses the SDK default (api.openai.com) for normal calls,
    and switches to a data-residency endpoint when one is passed via `endpoint=`.
    Sensitive calls MUST route through the EU residency endpoint — the router
    supplies it (config.providers.openai.eu_endpoint) and the CLI forwards it here.
    """

    def __init__(self) -> None:
        self._clients: dict[str, OpenAI] = {}

    def _client_(self, endpoint: str | None) -> OpenAI:
        # Cache one client per endpoint; lazy so construction needs no credentials.
        key = endpoint or "__default__"
        if key not in self._clients:
            self._clients[key] = OpenAI(
                api_key=os.environ.get("OPENAI_API_KEY"),
                base_url=endpoint,  # None → SDK default (https://api.openai.com/v1)
            )
        return self._clients[key]

    def complete(self, prompt: str, model: str, **kwargs) -> str:
        max_tokens: int = kwargs.get("max_tokens", 1024)
        endpoint: str | None = kwargs.get("endpoint")
        messages = []
        if kwargs.get("system"):
            messages.append({"role": "system", "content": kwargs["system"]})
        messages.append({"role": "user", "content": prompt})
        # Newer OpenAI models (gpt-5.x, o-series) reject `max_tokens` and require
        # `max_completion_tokens`; it is accepted by current gpt-4o too.
        response = self._client_(endpoint).chat.completions.create(
            model=model,
            max_completion_tokens=max_tokens,
            messages=messages,
        )
        return message_text(response.choices[0].message)
