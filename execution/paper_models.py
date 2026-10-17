from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping

from execution.models import (
    ExecutionRequest,
    ExecutionStatus,
    OrderState,
    OrderType,
)
from strategy.models import SignalDirection


def validate_finite_number(value: Any, name: str, strictly_positive: bool = False, non_negative: bool = False) -> float:
    """Validate that a value is a finite number matching the given sign constraints."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a numeric float or int, got {type(value).__name__}")
    val = float(value)
    if not math.isfinite(val):
        raise ValueError(f"{name} must be a finite number, got {val}")
    if strictly_positive and val <= 0.0:
        raise ValueError(f"{name} must be strictly positive (> 0), got {val}")
    if non_negative and val < 0.0:
        raise ValueError(f"{name} must be non-negative (>= 0), got {val}")
    return val


@dataclass(frozen=True)
class PaperOrder:
    """
    Immutable representation of an order in the simulated paper trading environment.
    Strictly isolated from exchange order routing.
    """
    order_id: str
    request_id: str
    idempotency_key: str
    symbol: str
    direction: SignalDirection
    order_type: OrderType
    quantity: float
    requested_price: float
    fill_price: float
    state: OrderState
    created_at: datetime
    filled_at: datetime | None = None
    fee: float = 0.0
    is_paper: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("PaperOrder order_id must be a non-empty string")
        if not self.order_id.startswith("paper_ord_"):
            raise ValueError(f"PaperOrder order_id must carry 'paper_ord_' prefix, got '{self.order_id}'")

        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("PaperOrder request_id must be a non-empty string")

        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValueError("PaperOrder idempotency_key must be a non-empty string")

        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("PaperOrder symbol must be a non-empty string")

        if not isinstance(self.direction, SignalDirection):
            raise ValueError("PaperOrder direction must be an instance of SignalDirection")

        if not isinstance(self.order_type, OrderType):
            raise ValueError("PaperOrder order_type must be an instance of OrderType")

        validate_finite_number(self.quantity, "quantity", strictly_positive=True)
        validate_finite_number(self.requested_price, "requested_price", strictly_positive=True)
        validate_finite_number(self.fill_price, "fill_price", strictly_positive=True)
        validate_finite_number(self.fee, "fee", non_negative=True)

        if not isinstance(self.state, OrderState):
            raise ValueError("PaperOrder state must be an instance of OrderState")

        if not isinstance(self.created_at, datetime):
            raise ValueError("PaperOrder created_at must be a datetime instance")

        if self.filled_at is not None and not isinstance(self.filled_at, datetime):
            raise ValueError("PaperOrder filled_at must be a datetime instance or None")

        if not isinstance(self.is_paper, bool) or not self.is_paper:
            raise ValueError("PaperOrder is_paper must be strictly True")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("PaperOrder metadata must be a mapping")

        object.__setattr__(self, "quantity", float(self.quantity))
        object.__setattr__(self, "requested_price", float(self.requested_price))
        object.__setattr__(self, "fill_price", float(self.fill_price))
        object.__setattr__(self, "fee", float(self.fee))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class PaperPosition:
    """
    Immutable representation of an open simulated position.
    """
    position_id: str
    order_id: str
    symbol: str
    direction: SignalDirection
    entry_price: float
    quantity: float
    contract_multiplier: float
    notional: float
    required_margin: float
    leverage: float
    entry_time: datetime
    stop_loss_price: float
    take_profit_price: float | None = None
    entry_fee: float = 0.0
    unrealized_pnl: float = 0.0
    is_closed: bool = False
    is_paper: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.position_id, str) or not self.position_id.strip():
            raise ValueError("PaperPosition position_id must be a non-empty string")
        if not self.position_id.startswith("paper_pos_"):
            raise ValueError(f"PaperPosition position_id must carry 'paper_pos_' prefix, got '{self.position_id}'")

        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("PaperPosition order_id must be a non-empty string")

        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("PaperPosition symbol must be a non-empty string")

        if not isinstance(self.direction, SignalDirection):
            raise ValueError("PaperPosition direction must be an instance of SignalDirection")
        if self.direction not in (SignalDirection.LONG, SignalDirection.SHORT):
            raise ValueError(f"PaperPosition direction must be LONG or SHORT, got {self.direction}")

        validate_finite_number(self.entry_price, "entry_price", strictly_positive=True)
        validate_finite_number(self.quantity, "quantity", strictly_positive=True)
        validate_finite_number(self.contract_multiplier, "contract_multiplier", strictly_positive=True)
        validate_finite_number(self.notional, "notional", strictly_positive=True)
        validate_finite_number(self.required_margin, "required_margin", non_negative=True)
        validate_finite_number(self.leverage, "leverage", strictly_positive=True)
        validate_finite_number(self.stop_loss_price, "stop_loss_price", strictly_positive=True)
        if self.take_profit_price is not None:
            validate_finite_number(self.take_profit_price, "take_profit_price", strictly_positive=True)
        validate_finite_number(self.entry_fee, "entry_fee", non_negative=True)
        validate_finite_number(self.unrealized_pnl, "unrealized_pnl")

        # Directional Stop-Loss & Take-Profit checks
        if self.direction == SignalDirection.LONG:
            if self.stop_loss_price >= self.entry_price:
                raise ValueError(
                    f"LONG position stop_loss_price ({self.stop_loss_price}) must be strictly below entry ({self.entry_price})"
                )
            if self.take_profit_price is not None and self.take_profit_price <= self.entry_price:
                raise ValueError(
                    f"LONG position take_profit_price ({self.take_profit_price}) must be strictly above entry ({self.entry_price})"
                )
        elif self.direction == SignalDirection.SHORT:
            if self.stop_loss_price <= self.entry_price:
                raise ValueError(
                    f"SHORT position stop_loss_price ({self.stop_loss_price}) must be strictly above entry ({self.entry_price})"
                )
            if self.take_profit_price is not None and self.take_profit_price >= self.entry_price:
                raise ValueError(
                    f"SHORT position take_profit_price ({self.take_profit_price}) must be strictly below entry ({self.entry_price})"
                )

        if not isinstance(self.entry_time, datetime):
            raise ValueError("PaperPosition entry_time must be a datetime instance")

        if not isinstance(self.is_closed, bool):
            raise ValueError("PaperPosition is_closed must be a boolean")

        if not isinstance(self.is_paper, bool) or not self.is_paper:
            raise ValueError("PaperPosition is_paper must be strictly True")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("PaperPosition metadata must be a mapping")

        object.__setattr__(self, "entry_price", float(self.entry_price))
        object.__setattr__(self, "quantity", float(self.quantity))
        object.__setattr__(self, "contract_multiplier", float(self.contract_multiplier))
        object.__setattr__(self, "notional", float(self.notional))
        object.__setattr__(self, "required_margin", float(self.required_margin))
        object.__setattr__(self, "leverage", float(self.leverage))
        object.__setattr__(self, "stop_loss_price", float(self.stop_loss_price))
        if self.take_profit_price is not None:
            object.__setattr__(self, "take_profit_price", float(self.take_profit_price))
        object.__setattr__(self, "entry_fee", float(self.entry_fee))
        object.__setattr__(self, "unrealized_pnl", float(self.unrealized_pnl))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class PaperTrade:
    """
    Immutable representation of a closed paper trade.
    """
    trade_id: str
    position_id: str
    order_id: str
    symbol: str
    direction: SignalDirection
    strategy_id: str
    entry_price: float
    exit_price: float
    quantity: float
    contract_multiplier: float
    entry_time: datetime
    exit_time: datetime
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    net_pnl: float
    exit_reason: str
    is_paper: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.trade_id, str) or not self.trade_id.strip():
            raise ValueError("PaperTrade trade_id must be a non-empty string")
        if not self.trade_id.startswith("paper_trd_"):
            raise ValueError(f"PaperTrade trade_id must carry 'paper_trd_' prefix, got '{self.trade_id}'")

        if not isinstance(self.position_id, str) or not self.position_id.strip():
            raise ValueError("PaperTrade position_id must be a non-empty string")

        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("PaperTrade order_id must be a non-empty string")

        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("PaperTrade symbol must be a non-empty string")

        if not isinstance(self.direction, SignalDirection):
            raise ValueError("PaperTrade direction must be an instance of SignalDirection")

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError("PaperTrade strategy_id must be a non-empty string")

        validate_finite_number(self.entry_price, "entry_price", strictly_positive=True)
        validate_finite_number(self.exit_price, "exit_price", strictly_positive=True)
        validate_finite_number(self.quantity, "quantity", strictly_positive=True)
        validate_finite_number(self.contract_multiplier, "contract_multiplier", strictly_positive=True)
        validate_finite_number(self.gross_pnl, "gross_pnl")
        validate_finite_number(self.entry_fee, "entry_fee", non_negative=True)
        validate_finite_number(self.exit_fee, "exit_fee", non_negative=True)
        validate_finite_number(self.net_pnl, "net_pnl")

        if not isinstance(self.entry_time, datetime):
            raise ValueError("PaperTrade entry_time must be a datetime instance")

        if not isinstance(self.exit_time, datetime):
            raise ValueError("PaperTrade exit_time must be a datetime instance")

        if not isinstance(self.exit_reason, str) or not self.exit_reason.strip():
            raise ValueError("PaperTrade exit_reason must be a non-empty string")

        if not isinstance(self.is_paper, bool) or not self.is_paper:
            raise ValueError("PaperTrade is_paper must be strictly True")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("PaperTrade metadata must be a mapping")

        object.__setattr__(self, "entry_price", float(self.entry_price))
        object.__setattr__(self, "exit_price", float(self.exit_price))
        object.__setattr__(self, "quantity", float(self.quantity))
        object.__setattr__(self, "contract_multiplier", float(self.contract_multiplier))
        object.__setattr__(self, "gross_pnl", float(self.gross_pnl))
        object.__setattr__(self, "entry_fee", float(self.entry_fee))
        object.__setattr__(self, "exit_fee", float(self.exit_fee))
        object.__setattr__(self, "net_pnl", float(self.net_pnl))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class PaperPortfolio:
    """
    Immutable snapshot of the simulated paper portfolio state.
    Satisfies accounting identity:
      total_equity == cash + reserved_margin + unrealized_pnl
    """
    starting_equity: float
    cash: float
    reserved_margin: float
    open_notional: float
    realized_pnl: float
    unrealized_pnl: float
    fees_paid: float
    total_equity: float
    open_positions: tuple[PaperPosition, ...] = field(default_factory=tuple)
    closed_trades: tuple[PaperTrade, ...] = field(default_factory=tuple)
    is_paper: bool = True
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        validate_finite_number(self.starting_equity, "starting_equity", strictly_positive=True)
        validate_finite_number(self.cash, "cash")
        validate_finite_number(self.reserved_margin, "reserved_margin", non_negative=True)
        validate_finite_number(self.open_notional, "open_notional", non_negative=True)
        validate_finite_number(self.realized_pnl, "realized_pnl")
        validate_finite_number(self.unrealized_pnl, "unrealized_pnl")
        validate_finite_number(self.fees_paid, "fees_paid", non_negative=True)
        validate_finite_number(self.total_equity, "total_equity")

        if not isinstance(self.open_positions, (list, tuple)):
            raise ValueError("open_positions must be a sequence of PaperPosition")
        if not all(isinstance(p, PaperPosition) for p in self.open_positions):
            raise ValueError("open_positions elements must be PaperPosition instances")

        if not isinstance(self.closed_trades, (list, tuple)):
            raise ValueError("closed_trades must be a sequence of PaperTrade")
        if not all(isinstance(t, PaperTrade) for t in self.closed_trades):
            raise ValueError("closed_trades elements must be PaperTrade instances")

        if not isinstance(self.is_paper, bool) or not self.is_paper:
            raise ValueError("PaperPortfolio is_paper must be strictly True")

        if not isinstance(self.timestamp, datetime):
            raise ValueError("PaperPortfolio timestamp must be a datetime instance")

        object.__setattr__(self, "starting_equity", float(self.starting_equity))
        object.__setattr__(self, "cash", float(self.cash))
        object.__setattr__(self, "reserved_margin", float(self.reserved_margin))
        object.__setattr__(self, "open_notional", float(self.open_notional))
        object.__setattr__(self, "realized_pnl", float(self.realized_pnl))
        object.__setattr__(self, "unrealized_pnl", float(self.unrealized_pnl))
        object.__setattr__(self, "fees_paid", float(self.fees_paid))
        object.__setattr__(self, "total_equity", float(self.total_equity))
        object.__setattr__(self, "open_positions", tuple(self.open_positions))
        object.__setattr__(self, "closed_trades", tuple(self.closed_trades))


@dataclass(frozen=True)
class PaperExecutionResult:
    """
    Structured outcome of a paper execution evaluation.
    Clearly indicates simulation origin.
    """
    status: ExecutionStatus
    order_state: OrderState
    order: PaperOrder | None
    position: PaperPosition | None
    trade: PaperTrade | None
    portfolio: PaperPortfolio
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    explanation: str = ""
    audit_evidence: Mapping[str, Any] = field(default_factory=dict)
    is_paper: bool = True
    request: ExecutionRequest | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if not isinstance(self.status, ExecutionStatus):
            raise ValueError("PaperExecutionResult status must be an instance of ExecutionStatus")

        if not isinstance(self.order_state, OrderState):
            raise ValueError("PaperExecutionResult order_state must be an instance of OrderState")

        if self.order is not None and not isinstance(self.order, PaperOrder):
            raise ValueError("PaperExecutionResult order must be PaperOrder or None")

        if self.position is not None and not isinstance(self.position, PaperPosition):
            raise ValueError("PaperExecutionResult position must be PaperPosition or None")

        if self.trade is not None and not isinstance(self.trade, PaperTrade):
            raise ValueError("PaperExecutionResult trade must be PaperTrade or None")

        if not isinstance(self.portfolio, PaperPortfolio):
            raise ValueError("PaperExecutionResult portfolio must be a PaperPortfolio instance")

        if not isinstance(self.reason_codes, (list, tuple)):
            raise ValueError("PaperExecutionResult reason_codes must be a sequence of strings")
        if not all(isinstance(rc, str) and rc for rc in self.reason_codes):
            raise ValueError("PaperExecutionResult reason_codes elements must be non-empty strings")

        if not isinstance(self.explanation, str):
            raise ValueError("PaperExecutionResult explanation must be a string")

        if not isinstance(self.audit_evidence, (dict, Mapping)):
            raise ValueError("PaperExecutionResult audit_evidence must be a mapping")

        if not isinstance(self.is_paper, bool) or not self.is_paper:
            raise ValueError("PaperExecutionResult is_paper must be strictly True")

        if self.request is not None and not isinstance(self.request, ExecutionRequest):
            raise ValueError("PaperExecutionResult request must be ExecutionRequest or None")

        if not isinstance(self.timestamp, datetime):
            raise ValueError("PaperExecutionResult timestamp must be a datetime instance")

        object.__setattr__(self, "reason_codes", tuple(str(rc) for rc in self.reason_codes))
        object.__setattr__(self, "audit_evidence", MappingProxyType(dict(self.audit_evidence)))

    @property
    def is_executed(self) -> bool:
        """True if the paper order was simulated as filled."""
        return self.order_state == OrderState.FILLED

    @property
    def is_rejected(self) -> bool:
        """True if the request was rejected or in conflict."""
        return self.status in (ExecutionStatus.REJECTED, ExecutionStatus.CONFLICT)

    @property
    def is_duplicate(self) -> bool:
        """True if the request was an identical duplicate."""
        return self.status == ExecutionStatus.DUPLICATE
