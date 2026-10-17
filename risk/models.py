from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from strategy.models import SignalDirection


class RiskVerdict(str, Enum):
    """
    Verdict issued by the ATHENA Risk Engine.
    NOTE: A PASS verdict indicates proposed trade parameters satisfy all configured
    account and exposure constraints. It does NOT authorize order execution or place trades.
    """
    PASS = "PASS"
    REJECT = "REJECT"


@dataclass(frozen=True)
class RiskPolicy:
    """
    Immutable risk policy configuration defining account, position, and portfolio constraints.
    """
    policy_id: str = "deterministic_risk_v1"
    max_risk_per_trade_pct: float = 2.0
    max_position_notional: float = 10000.0
    max_aggregate_exposure: float = 25000.0
    max_leverage: float = 10.0
    max_symbol_exposure: float | None = None
    min_risk_reward_ratio: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("RiskPolicy policy_id must be a non-empty string")

        if not isinstance(self.max_risk_per_trade_pct, (int, float)) or isinstance(self.max_risk_per_trade_pct, bool):
            raise ValueError("RiskPolicy max_risk_per_trade_pct must be a float")
        if not (0.0 < float(self.max_risk_per_trade_pct) <= 100.0):
            raise ValueError("RiskPolicy max_risk_per_trade_pct must be between 0.0 and 100.0 (exclusive 0)")

        if not isinstance(self.max_position_notional, (int, float)) or isinstance(self.max_position_notional, bool):
            raise ValueError("RiskPolicy max_position_notional must be a float")
        if float(self.max_position_notional) <= 0.0:
            raise ValueError("RiskPolicy max_position_notional must be strictly positive")

        if not isinstance(self.max_aggregate_exposure, (int, float)) or isinstance(self.max_aggregate_exposure, bool):
            raise ValueError("RiskPolicy max_aggregate_exposure must be a float")
        if float(self.max_aggregate_exposure) <= 0.0:
            raise ValueError("RiskPolicy max_aggregate_exposure must be strictly positive")

        if not isinstance(self.max_leverage, (int, float)) or isinstance(self.max_leverage, bool):
            raise ValueError("RiskPolicy max_leverage must be a float")
        if float(self.max_leverage) < 1.0:
            raise ValueError("RiskPolicy max_leverage must be at least 1.0")

        if self.max_symbol_exposure is not None:
            if not isinstance(self.max_symbol_exposure, (int, float)) or isinstance(self.max_symbol_exposure, bool):
                raise ValueError("RiskPolicy max_symbol_exposure must be a float or None")
            if float(self.max_symbol_exposure) <= 0.0:
                raise ValueError("RiskPolicy max_symbol_exposure must be strictly positive")

        if self.min_risk_reward_ratio is not None:
            if not isinstance(self.min_risk_reward_ratio, (int, float)) or isinstance(self.min_risk_reward_ratio, bool):
                raise ValueError("RiskPolicy min_risk_reward_ratio must be a float or None")
            if float(self.min_risk_reward_ratio) <= 0.0:
                raise ValueError("RiskPolicy min_risk_reward_ratio must be strictly positive")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("RiskPolicy metadata must be a mapping")

        object.__setattr__(self, "policy_id", self.policy_id.strip())
        object.__setattr__(self, "max_risk_per_trade_pct", float(self.max_risk_per_trade_pct))
        object.__setattr__(self, "max_position_notional", float(self.max_position_notional))
        object.__setattr__(self, "max_aggregate_exposure", float(self.max_aggregate_exposure))
        object.__setattr__(self, "max_leverage", float(self.max_leverage))
        if self.max_symbol_exposure is not None:
            object.__setattr__(self, "max_symbol_exposure", float(self.max_symbol_exposure))
        if self.min_risk_reward_ratio is not None:
            object.__setattr__(self, "min_risk_reward_ratio", float(self.min_risk_reward_ratio))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class TradeProposal:
    """
    Immutable representation of proposed trade parameters submitted to the Risk Engine.
    Pairs a JudgeDecision with pricing, sizing, and account context.
    """
    judge_decision: Any
    entry_price: float
    stop_loss_price: float
    quantity: float
    account_equity: float
    available_margin: float | None = None
    existing_exposure: float = 0.0
    existing_symbol_exposure: float = 0.0
    contract_multiplier: float = 1.0
    requested_leverage: float = 1.0
    proposal_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.entry_price, (int, float)) or isinstance(self.entry_price, bool):
            raise ValueError("TradeProposal entry_price must be a float")

        if not isinstance(self.stop_loss_price, (int, float)) or isinstance(self.stop_loss_price, bool):
            raise ValueError("TradeProposal stop_loss_price must be a float")

        if not isinstance(self.quantity, (int, float)) or isinstance(self.quantity, bool):
            raise ValueError("TradeProposal quantity must be a float")

        if not isinstance(self.account_equity, (int, float)) or isinstance(self.account_equity, bool):
            raise ValueError("TradeProposal account_equity must be a float")

        if self.available_margin is not None:
            if not isinstance(self.available_margin, (int, float)) or isinstance(self.available_margin, bool):
                raise ValueError("TradeProposal available_margin must be a float or None")

        if not isinstance(self.existing_exposure, (int, float)) or isinstance(self.existing_exposure, bool):
            raise ValueError("TradeProposal existing_exposure must be a float")

        if not isinstance(self.existing_symbol_exposure, (int, float)) or isinstance(self.existing_symbol_exposure, bool):
            raise ValueError("TradeProposal existing_symbol_exposure must be a float")

        if not isinstance(self.contract_multiplier, (int, float)) or isinstance(self.contract_multiplier, bool):
            raise ValueError("TradeProposal contract_multiplier must be a float")

        if not isinstance(self.requested_leverage, (int, float)) or isinstance(self.requested_leverage, bool):
            raise ValueError("TradeProposal requested_leverage must be a float")

        if not isinstance(self.proposal_id, str):
            raise ValueError("TradeProposal proposal_id must be a string")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("TradeProposal metadata must be a mapping")

        object.__setattr__(self, "entry_price", float(self.entry_price))
        object.__setattr__(self, "stop_loss_price", float(self.stop_loss_price))
        object.__setattr__(self, "quantity", float(self.quantity))
        object.__setattr__(self, "account_equity", float(self.account_equity))
        if self.available_margin is not None:
            object.__setattr__(self, "available_margin", float(self.available_margin))
        object.__setattr__(self, "existing_exposure", float(self.existing_exposure))
        object.__setattr__(self, "existing_symbol_exposure", float(self.existing_symbol_exposure))
        object.__setattr__(self, "contract_multiplier", float(self.contract_multiplier))
        object.__setattr__(self, "requested_leverage", float(self.requested_leverage))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def symbol(self) -> str:
        if self.judge_decision is not None and hasattr(self.judge_decision, "symbol"):
            return str(self.judge_decision.symbol)
        return ""

    @property
    def strategy_id(self) -> str:
        if self.judge_decision is not None and hasattr(self.judge_decision, "strategy_id"):
            return str(self.judge_decision.strategy_id)
        return ""

    @property
    def direction(self) -> SignalDirection | None:
        if self.judge_decision is not None and hasattr(self.judge_decision, "direction"):
            return self.judge_decision.direction
        return None


