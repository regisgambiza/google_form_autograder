from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseProvider(ABC):
    name = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def chat(self, payload: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
        raise NotImplementedError
