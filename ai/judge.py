from abc import ABC, abstractmethod
from typing import Any

from ai.models import JudgeDecision, ValidatedCandidate
from market.analysis_models import MarketAnalysis
from strategy.context import StrategyContext


class JudgeEngine(ABC):
    """
    Abstract Base Class for Judge engines in ATHENA.
    Consumes a ValidatedCandidate and market context to produce an immutable JudgeDecision.
    Must NOT perform risk management, position sizing, order execution, or trade placement.
    """

    judge_id: str = ""
    policy_id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, judge_id: str | None = None, policy_id: str | None = None):
        if judge_id is not None:
            self.judge_id = judge_id
        if policy_id is not None:
            self.policy_id = policy_id

        if not isinstance(self.judge_id, str) or not self.judge_id:
            raise ValueError(f"Judge {self.__class__.__name__} must define a non-empty judge_id")
        if not isinstance(self.policy_id, str) or not self.policy_id:
            raise ValueError(f"Judge {self.__class__.__name__} must define a non-empty policy_id")

    @abstractmethod
    def judge(
        self,
        candidate: Any,
        context: StrategyContext | MarketAnalysis | None = None,
    ) -> JudgeDecision:
        """
        Evaluate candidate and market evidence to produce a deterministic JudgeDecision.
        """
        pass


# Backward-compatible alias
Judge = JudgeEngine
