"""
ATHENA Risk Engine Package.

Provides the foundational risk assessment contracts and reference deterministic engine.
Consumes passing JudgeDecisions and trade proposals, enforces account, stop-loss,
notional, leverage, and portfolio exposure limits, and issues an immutable RiskAssessment.

NOTE: This package strictly evaluates risk parameters. It does NOT authorize order execution,
place trades, or communicate with exchange private trading endpoints.
"""

from risk.base import BaseRiskEngine, RiskEngine
from risk.deterministic_engine import DeterministicRiskEngine
from risk.models import (
    RiskAssessment,
    RiskPolicy,
    RiskVerdict,
    TradeProposal,
)

__all__ = [
    "BaseRiskEngine",
    "DeterministicRiskEngine",
    "RiskAssessment",
    "RiskEngine",
    "RiskPolicy",
    "RiskVerdict",
    "TradeProposal",
]
