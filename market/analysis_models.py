from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from data.candle import Candle
from features.models import TimeframeFeatures
from regime.models import TimeframeRegime
from scanner.ranking_engine import RankedMarket


@dataclass(frozen=True)
class MarketAnalysis:
    symbol: str
    ranking: RankedMarket | None
    candles_by_granularity: Mapping[str, list[Candle]]
    features_by_granularity: Mapping[str, TimeframeFeatures]
    regimes_by_granularity: Mapping[str, TimeframeRegime]
    success: bool
    error: str | None = None
    analyzed_at: datetime | None = None

    def __post_init__(self):
        object.__setattr__(
            self,
            "candles_by_granularity",
            MappingProxyType(dict(self.candles_by_granularity)),
        )
        object.__setattr__(
            self,
            "features_by_granularity",
            MappingProxyType(dict(self.features_by_granularity)),
        )
        object.__setattr__(
            self,
            "regimes_by_granularity",
            MappingProxyType(dict(self.regimes_by_granularity)),
        )
