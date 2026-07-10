from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"
    OUT_OF_CREDITS = "OUT_OF_CREDITS"
    RECOVERING = "RECOVERING"
    OFFLINE = "OFFLINE"
    DISABLED = "DISABLED"


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True)
class ProviderRequest:
    request_id: str
    judge_name: str
    payload: Dict[str, Any]
    timeout_s: int
    schema: Optional[Dict[str, Any]] = None
    model_preferences: List[str] = field(default_factory=list)
    fallback_models: List[str] = field(default_factory=list)
    retries: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResponse:
    request_id: str
    provider: str
    model: str
    latency_ms: float
    raw_response: Dict[str, Any]
    parsed_json: Optional[Dict[str, Any]]
    success: bool
    error: str = ""
    retry_count: int = 0
    queue_wait_ms: float = 0.0
    tokens: Dict[str, Any] = field(default_factory=dict)

    @property
    def payload(self) -> Dict[str, Any]:
        return self.raw_response


class ProviderError(Exception):
    def __init__(self, message: str, category: str = "provider_error"):
        super().__init__(message)
        self.category = category


class ProviderValidationError(ProviderError):
    def __init__(self, message: str):
        super().__init__(message, "validation")
