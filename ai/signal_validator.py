from abc import ABC, abstractmethod
from typing import Any

from ai.models import ValidatedCandidate
from strategy.context import StrategyContext


class SignalValidator(ABC):
    """
    Abstract Base Class for strategy signal validators in ATHENA.
    Validates strategy signals structurally and contextually before handoff to Judge.
    Must NOT perform risk management, position sizing, order execution, or trade placement.
    """

    validator_id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, validator_id: str | None = None):
        if validator_id is not None:
            self.validator_id = validator_id

        if not isinstance(self.validator_id, str) or not self.validator_id:
            raise ValueError(f"Validator {self.__class__.__name__} must define a non-empty validator_id")

    @abstractmethod
    def validate(
        self,
        signal: Any,
        context: StrategyContext | None = None,
        selected_strategy_id: str | Any | None = None,
    ) -> ValidatedCandidate:
        """
        Validate signal structurally and contextually.
        Returns immutable ValidatedCandidate preserving audit evidence.
        """
        pass
