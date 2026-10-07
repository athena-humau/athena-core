from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from market.analysis_models import MarketAnalysis


@dataclass(frozen=True)
class StrategyContext:
    analysis: MarketAnalysis
    parameters: Mapping[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if not isinstance(self.analysis, MarketAnalysis):
            raise ValueError("StrategyContext analysis must be a MarketAnalysis instance")

        if not isinstance(self.parameters, (dict, Mapping)):
            raise ValueError("StrategyContext parameters must be a mapping")

        if not isinstance(self.timestamp, datetime):
            raise ValueError("StrategyContext timestamp must be a datetime instance")

        object.__setattr__(
            self,
            "parameters",
            MappingProxyType(dict(self.parameters)),
        )

    @property
    def symbol(self) -> str:
        return self.analysis.symbol
