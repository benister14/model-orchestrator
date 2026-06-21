from __future__ import annotations

from .base import Adapter
from .anthropic import AnthropicAdapter
from .deepseek import DeepSeekAdapter
from .mistral import MistralAdapter
from .google import GoogleAdapter
from .openai import OpenAIAdapter


def get_adapter(provider: str) -> Adapter:
    if provider == "anthropic":
        return AnthropicAdapter()
    if provider == "deepseek":
        return DeepSeekAdapter()
    if provider == "mistral":
        return MistralAdapter()
    if provider == "google":
        return GoogleAdapter()
    if provider == "openai":
        return OpenAIAdapter()
    raise ValueError(
        f"no adapter registered for provider '{provider}' "
        f"(available: anthropic, deepseek, mistral, google, openai)"
    )
