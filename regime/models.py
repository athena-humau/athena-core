from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Regime(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TimeframeRegime:
    symbol: str | None
    granularity: str
    timestamp: datetime | None
    candle_count: int
    regime: Regime