@dataclass(frozen=True)
class RiskAssessment:
    """
    Immutable structured evaluation output produced by the ATHENA Risk Engine.
    NOTE: A PASS assessment confirms that risk limits are satisfied. It does NOT authorize
    order placement, connect to execution APIs, or predict financial return.
    """
    verdict: RiskVerdict
    symbol: str
    strategy_id: str
    direction: SignalDirection | None
    notional: float
    estimated_loss: float
    risk_percent: float
    reason_codes: tuple[str, ...]
    explanation: str
    check_evidence: Mapping[str, Any] = field(default_factory=dict)
    proposal: TradeProposal | None = None
    policy_id: str = "deterministic_risk_v1"
    assessment_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.verdict, RiskVerdict):
            raise ValueError("RiskAssessment verdict must be an instance of RiskVerdict")

        if not isinstance(self.symbol, str):
            raise ValueError("RiskAssessment symbol must be a string")

        if not isinstance(self.strategy_id, str):
            raise ValueError("RiskAssessment strategy_id must be a string")

        if self.direction is not None and not isinstance(self.direction, SignalDirection):
            raise ValueError("RiskAssessment direction must be an instance of SignalDirection or None")

        if not isinstance(self.notional, (int, float)) or isinstance(self.notional, bool):
            raise ValueError("RiskAssessment notional must be a float")
        if float(self.notional) < 0.0:
            raise ValueError("RiskAssessment notional must be non-negative")

        if not isinstance(self.estimated_loss, (int, float)) or isinstance(self.estimated_loss, bool):
            raise ValueError("RiskAssessment estimated_loss must be a float")
        if float(self.estimated_loss) < 0.0:
            raise ValueError("RiskAssessment estimated_loss must be non-negative")

        if not isinstance(self.risk_percent, (int, float)) or isinstance(self.risk_percent, bool):
            raise ValueError("RiskAssessment risk_percent must be a float")
        if float(self.risk_percent) < 0.0:
            raise ValueError("RiskAssessment risk_percent must be non-negative")

        if not isinstance(self.reason_codes, (list, tuple)):
            raise ValueError("RiskAssessment reason_codes must be a sequence of strings")
        if not all(isinstance(rc, str) and rc for rc in self.reason_codes):
            raise ValueError("RiskAssessment reason_codes elements must be non-empty strings")

        if not isinstance(self.explanation, str):
            raise ValueError("RiskAssessment explanation must be a string")

        if not isinstance(self.check_evidence, (dict, Mapping)):
            raise ValueError("RiskAssessment check_evidence must be a mapping")

        if self.proposal is not None and not isinstance(self.proposal, TradeProposal):
            raise ValueError("RiskAssessment proposal must be a TradeProposal or None")

        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("RiskAssessment policy_id must be a non-empty string")

        if not isinstance(self.assessment_timestamp, datetime):
            raise ValueError("RiskAssessment assessment_timestamp must be a datetime instance")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("RiskAssessment metadata must be a mapping")

        object.__setattr__(self, "notional", float(self.notional))
        object.__setattr__(self, "estimated_loss", float(self.estimated_loss))
        object.__setattr__(self, "risk_percent", float(self.risk_percent))
        object.__setattr__(self, "reason_codes", tuple(str(rc) for rc in self.reason_codes))
        object.__setattr__(self, "check_evidence", MappingProxyType(dict(self.check_evidence)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def is_passed(self) -> bool:
        return self.verdict == RiskVerdict.PASS

    @property
    def is_rejected(self) -> bool:
        return self.verdict == RiskVerdict.REJECT
