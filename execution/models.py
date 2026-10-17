from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from risk.models import RiskAssessment, RiskVerdict
from strategy.models import SignalDirection


class OrderType(str, Enum):
    """Supported order types for execution requests."""
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderState(str, Enum):
    """
    Explicit lifecycle states for order management.
    NOTE: In Sprint 29, states beyond VALIDATED are defined for contract completeness only;
    no actual order submission, acknowledgement, or execution takes place.
    """
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ExecutionStatus(str, Enum):
    """Status of an execution request evaluation."""
    NOT_EXECUTED = "NOT_EXECUTED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


class IllegalOrderStateTransitionError(ValueError):
    """Raised when an illegal order lifecycle transition is attempted."""
    pass


TERMINAL_STATES = frozenset({
    OrderState.FILLED,
    OrderState.REJECTED,
    OrderState.CANCELLED,
    OrderState.FAILED,
})

LEGAL_TRANSITIONS: Mapping[OrderState, frozenset[OrderState]] = MappingProxyType({
    OrderState.CREATED: frozenset({
        OrderState.VALIDATED,
        OrderState.REJECTED,
        OrderState.FAILED,
    }),
    OrderState.VALIDATED: frozenset({
        OrderState.SUBMITTED,
        OrderState.REJECTED,
        OrderState.FAILED,
    }),
    OrderState.SUBMITTED: frozenset({
        OrderState.ACKNOWLEDGED,
        OrderState.REJECTED,
        OrderState.FAILED,
        OrderState.UNKNOWN,
    }),
    OrderState.ACKNOWLEDGED: frozenset({
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.FAILED,
        OrderState.UNKNOWN,
    }),
    OrderState.PARTIALLY_FILLED: frozenset({
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.FAILED,
        OrderState.UNKNOWN,
    }),
    OrderState.FILLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.FAILED: frozenset(),
    OrderState.UNKNOWN: frozenset({
        OrderState.FAILED,
        OrderState.CANCELLED,
    }),
})


def is_valid_transition(from_state: OrderState, to_state: OrderState) -> bool:
    """Check whether a transition between two order lifecycle states is legal."""
    if not isinstance(from_state, OrderState) or not isinstance(to_state, OrderState):
        return False
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


def validate_transition(from_state: OrderState, to_state: OrderState) -> None:
    """
    Validate that an order lifecycle transition is legal.
    Raises IllegalOrderStateTransitionError if the transition is prohibited.
    """
    if not is_valid_transition(from_state, to_state):
        raise IllegalOrderStateTransitionError(
            f"Illegal order lifecycle transition from {from_state.value if isinstance(from_state, OrderState) else from_state} "
            f"to {to_state.value if isinstance(to_state, OrderState) else to_state}"
        )


