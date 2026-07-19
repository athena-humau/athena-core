from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class TimeframeFeatures:
    symbol: str | None
    granularity: str
    timestamp: datetime | None
    candle_count: int
    values: Mapping[str, int | float | bool]

    def __post_init__(self):

        object.__setattr__(
            self,
            "values",
            MappingProxyType(dict(self.values)),
        )
