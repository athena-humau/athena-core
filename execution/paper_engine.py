from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Mapping

from config.settings import MAX_PORTFOLIO_DRAWDOWN_PCT
from data.candle import Candle
from execution.base import ExecutionEngine
from execution.models import (
    ExecutionRequest,
    ExecutionStatus,
    OrderState,
    OrderType,
)
from execution.paper_models import (
    PaperExecutionResult,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    PaperTrade,
    validate_finite_number,
)
from execution.paper_persistence import PaperStorage
from risk.circuit_breaker import (
    CircuitBreakerState,
    CircuitBreakerStatus,
    PortfolioCircuitBreaker,
)
from risk.models import RiskAssessment, RiskVerdict
from strategy.models import SignalDirection


class PaperTradingEngine(ExecutionEngine):
    """
    Simulated Paper Trading Engine for ATHENA (Sprint 30 & 33).

    Operates under documented accounting and fill models.
    Supports in-memory mode or durable SQLite persistence with restart recovery.
    Does NOT connect to exchange endpoints or execute live orders.

    Accounting Model (Linear USDT-Margined Futures):
      Total Equity = Cash + Reserved Margin + Unrealized PnL
      Gross PnL (LONG) = (Exit Price - Entry Price) * Quantity * Multiplier
      Gross PnL (SHORT) = (Entry Price - Exit Price) * Quantity * Multiplier
      Net PnL = Gross PnL - Entry Fee - Exit Fee
    """

    engine_id: str = "paper_trading_engine"
    name: str = "ATHENA Paper Trading Engine"
    description: str = "Deterministic simulated trade execution, portfolio accounting, lifecycle manager, and persistence"

    def __init__(
        self,
        starting_cash: float = 10000.0,
        fee_rate: float = 0.0006,  # 0.06% default simulation fee assumption
        slippage_pct: float = 0.0,
        contract_multiplier: float = 1.0,
        engine_id: str | None = None,
        db_path: str | Path | None = None,
        storage: PaperStorage | None = None,
        max_drawdown_pct: float = MAX_PORTFOLIO_DRAWDOWN_PCT,
        circuit_breaker: PortfolioCircuitBreaker | None = None,
    ):
        super().__init__(engine_id=engine_id or self.engine_id)

        validate_finite_number(starting_cash, "starting_cash", strictly_positive=True)
        validate_finite_number(fee_rate, "fee_rate", non_negative=True)
        validate_finite_number(slippage_pct, "slippage_pct", non_negative=True)
        validate_finite_number(contract_multiplier, "contract_multiplier", strictly_positive=True)

        self._starting_equity = float(starting_cash)
        self.fee_rate = float(fee_rate)
        self.slippage_pct = float(slippage_pct)
        self.contract_multiplier = float(contract_multiplier)

        self._cash = float(starting_cash)
        self._reserved_margin = 0.0
        self._realized_pnl = 0.0
        self._fees_paid = 0.0

        self._positions: dict[str, PaperPosition] = {}
        self._trades: list[PaperTrade] = []
        self._orders: dict[str, PaperOrder] = {}

        self._idempotency_store: dict[str, tuple[Any, ...]] = {}
        self._idempotency_order_map: dict[str, str] = {}
        self._idempotency_pos_map: dict[str, str] = {}

        self._order_counter = 0
        self._position_counter = 0
        self._trade_counter = 0

        # Persistence integration
        if storage is not None:
            self.storage = storage
        elif db_path is not None:
            self.storage = PaperStorage(db_path=db_path)
        else:
            self.storage = None

        if self.storage is not None:
            self._bootstrap_persistence()

        # Circuit Breaker integration
        if circuit_breaker is not None:
            self.circuit_breaker = circuit_breaker
        else:
            initial_equity = self.get_portfolio_snapshot().total_equity
            self.circuit_breaker = PortfolioCircuitBreaker(
                threshold_pct=max_drawdown_pct,
                initial_equity=initial_equity,
                storage=self.storage,
            )
            self.circuit_breaker.evaluate(initial_equity)

    def _bootstrap_persistence(self) -> None:
        """Initialize database schema and restore existing ledger state or record initial state."""
        self.storage.initialize_schema()
        recovered = self.storage.load_ledger_state()

        if recovered is not None:
            # Restore state from durable storage without resetting to initial equity
            self._starting_equity = recovered.starting_equity
            self.fee_rate = recovered.fee_rate
            self.slippage_pct = recovered.slippage_pct
            self.contract_multiplier = recovered.contract_multiplier
            self._cash = recovered.cash
            self._realized_pnl = recovered.realized_pnl
            self._fees_paid = recovered.fees_paid
            self._order_counter = recovered.order_counter
            self._position_counter = recovered.position_counter
            self._trade_counter = recovered.trade_counter
            self._positions = dict(recovered.open_positions)
            self._trades = list(recovered.closed_trades)
            self._orders = dict(recovered.orders)
            self._idempotency_store = dict(recovered.idempotency_store)
            self._idempotency_order_map = dict(recovered.idempotency_order_map)
            self._idempotency_pos_map = dict(recovered.idempotency_pos_map)
            self._reserved_margin = sum(p.required_margin for p in self._positions.values())
        else:
            # First initialization of fresh database
            self.storage.save_portfolio_initialization(
                starting_equity=self._starting_equity,
                cash=self._cash,
                fee_rate=self.fee_rate,
                slippage_pct=self.slippage_pct,
                contract_multiplier=self.contract_multiplier,
            )

    def close(self) -> None:
        """Safely close active database connections if persistence is enabled."""
        if self.storage is not None:
            self.storage.close()

    def reset(self) -> None:
        """Reset the paper portfolio back to initial starting cash."""
        if self.storage is not None:
            raise RuntimeError(
                "Resetting a persistent paper portfolio is prohibited to prevent accidental state loss. "
                "Use an isolated test database or explicit purge method."
            )
        self._cash = self._starting_equity
        self._reserved_margin = 0.0
        self._realized_pnl = 0.0
        self._fees_paid = 0.0
        self._positions.clear()
        self._trades.clear()
        self._orders.clear()
        self._idempotency_store.clear()
        self._idempotency_order_map.clear()
        self._idempotency_pos_map.clear()
        self._order_counter = 0
        self._position_counter = 0
        self._trade_counter = 0
        if hasattr(self, "circuit_breaker") and self.circuit_breaker is not None:
            self.circuit_breaker = PortfolioCircuitBreaker(
                threshold_pct=self.circuit_breaker.threshold_pct,
                initial_equity=self._starting_equity,
                storage=None,
            )

    def get_portfolio_snapshot(self, timestamp: datetime | None = None) -> PaperPortfolio:
        """Generate an immutable snapshot of the current portfolio state."""
        total_unrealized_pnl = sum(p.unrealized_pnl for p in self._positions.values())
        open_notional = sum(p.notional for p in self._positions.values())
        reserved_margin = sum(p.required_margin for p in self._positions.values())
        total_equity = self._cash + reserved_margin + total_unrealized_pnl

        return PaperPortfolio(
            starting_equity=self._starting_equity,
            cash=self._cash,
            reserved_margin=reserved_margin,
            open_notional=open_notional,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=total_unrealized_pnl,
            fees_paid=self._fees_paid,
            total_equity=total_equity,
            open_positions=tuple(self._positions.values()),
            closed_trades=tuple(self._trades),
            is_paper=True,
            timestamp=timestamp or datetime.now(timezone.utc),
        )

    def get_open_positions(self) -> tuple[PaperPosition, ...]:
        """Return all active open paper positions."""
        return tuple(self._positions.values())

    def get_closed_trades(self) -> tuple[PaperTrade, ...]:
        """Return all closed paper trade records."""
        return tuple(self._trades)

    def get_order(self, order_id: str) -> PaperOrder | None:
        """Retrieve a paper order by its identifier."""
        return self._orders.get(order_id)

    def get_position(self, position_id: str) -> PaperPosition | None:
        """Retrieve an open paper position by its identifier."""
        return self._positions.get(position_id)

    def execute(self, request: Any, market_price: float | None = None) -> PaperExecutionResult:
        """
        Evaluate an ExecutionRequest and simulate order execution under the documented paper fill model.
        """
        snapshot_before = self.get_portfolio_snapshot()

        # 1. Structural checks
        if request is None:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("MISSING_EXECUTION_REQUEST",),
                explanation="Execution request is None",
                audit_evidence={"request": {"status": "FAIL", "reason": "Request is None"}},
                request=None,
            )

        if not isinstance(request, ExecutionRequest):
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("INVALID_REQUEST_TYPE",),
                explanation=f"Expected ExecutionRequest, got {type(request).__name__}",
                audit_evidence={"request": {"status": "FAIL", "reason": f"Expected ExecutionRequest, got {type(request).__name__}"}},
                request=None,
            )

        # 1b. Portfolio Drawdown Circuit Breaker check
        if self.circuit_breaker.is_triggered:
            cb_status = self.circuit_breaker.get_status()
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("CIRCUIT_BREAKER_ACTIVE",),
                explanation=(
                    f"Simulated order rejected: Portfolio drawdown circuit breaker is ACTIVE "
                    f"(Drawdown: {cb_status.drawdown_pct:.2f}% >= {cb_status.threshold_pct:.2f}%, "
                    f"HWM: {cb_status.high_water_mark:.2f} USDT, Equity: {cb_status.current_equity:.2f} USDT)"
                ),
                audit_evidence={
                    "circuit_breaker": {
                        "status": "FAIL",
                        "reason": "CIRCUIT_BREAKER_ACTIVE",
                        "drawdown_pct": cb_status.drawdown_pct,
                        "threshold_pct": cb_status.threshold_pct,
                        "high_water_mark": cb_status.high_water_mark,
                        "current_equity": cb_status.current_equity,
                    }
                },
                request=request,
            )

        # 2. Risk Provenance & Integrity checks
        risk_assessment = request.risk_assessment
        if risk_assessment is None:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("MISSING_RISK_ASSESSMENT",),
                explanation="Execution request lacks an associated RiskAssessment",
                audit_evidence={"provenance": {"status": "FAIL", "reason": "RiskAssessment is None"}},
                request=request,
            )

        if not isinstance(risk_assessment, RiskAssessment):
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("INVALID_RISK_ASSESSMENT_TYPE",),
                explanation=f"Expected RiskAssessment, got {type(risk_assessment).__name__}",
                audit_evidence={"provenance": {"status": "FAIL", "reason": f"Expected RiskAssessment, got {type(risk_assessment).__name__}"}},
                request=request,
            )

        if risk_assessment.verdict != RiskVerdict.PASS:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("RISK_VERDICT_REJECTED",),
                explanation=f"Associated RiskAssessment verdict was {risk_assessment.verdict.value}, not PASS",
                audit_evidence={"provenance": {"status": "FAIL", "verdict": risk_assessment.verdict.value}},
                request=request,
            )

        if risk_assessment.proposal is None:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("MISSING_RISK_PROPOSAL",),
                explanation="Associated RiskAssessment does not contain an approved TradeProposal",
                audit_evidence={"provenance": {"status": "FAIL", "reason": "TradeProposal is None"}},
                request=request,
            )

        # 3. Consistency checks
        if request.symbol != risk_assessment.symbol:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("SYMBOL_MISMATCH",),
                explanation=f"Request symbol '{request.symbol}' does not match RiskAssessment symbol '{risk_assessment.symbol}'",
                audit_evidence={"provenance": {"status": "FAIL", "request_symbol": request.symbol, "risk_symbol": risk_assessment.symbol}},
                request=request,
            )

        if request.strategy_id != risk_assessment.strategy_id:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("STRATEGY_MISMATCH",),
                explanation=f"Request strategy '{request.strategy_id}' does not match RiskAssessment strategy '{risk_assessment.strategy_id}'",
                audit_evidence={"provenance": {"status": "FAIL", "request_strategy": request.strategy_id, "risk_strategy": risk_assessment.strategy_id}},
                request=request,
            )

        if request.direction != risk_assessment.direction:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("DIRECTION_MISMATCH",),
                explanation=f"Request direction '{request.direction}' does not match RiskAssessment direction '{risk_assessment.direction}'",
                audit_evidence={"provenance": {"status": "FAIL", "request_dir": str(request.direction), "risk_dir": str(risk_assessment.direction)}},
                request=request,
            )

        if request.direction == SignalDirection.FLAT:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("FLAT_DIRECTION_NOT_EXECUTABLE",),
                explanation="FLAT signals are non-executable",
                audit_evidence={"direction": {"status": "FAIL", "direction": "FLAT"}},
                request=request,
            )

        if request.direction not in (SignalDirection.LONG, SignalDirection.SHORT):
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("INVALID_DIRECTION",),
                explanation=f"Invalid direction: {request.direction}",
                audit_evidence={"direction": {"status": "FAIL", "direction": str(request.direction)}},
                request=request,
            )

        # Sizing limits
        if request.quantity > risk_assessment.proposal.quantity + 1e-9:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("QUANTITY_EXCEEDS_RISK_LIMIT",),
                explanation=f"Requested quantity ({request.quantity}) exceeds risk-approved proposal ({risk_assessment.proposal.quantity})",
                audit_evidence={"limits": {"status": "FAIL", "requested_qty": request.quantity, "approved_qty": risk_assessment.proposal.quantity}},
                request=request,
            )

        if request.notional > risk_assessment.notional + 1e-9:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("NOTIONAL_EXCEEDS_RISK_LIMIT",),
                explanation=f"Requested notional ({request.notional}) exceeds risk-approved notional ({risk_assessment.notional})",
                audit_evidence={"limits": {"status": "FAIL", "requested_notional": request.notional, "approved_notional": risk_assessment.notional}},
                request=request,
            )

        if request.risk_policy_id != risk_assessment.policy_id:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("RISK_POLICY_MISMATCH",),
                explanation=f"Request risk policy '{request.risk_policy_id}' does not match RiskAssessment policy '{risk_assessment.policy_id}'",
                audit_evidence={"policy": {"status": "FAIL", "request_policy": request.risk_policy_id, "assessment_policy": risk_assessment.policy_id}},
                request=request,
            )

        # 4. Numeric validity
        try:
            raw_ref_price = market_price if market_price is not None else request.reference_price
            ref_price = validate_finite_number(raw_ref_price, "reference_price", strictly_positive=True)
            quantity = validate_finite_number(request.quantity, "quantity", strictly_positive=True)
        except ValueError as err:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("INVALID_QUANTITY_OR_PRICE",),
                explanation=str(err),
                audit_evidence={"pricing": {"status": "FAIL", "error": str(err)}},
                request=request,
            )

        # 5. Idempotency evaluation
        canonical = request.canonical_payload()
        key = request.idempotency_key

        if key in self._idempotency_store:
            stored_canonical = self._idempotency_store[key]
            if stored_canonical == canonical:
                existing_order_id = self._idempotency_order_map.get(key)
                existing_order = self._orders.get(existing_order_id) if existing_order_id else None
                existing_pos_id = self._idempotency_pos_map.get(key)
                existing_pos = self._positions.get(existing_pos_id) if existing_pos_id else None

                return PaperExecutionResult(
                    status=ExecutionStatus.DUPLICATE,
                    order_state=existing_order.state if existing_order else OrderState.VALIDATED,
                    order=existing_order,
                    position=existing_pos,
                    trade=None,
                    portfolio=snapshot_before,
                    reason_codes=("DUPLICATE_REQUEST_IGNORED",),
                    explanation=f"Duplicate request with idempotency key '{key}' recognized and ignored",
                    audit_evidence={"idempotency": {"status": "DUPLICATE", "key": key}},
                    request=request,
                )
            else:
                return PaperExecutionResult(
                    status=ExecutionStatus.CONFLICT,
                    order_state=OrderState.REJECTED,
                    order=None,
                    position=None,
                    trade=None,
                    portfolio=snapshot_before,
                    reason_codes=("IDEMPOTENCY_CONFLICT",),
                    explanation=f"Conflicting request content submitted with existing idempotency key '{key}'",
                    audit_evidence={"idempotency": {"status": "CONFLICT", "key": key}},
                    request=request,
                )

        # 6. Fill Model & Slippage calculation
        if request.direction == SignalDirection.LONG:
            fill_price = ref_price * (1.0 + self.slippage_pct)
        else:
            fill_price = ref_price * (1.0 - self.slippage_pct)

        trade_notional = fill_price * quantity * self.contract_multiplier
        leverage = max(float(request.requested_leverage), 1.0)
        required_margin = trade_notional / leverage
        entry_fee = trade_notional * self.fee_rate
        total_cash_required = required_margin + entry_fee

        # 7. Balance check (no partial or negative cash balance allowed)
        if total_cash_required > self._cash + 1e-9:
            return PaperExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                order=None,
                position=None,
                trade=None,
                portfolio=snapshot_before,
                reason_codes=("INSUFFICIENT_PAPER_CASH",),
                explanation=(
                    f"Insufficient paper cash: required {total_cash_required:.2f} USDT "
                    f"(margin {required_margin:.2f} + fee {entry_fee:.2f}), available {self._cash:.2f} USDT"
                ),
                audit_evidence={
                    "balance": {
                        "status": "FAIL",
                        "available_cash": self._cash,
                        "required_margin": required_margin,
                        "entry_fee": entry_fee,
                        "total_required": total_cash_required,
                    }
                },
                request=request,
            )

        # 8. Protective Levels (Stop Loss & Take Profit)
        proposal = risk_assessment.proposal
        stop_loss_price = proposal.stop_loss_price
        take_profit_price = None

        if "take_profit_price" in request.metadata:
            try:
                take_profit_price = validate_finite_number(
                    request.metadata["take_profit_price"], "take_profit_price", strictly_positive=True
                )
            except ValueError as err:
                return PaperExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    order_state=OrderState.REJECTED,
                    order=None,
                    position=None,
                    trade=None,
                    portfolio=snapshot_before,
                    reason_codes=("INVALID_PROTECTIVE_LEVELS",),
                    explanation=f"Invalid take-profit level: {err}",
                    audit_evidence={"protective_levels": {"status": "FAIL", "error": str(err)}},
                    request=request,
                )

        # Directional Stop-Loss / Take-Profit check
        if request.direction == SignalDirection.LONG:
            if stop_loss_price >= fill_price:
                return PaperExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    order_state=OrderState.REJECTED,
                    order=None,
                    position=None,
                    trade=None,
                    portfolio=snapshot_before,
                    reason_codes=("INVALID_PROTECTIVE_LEVELS",),
                    explanation=f"LONG stop_loss_price ({stop_loss_price}) must be strictly below fill price ({fill_price})",
                    audit_evidence={"protective_levels": {"status": "FAIL", "stop_loss": stop_loss_price, "fill_price": fill_price}},
                    request=request,
                )
            if take_profit_price is not None and take_profit_price <= fill_price:
                return PaperExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    order_state=OrderState.REJECTED,
                    order=None,
                    position=None,
                    trade=None,
                    portfolio=snapshot_before,
                    reason_codes=("INVALID_PROTECTIVE_LEVELS",),
                    explanation=f"LONG take_profit_price ({take_profit_price}) must be strictly above fill price ({fill_price})",
                    audit_evidence={"protective_levels": {"status": "FAIL", "take_profit": take_profit_price, "fill_price": fill_price}},
                    request=request,
                )
        elif request.direction == SignalDirection.SHORT:
            if stop_loss_price <= fill_price:
                return PaperExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    order_state=OrderState.REJECTED,
                    order=None,
                    position=None,
                    trade=None,
                    portfolio=snapshot_before,
                    reason_codes=("INVALID_PROTECTIVE_LEVELS",),
                    explanation=f"SHORT stop_loss_price ({stop_loss_price}) must be strictly above fill price ({fill_price})",
                    audit_evidence={"protective_levels": {"status": "FAIL", "stop_loss": stop_loss_price, "fill_price": fill_price}},
                    request=request,
                )
            if take_profit_price is not None and take_profit_price >= fill_price:
                return PaperExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    order_state=OrderState.REJECTED,
                    order=None,
                    position=None,
                    trade=None,
                    portfolio=snapshot_before,
                    reason_codes=("INVALID_PROTECTIVE_LEVELS",),
                    explanation=f"SHORT take_profit_price ({take_profit_price}) must be strictly below fill price ({fill_price})",
                    audit_evidence={"protective_levels": {"status": "FAIL", "take_profit": take_profit_price, "fill_price": fill_price}},
                    request=request,
                )

        # 9. Commit State Mutation (Atomic)
        next_order_counter = self._order_counter + 1
        next_pos_counter = self._position_counter + 1
        order_id = f"paper_ord_{next_order_counter:06d}"
        position_id = f"paper_pos_{next_pos_counter:06d}"
        now_utc = datetime.now(timezone.utc)

        order = PaperOrder(
            order_id=order_id,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            symbol=request.symbol,
            direction=request.direction,
            order_type=request.order_type,
            quantity=quantity,
            requested_price=ref_price,
            fill_price=fill_price,
            state=OrderState.FILLED,
            created_at=now_utc,
            filled_at=now_utc,
            fee=entry_fee,
            is_paper=True,
            metadata={"strategy_id": request.strategy_id},
        )

        position = PaperPosition(
            position_id=position_id,
            order_id=order_id,
            symbol=request.symbol,
            direction=request.direction,
            entry_price=fill_price,
            quantity=quantity,
            contract_multiplier=self.contract_multiplier,
            notional=trade_notional,
            required_margin=required_margin,
            leverage=leverage,
            entry_time=now_utc,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            entry_fee=entry_fee,
            unrealized_pnl=0.0,
            is_closed=False,
            is_paper=True,
            metadata={"strategy_id": request.strategy_id},
        )

        new_cash = self._cash - total_cash_required
        new_fees = self._fees_paid + entry_fee

        # Persist to durable storage first if persistence is enabled
        if self.storage is not None:
            try:
                self.storage.record_order_fill(
                    order=order,
                    position=position,
                    key=key,
                    canonical_payload=canonical,
                    cash=new_cash,
                    fees_paid=new_fees,
                    order_counter=next_order_counter,
                    position_counter=next_pos_counter,
                )
            except Exception as exc:
                # Transaction failed and rolled back; in-memory state remains untouched
                return PaperExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    order_state=OrderState.REJECTED,
                    order=None,
                    position=None,
                    trade=None,
                    portfolio=snapshot_before,
                    reason_codes=("PERSISTENCE_TRANSACTION_FAILED",),
                    explanation=f"Durable database write failed; transaction rolled back: {exc}",
                    audit_evidence={"persistence": {"status": "FAIL", "error": str(exc)}},
                    request=request,
                )

        # Apply in-memory state mutations only after durable commit succeeds
        self._order_counter = next_order_counter
        self._position_counter = next_pos_counter
        self._cash = new_cash
        self._reserved_margin += required_margin
        self._fees_paid = new_fees

        self._orders[order_id] = order
        self._positions[position_id] = position
        self._idempotency_store[key] = canonical
        self._idempotency_order_map[key] = order_id
        self._idempotency_pos_map[key] = position_id

        snapshot_after = self.get_portfolio_snapshot(timestamp=now_utc)
        self.circuit_breaker.evaluate(snapshot_after.total_equity, timestamp=now_utc)

        return PaperExecutionResult(
            status=ExecutionStatus.FILLED,
            order_state=OrderState.FILLED,
            order=order,
            position=position,
            trade=None,
            portfolio=snapshot_after,
            reason_codes=("SIMULATED_ORDER_FILLED",),
            explanation=f"Simulated {request.direction.value} order filled at {fill_price:.4f} USDT",
            audit_evidence={
                "fill": {
                    "status": "FILLED",
                    "fill_price": fill_price,
                    "reference_price": ref_price,
                    "slippage_pct": self.slippage_pct,
                    "fee": entry_fee,
                    "required_margin": required_margin,
                }
            },
            request=request,
            timestamp=now_utc,
        )

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        quantity: float | None = None,
        exit_reason: str = "MANUAL_CLOSE",
        timestamp: datetime | None = None,
    ) -> PaperTrade:
        """
        Close an open paper position (in full or partially) and realize PnL.
        """
        valid_exit_price = validate_finite_number(exit_price, "exit_price", strictly_positive=True)

        if position_id not in self._positions:
            raise KeyError(f"Position '{position_id}' not found among open paper positions")

        pos = self._positions[position_id]
        if pos.is_closed:
            raise ValueError(f"Position '{position_id}' is already closed")

        now_ts = timestamp or datetime.now(timezone.utc)

        # Determine quantity to close
        if quantity is None or quantity >= pos.quantity - 1e-9:
            close_qty = pos.quantity
            is_full_close = True
        else:
            close_qty = validate_finite_number(quantity, "quantity", strictly_positive=True)
            if close_qty > pos.quantity + 1e-9:
                raise ValueError(f"Cannot close {close_qty}: exceeds position quantity {pos.quantity}")
            is_full_close = False

        ratio = close_qty / pos.quantity
        released_margin = pos.required_margin * ratio
        entry_fee_portion = pos.entry_fee * ratio

        exit_notional = valid_exit_price * close_qty * pos.contract_multiplier
        exit_fee = exit_notional * self.fee_rate

        # Gross PnL
        if pos.direction == SignalDirection.LONG:
            gross_pnl = (valid_exit_price - pos.entry_price) * close_qty * pos.contract_multiplier
        else:
            gross_pnl = (pos.entry_price - valid_exit_price) * close_qty * pos.contract_multiplier

        net_pnl = gross_pnl - entry_fee_portion - exit_fee

        # Accounting mutation
        new_cash = self._cash + released_margin + gross_pnl - exit_fee
        new_realized_pnl = self._realized_pnl + gross_pnl
        new_fees = self._fees_paid + exit_fee
        next_trade_counter = self._trade_counter + 1
        trade_id = f"paper_trd_{next_trade_counter:06d}"

        trade = PaperTrade(
            trade_id=trade_id,
            position_id=pos.position_id,
            order_id=pos.order_id,
            symbol=pos.symbol,
            direction=pos.direction,
            strategy_id=pos.metadata.get("strategy_id", ""),
            entry_price=pos.entry_price,
            exit_price=valid_exit_price,
            quantity=close_qty,
            contract_multiplier=pos.contract_multiplier,
            entry_time=pos.entry_time,
            exit_time=now_ts,
            gross_pnl=gross_pnl,
            entry_fee=entry_fee_portion,
            exit_fee=exit_fee,
            net_pnl=net_pnl,
            exit_reason=exit_reason,
            is_paper=True,
        )

        updated_pos = None
        if not is_full_close:
            # Update remaining position state
            rem_qty = pos.quantity - close_qty
            rem_notional = pos.notional * (1.0 - ratio)
            rem_margin = pos.required_margin - released_margin
            rem_entry_fee = pos.entry_fee - entry_fee_portion

            updated_pos = PaperPosition(
                position_id=pos.position_id,
                order_id=pos.order_id,
                symbol=pos.symbol,
                direction=pos.direction,
                entry_price=pos.entry_price,
                quantity=rem_qty,
                contract_multiplier=pos.contract_multiplier,
                notional=rem_notional,
                required_margin=rem_margin,
                leverage=pos.leverage,
                entry_time=pos.entry_time,
                stop_loss_price=pos.stop_loss_price,
                take_profit_price=pos.take_profit_price,
                entry_fee=rem_entry_fee,
                unrealized_pnl=0.0,
                is_closed=False,
                is_paper=True,
                metadata=pos.metadata,
            )

        # Persist to durable storage first if persistence is enabled
        if self.storage is not None:
            self.storage.record_position_close(
                trade=trade,
                position=pos,
                is_full_close=is_full_close,
                cash=new_cash,
                realized_pnl=new_realized_pnl,
                fees_paid=new_fees,
                trade_counter=next_trade_counter,
                updated_position=updated_pos,
            )

        # Apply in-memory state mutations only after durable commit succeeds
        self._trade_counter = next_trade_counter
        self._cash = new_cash
        self._reserved_margin -= released_margin
        self._realized_pnl = new_realized_pnl
        self._fees_paid = new_fees
        self._trades.append(trade)

        if is_full_close:
            del self._positions[position_id]
        else:
            self._positions[position_id] = updated_pos

        snapshot_after = self.get_portfolio_snapshot(timestamp=now_ts)
        self.circuit_breaker.evaluate(snapshot_after.total_equity, timestamp=now_ts)

        return trade

    def update_market_price(
        self,
        symbol: str,
        current_price: float,
        timestamp: datetime | None = None,
    ) -> list[PaperTrade]:
        """
        Update unrealized PnL for active positions and evaluate protective triggers (Stop Loss / Take Profit).
        """
        valid_price = validate_finite_number(current_price, "current_price", strictly_positive=True)
        now_ts = timestamp or datetime.now(timezone.utc)
        closed_trades: list[PaperTrade] = []

        # Iterate over open positions for this symbol
        matching_pos_ids = [pid for pid, p in self._positions.items() if p.symbol == symbol]

        for pid in matching_pos_ids:
            if pid not in self._positions:
                continue
            pos = self._positions[pid]

            # Calculate unrealized PnL
            if pos.direction == SignalDirection.LONG:
                u_pnl = (valid_price - pos.entry_price) * pos.quantity * pos.contract_multiplier
                hit_sl = valid_price <= pos.stop_loss_price
                hit_tp = pos.take_profit_price is not None and valid_price >= pos.take_profit_price
            else:
                u_pnl = (pos.entry_price - valid_price) * pos.quantity * pos.contract_multiplier
                hit_sl = valid_price >= pos.stop_loss_price
                hit_tp = pos.take_profit_price is not None and valid_price <= pos.take_profit_price

            if hit_sl:
                trade = self.close_position(
                    position_id=pid,
                    exit_price=pos.stop_loss_price,
                    exit_reason="STOP_LOSS_TRIGGERED",
                    timestamp=now_ts,
                )
                closed_trades.append(trade)
            elif hit_tp and pos.take_profit_price is not None:
                trade = self.close_position(
                    position_id=pid,
                    exit_price=pos.take_profit_price,
                    exit_reason="TAKE_PROFIT_TRIGGERED",
                    timestamp=now_ts,
                )
                closed_trades.append(trade)
            else:
                # Update unrealized PnL
                updated_pos = PaperPosition(
                    position_id=pos.position_id,
                    order_id=pos.order_id,
                    symbol=pos.symbol,
                    direction=pos.direction,
                    entry_price=pos.entry_price,
                    quantity=pos.quantity,
                    contract_multiplier=pos.contract_multiplier,
                    notional=pos.notional,
                    required_margin=pos.required_margin,
                    leverage=pos.leverage,
                    entry_time=pos.entry_time,
                    stop_loss_price=pos.stop_loss_price,
                    take_profit_price=pos.take_profit_price,
                    entry_fee=pos.entry_fee,
                    unrealized_pnl=u_pnl,
                    is_closed=False,
                    is_paper=True,
                    metadata=pos.metadata,
                )
                self._positions[pid] = updated_pos

        snapshot_after = self.get_portfolio_snapshot(timestamp=now_ts)
        self.circuit_breaker.evaluate(snapshot_after.total_equity, timestamp=now_ts)

        return closed_trades

    def process_candle(self, candle: Candle) -> list[PaperTrade]:
        """
        Evaluate an incoming historical or live candle against open positions.
        Enforces chronological causality and documented conservative ambiguous-candle policy.
        """
        if not isinstance(candle, Candle):
            raise ValueError(f"Expected Candle instance, got {type(candle).__name__}")

        validate_finite_number(candle.open, "candle.open", strictly_positive=True)
        validate_finite_number(candle.high, "candle.high", strictly_positive=True)
        validate_finite_number(candle.low, "candle.low", strictly_positive=True)
        validate_finite_number(candle.close, "candle.close", strictly_positive=True)

        if candle.high < candle.low:
            raise ValueError(f"Candle high ({candle.high}) cannot be less than low ({candle.low})")

        closed_trades: list[PaperTrade] = []
        matching_pos_ids = [pid for pid, p in self._positions.items() if p.symbol == candle.symbol]

        for pid in matching_pos_ids:
            if pid not in self._positions:
                continue
            pos = self._positions[pid]

            if pos.direction == SignalDirection.LONG:
                touches_sl = candle.low <= pos.stop_loss_price
                touches_tp = pos.take_profit_price is not None and candle.high >= pos.take_profit_price

                # Ambiguous Candle Policy: If both SL and TP touched within the same candle,
                # the documented conservative rule assumes Stop Loss triggered first.
                if touches_sl and touches_tp:
                    trade = self.close_position(
                        position_id=pid,
                        exit_price=pos.stop_loss_price,
                        exit_reason="STOP_LOSS_CONSERVATIVE_AMBIGUOUS_CANDLE",
                        timestamp=candle.timestamp,
                    )
                    closed_trades.append(trade)
                elif touches_sl:
                    trade = self.close_position(
                        position_id=pid,
                        exit_price=pos.stop_loss_price,
                        exit_reason="STOP_LOSS_TRIGGERED",
                        timestamp=candle.timestamp,
                    )
                    closed_trades.append(trade)
                elif touches_tp and pos.take_profit_price is not None:
                    trade = self.close_position(
                        position_id=pid,
                        exit_price=pos.take_profit_price,
                        exit_reason="TAKE_PROFIT_TRIGGERED",
                        timestamp=candle.timestamp,
                    )
                    closed_trades.append(trade)
                else:
                    # Update mark with candle close
                    u_pnl = (candle.close - pos.entry_price) * pos.quantity * pos.contract_multiplier
                    updated_pos = PaperPosition(
                        position_id=pos.position_id,
                        order_id=pos.order_id,
                        symbol=pos.symbol,
                        direction=pos.direction,
                        entry_price=pos.entry_price,
                        quantity=pos.quantity,
                        contract_multiplier=pos.contract_multiplier,
                        notional=pos.notional,
                        required_margin=pos.required_margin,
                        leverage=pos.leverage,
                        entry_time=pos.entry_time,
                        stop_loss_price=pos.stop_loss_price,
                        take_profit_price=pos.take_profit_price,
                        entry_fee=pos.entry_fee,
                        unrealized_pnl=u_pnl,
                        is_closed=False,
                        is_paper=True,
                        metadata=pos.metadata,
                    )
                    self._positions[pid] = updated_pos

            elif pos.direction == SignalDirection.SHORT:
                touches_sl = candle.high >= pos.stop_loss_price
                touches_tp = pos.take_profit_price is not None and candle.low <= pos.take_profit_price

                if touches_sl and touches_tp:
                    trade = self.close_position(
                        position_id=pid,
                        exit_price=pos.stop_loss_price,
                        exit_reason="STOP_LOSS_CONSERVATIVE_AMBIGUOUS_CANDLE",
                        timestamp=candle.timestamp,
                    )
                    closed_trades.append(trade)
                elif touches_sl:
                    trade = self.close_position(
                        position_id=pid,
                        exit_price=pos.stop_loss_price,
                        exit_reason="STOP_LOSS_TRIGGERED",
                        timestamp=candle.timestamp,
                    )
                    closed_trades.append(trade)
                elif touches_tp and pos.take_profit_price is not None:
                    trade = self.close_position(
                        position_id=pid,
                        exit_price=pos.take_profit_price,
                        exit_reason="TAKE_PROFIT_TRIGGERED",
                        timestamp=candle.timestamp,
                    )
                    closed_trades.append(trade)
                else:
                    u_pnl = (pos.entry_price - candle.close) * pos.quantity * pos.contract_multiplier
                    updated_pos = PaperPosition(
                        position_id=pos.position_id,
                        order_id=pos.order_id,
                        symbol=pos.symbol,
                        direction=pos.direction,
                        entry_price=pos.entry_price,
                        quantity=pos.quantity,
                        contract_multiplier=pos.contract_multiplier,
                        notional=pos.notional,
                        required_margin=pos.required_margin,
                        leverage=pos.leverage,
                        entry_time=pos.entry_time,
                        stop_loss_price=pos.stop_loss_price,
                        take_profit_price=pos.take_profit_price,
                        entry_fee=pos.entry_fee,
                        unrealized_pnl=u_pnl,
                        is_closed=False,
                        is_paper=True,
                        metadata=pos.metadata,
                    )
                    self._positions[pid] = updated_pos

        snapshot_after = self.get_portfolio_snapshot(timestamp=candle.timestamp)
        self.circuit_breaker.evaluate(snapshot_after.total_equity, timestamp=candle.timestamp)

        return closed_trades