@dataclass(frozen=True)
class ExecutionRequest:
    """
    Immutable structured execution request bound to an approved RiskAssessment.
    Guarantees provenance and prevents unauthorized order creation.
    """
    request_id: str
    idempotency_key: str
    symbol: str
    strategy_id: str
    direction: SignalDirection
    quantity: float
    notional: float
    reference_price: float
    risk_assessment: Any
    risk_policy_id: str = "deterministic_risk_v1"
    order_type: OrderType = OrderType.LIMIT
    requested_leverage: float = 1.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("ExecutionRequest request_id must be a non-empty string")

        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValueError("ExecutionRequest idempotency_key must be a non-empty string")

        if not isinstance(self.symbol, str):
            raise ValueError("ExecutionRequest symbol must be a string")

        if not isinstance(self.strategy_id, str):
            raise ValueError("ExecutionRequest strategy_id must be a string")

        if not isinstance(self.direction, SignalDirection):
            raise ValueError("ExecutionRequest direction must be an instance of SignalDirection")

        if not isinstance(self.quantity, (int, float)) or isinstance(self.quantity, bool):
            raise ValueError("ExecutionRequest quantity must be a float")

        if not isinstance(self.notional, (int, float)) or isinstance(self.notional, bool):
            raise ValueError("ExecutionRequest notional must be a float")

        if not isinstance(self.reference_price, (int, float)) or isinstance(self.reference_price, bool):
            raise ValueError("ExecutionRequest reference_price must be a float")

        if not isinstance(self.risk_policy_id, str) or not self.risk_policy_id.strip():
            raise ValueError("ExecutionRequest risk_policy_id must be a non-empty string")

        if not isinstance(self.order_type, OrderType):
            raise ValueError("ExecutionRequest order_type must be an instance of OrderType")

        if not isinstance(self.requested_leverage, (int, float)) or isinstance(self.requested_leverage, bool):
            raise ValueError("ExecutionRequest requested_leverage must be a float")

        if not isinstance(self.created_at, datetime):
            raise ValueError("ExecutionRequest created_at must be a datetime instance")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("ExecutionRequest metadata must be a mapping")

        object.__setattr__(self, "request_id", self.request_id.strip())
        object.__setattr__(self, "idempotency_key", self.idempotency_key.strip())
        object.__setattr__(self, "quantity", float(self.quantity))
        object.__setattr__(self, "notional", float(self.notional))
        object.__setattr__(self, "reference_price", float(self.reference_price))
        object.__setattr__(self, "risk_policy_id", self.risk_policy_id.strip())
        object.__setattr__(self, "requested_leverage", float(self.requested_leverage))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def canonical_payload(self) -> tuple[Any, ...]:
        """Produce an immutable canonical payload tuple for deterministic idempotency comparison."""
        return (
            self.symbol,
            self.strategy_id,
            self.direction.value,
            round(self.quantity, 8),
            round(self.notional, 4),
            round(self.reference_price, 4),
            self.order_type.value,
            round(self.requested_leverage, 4),
            self.risk_policy_id,
        )

    @classmethod
    def from_risk_assessment(
        cls,
        risk_assessment: RiskAssessment,
        request_id: str,
        idempotency_key: str,
        order_type: OrderType = OrderType.LIMIT,
        quantity: float | None = None,
        notional: float | None = None,
        reference_price: float | None = None,
        requested_leverage: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ExecutionRequest":
        """
        Factory method to construct an ExecutionRequest strictly bound to an approved RiskAssessment.
        Enforces approval status, provenance consistency, and limit compliance.
        """
        if not isinstance(risk_assessment, RiskAssessment):
            raise ValueError(f"risk_assessment must be a RiskAssessment, got {type(risk_assessment).__name__}")

        if not risk_assessment.is_passed:
            raise ValueError(
                f"Cannot create ExecutionRequest from non-passing RiskAssessment (verdict={risk_assessment.verdict.value})"
            )

        if risk_assessment.proposal is None:
            raise ValueError("risk_assessment must contain a valid TradeProposal")

        if risk_assessment.direction in (None, SignalDirection.FLAT):
            raise ValueError(f"Direction {risk_assessment.direction} is non-executable")

        proposal = risk_assessment.proposal
        req_qty = float(proposal.quantity if quantity is None else quantity)
        req_notional = float(risk_assessment.notional if notional is None else notional)
        req_ref_price = float(proposal.entry_price if reference_price is None else reference_price)
        req_leverage = float(proposal.requested_leverage if requested_leverage is None else requested_leverage)

        if req_qty <= 0.0:
            raise ValueError(f"Requested quantity must be strictly positive, got {req_qty}")
        if req_qty > proposal.quantity + 1e-9:
            raise ValueError(
                f"Requested quantity ({req_qty}) exceeds risk-approved proposal quantity ({proposal.quantity})"
            )

        if req_notional <= 0.0:
            raise ValueError(f"Requested notional must be strictly positive, got {req_notional}")
        if req_notional > risk_assessment.notional + 1e-9:
            raise ValueError(
                f"Requested notional ({req_notional}) exceeds risk-approved notional ({risk_assessment.notional})"
            )

        if req_ref_price <= 0.0:
            raise ValueError(f"Reference price must be strictly positive, got {req_ref_price}")

        return cls(
            request_id=request_id,
            idempotency_key=idempotency_key,
            symbol=risk_assessment.symbol,
            strategy_id=risk_assessment.strategy_id,
            direction=risk_assessment.direction,
            quantity=req_qty,
            notional=req_notional,
            reference_price=req_ref_price,
            risk_assessment=risk_assessment,
            risk_policy_id=risk_assessment.policy_id,
            order_type=order_type,
            requested_leverage=req_leverage,
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class ExecutionResult:
    """
    Immutable structured evaluation result returned by the Execution Engine.
    In Sprint 29, side effects and actual execution are strictly disabled.
    """
    status: ExecutionStatus
    order_state: OrderState
    request_id: str
    idempotency_key: str
    symbol: str
    strategy_id: str
    direction: SignalDirection | None
    requested_quantity: float
    requested_notional: float
    side_effect_occurred: bool = False
    execution_attempted: bool = False
    exchange_order_id: str | None = None
    filled_quantity: float = 0.0
    execution_price: float | None = None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    explanation: str = ""
    audit_evidence: Mapping[str, Any] = field(default_factory=dict)
    request: ExecutionRequest | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.status, ExecutionStatus):
            raise ValueError("ExecutionResult status must be an instance of ExecutionStatus")

        if not isinstance(self.order_state, OrderState):
            raise ValueError("ExecutionResult order_state must be an instance of OrderState")

        if not isinstance(self.request_id, str):
            raise ValueError("ExecutionResult request_id must be a string")

        if not isinstance(self.idempotency_key, str):
            raise ValueError("ExecutionResult idempotency_key must be a string")

        if not isinstance(self.symbol, str):
            raise ValueError("ExecutionResult symbol must be a string")

        if not isinstance(self.strategy_id, str):
            raise ValueError("ExecutionResult strategy_id must be a string")

        if self.direction is not None and not isinstance(self.direction, SignalDirection):
            raise ValueError("ExecutionResult direction must be an instance of SignalDirection or None")

        if not isinstance(self.requested_quantity, (int, float)) or isinstance(self.requested_quantity, bool):
            raise ValueError("ExecutionResult requested_quantity must be a float")

        if not isinstance(self.requested_notional, (int, float)) or isinstance(self.requested_notional, bool):
            raise ValueError("ExecutionResult requested_notional must be a float")

        # Safety Lock: Sprint 29 is strictly non-executing
        if self.side_effect_occurred or self.execution_attempted:
            raise ValueError("side_effect_occurred and execution_attempted must be False in Sprint 29")

        if self.exchange_order_id is not None or self.filled_quantity != 0.0 or self.execution_price is not None:
            raise ValueError("Fabricating exchange order IDs, fills, or execution prices is strictly prohibited")

        if not isinstance(self.reason_codes, (list, tuple)):
            raise ValueError("ExecutionResult reason_codes must be a sequence of strings")
        if not all(isinstance(rc, str) and rc for rc in self.reason_codes):
            raise ValueError("ExecutionResult reason_codes elements must be non-empty strings")

        if not isinstance(self.explanation, str):
            raise ValueError("ExecutionResult explanation must be a string")

        if not isinstance(self.audit_evidence, (dict, Mapping)):
            raise ValueError("ExecutionResult audit_evidence must be a mapping")

        if self.request is not None and not isinstance(self.request, ExecutionRequest):
            raise ValueError("ExecutionResult request must be an ExecutionRequest or None")

        if not isinstance(self.timestamp, datetime):
            raise ValueError("ExecutionResult timestamp must be a datetime instance")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("ExecutionResult metadata must be a mapping")

        object.__setattr__(self, "requested_quantity", float(self.requested_quantity))
        object.__setattr__(self, "requested_notional", float(self.requested_notional))
        object.__setattr__(self, "reason_codes", tuple(str(rc) for rc in self.reason_codes))
        object.__setattr__(self, "audit_evidence", MappingProxyType(dict(self.audit_evidence)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def is_executed(self) -> bool:
        """In Sprint 29, always returns False as execution is deferred."""
        return False

    @property
    def is_rejected(self) -> bool:
        """Returns True if request was rejected or in conflict."""
        return self.status in (ExecutionStatus.REJECTED, ExecutionStatus.CONFLICT)

    @property
    def is_duplicate(self) -> bool:
        """Returns True if request was an identical duplicate."""
        return self.status == ExecutionStatus.DUPLICATE
