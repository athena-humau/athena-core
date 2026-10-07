from abc import ABC, abstractmethod
from typing import Sequence

from ai.models import RankedStrategy, StrategyEvaluation


class StrategyRanker(ABC):
    """
    Abstract Base Class for strategy rankers in ATHENA.
    Ranks strategy evaluations deterministically based on suitability scores and eligibility.
    """

    ranker_id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, ranker_id: str | None = None):
        if ranker_id is not None:
            self.ranker_id = ranker_id

        if not isinstance(self.ranker_id, str) or not self.ranker_id:
            raise ValueError(f"Ranker {self.__class__.__name__} must define a non-empty ranker_id")

    @abstractmethod
    def rank(
        self,
        evaluations: Sequence[StrategyEvaluation],
    ) -> Sequence[RankedStrategy]:
        """
        Rank strategy evaluations deterministically.
        Must NOT mutate the input sequence.
        Must NOT access network, exchange, or future data.
        """
        pass
