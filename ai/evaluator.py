from abc import ABC, abstractmethod

from ai.models import StrategyCandidate, StrategyEvaluation
from strategy.context import StrategyContext


class StrategyEvaluator(ABC):
    """
    Abstract Base Class for strategy evaluators in ATHENA.
    Evaluates StrategyContext and a StrategyCandidate to produce a StrategyEvaluation.
    """

    evaluator_id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, evaluator_id: str | None = None):
        if evaluator_id is not None:
            self.evaluator_id = evaluator_id

        if not isinstance(self.evaluator_id, str) or not self.evaluator_id:
            raise ValueError(f"Evaluator {self.__class__.__name__} must define a non-empty evaluator_id")

    @abstractmethod
    def evaluate(
        self,
        context: StrategyContext,
        candidate: StrategyCandidate,
    ) -> StrategyEvaluation:
        """
        Evaluate candidate strategy suitability for the given context.
        Must NOT access network/exchange APIs, execute orders, or perform risk management.
        """
        pass
