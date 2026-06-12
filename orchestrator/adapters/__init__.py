from __future__ import annotations

from .base import Adapter
from .anthropic import AnthropicAdapter


def get_adapter(provider: str) -> Adapter:
    if provider == "anthropic":
        return AnthropicAdapter()
    raise ValueError(
        f"no adapter registered for provider '{provider}' "
        f"(available: anthropic)"
    )
