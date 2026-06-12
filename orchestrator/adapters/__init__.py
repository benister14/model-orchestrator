from __future__ import annotations
from .base import Adapter


def get_adapter(provider: str) -> Adapter:
    raise ValueError(
        f"no adapter registered for provider '{provider}' "
        f"(available: none yet — populated in P1 tasks 7-8)"
    )
