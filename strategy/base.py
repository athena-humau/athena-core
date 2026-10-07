from abc import ABC, abstractmethod

from strategy.context import StrategyContext
from strategy.models import Signal


class BaseStrategy(ABC):
    """
    Abstract Base Class for all ATHENA strategies.
    Strategies evaluate a StrategyContext and return a deterministic Signal.
    """

    strategy_id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, strategy_id: str | None = None):
        if strategy_id is not None:
            self.strategy_id = strategy_id

        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            raise ValueError(f"Strategy {self.__class__.__name__} must define a non-empty strategy_id")

    @abstractmethod
    def evaluate(self, context: StrategyContext) -> Signal:
        """
        Evaluate market context and emit a deterministic Signal.
        Must NOT execute orders, calculate risk sizing, or access exchange APIs.
        """
        pass
