from __future__ import annotations

from .base import Adapter
from .anthropic import AnthropicAdapter
from .deepseek import DeepSeekAdapter


def get_adapter(provider: str) -> Adapter:
    if provider == "anthropic":
        return AnthropicAdapter()
    if provider == "deepseek":
        return DeepSeekAdapter()
    raise ValueError(
        f"no adapter registered for provider '{provider}' "
        f"(available: anthropic, deepseek)"
    )
