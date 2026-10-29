from abc import ABC, abstractmethod
from typing import Optional


class BaseLogger(ABC):
    @abstractmethod
    def LogWarning(self, error_type: str, exception: Optional[Exception] = None) -> None:
        pass

    def LogEvent(self, event: str, **details) -> None:
        """Optional structured progress event."""
        pass
