from __future__ import annotations
from abc import ABC, abstractmethod


class Adapter(ABC):
    @abstractmethod
    def complete(self, prompt: str, model: str, **kwargs) -> str:
        """Send prompt to model and return the response text."""
