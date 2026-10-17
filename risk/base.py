from abc import ABC, abstractmethod
from typing import Any

from risk.models import RiskAssessment


class RiskEngine(ABC):
    """
    Abstract Base Class for Risk Engines in ATHENA.
    Consumes a TradeProposal and evaluates account, position, stop-loss,
    and exposure constraints to return an immutable RiskAssessment.

    Must NOT place orders, connect to exchange execution endpoints,
    or perform trade execution.
    """

    engine_id: str = ""
    policy_id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, engine_id: str | None = None, policy_id: str | None = None):
        if engine_id is not None:
            self.engine_id = engine_id
        if policy_id is not None:
            self.policy_id = policy_id

        if not isinstance(self.engine_id, str) or not self.engine_id.strip():
            raise ValueError(f"RiskEngine {self.__class__.__name__} must define a non-empty engine_id")
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError(f"RiskEngine {self.__class__.__name__} must define a non-empty policy_id")

    @abstractmethod
    def assess(self, proposal: Any) -> RiskAssessment:
        """
        Evaluate proposal against configured risk rules to produce an immutable RiskAssessment.
        """
        pass


# Backward-compatible alias
BaseRiskEngine = RiskEngine
