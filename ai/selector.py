from abc import ABC, abstractmethod
from typing import Any, Sequence

from ai.models import StrategyCandidate, StrategySelection
from strategy.context import StrategyContext


class StrategySelector(ABC):
    """
    Abstract Base Class for all strategy selectors in ATHENA.
    Evaluates StrategyContext and available StrategyCandidates (or ranked evaluations)
    to produce a StrategySelection.
    """

    selector_id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, selector_id: str | None = None):
        if selector_id is not None:
            self.selector_id = selector_id

        if not isinstance(self.selector_id, str) or not self.selector_id:
            raise ValueError(f"Selector {self.__class__.__name__} must define a non-empty selector_id")

    @abstractmethod
    def select(
        self,
        context: StrategyContext,
        candidates: Sequence[Any],
    ) -> StrategySelection:
        """
        Select a strategy among available candidates based on market context.
        Must NOT execute orders, place trades, or perform risk management.
        """
        pass
