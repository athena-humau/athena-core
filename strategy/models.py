from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@dataclass(frozen=True)
class Signal:
    symbol: str
    direction: SignalDirection
    strategy_id: str
    timestamp: datetime
    confidence: float = 0.0
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("Signal symbol must be a non-empty string")

        if not isinstance(self.direction, SignalDirection):
            raise ValueError("Signal direction must be an instance of SignalDirection")

        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            raise ValueError("Signal strategy_id must be a non-empty string")

        if not isinstance(self.timestamp, datetime):
            raise ValueError("Signal timestamp must be a datetime instance")

        if not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool):
            raise ValueError("Signal confidence must be a float between 0.0 and 1.0")

        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError("Signal confidence must be between 0.0 and 1.0")

        if not isinstance(self.reason, str):
            raise ValueError("Signal reason must be a string")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("Signal metadata must be a mapping")

        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )
