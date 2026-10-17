from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
import threading
from typing import Any

from config.settings import MAX_PORTFOLIO_DRAWDOWN_PCT
from core.logger import log


class CircuitBreakerState(str, Enum):
    """Lifecycle states for portfolio drawdown circuit breaker."""
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    RESET = "RESET"


@dataclass(frozen=True)
class CircuitBreakerStatus:
    """
    Immutable snapshot of the portfolio drawdown circuit breaker state.
    """
    state: CircuitBreakerState
    high_water_mark: float
    current_equity: float
    drawdown_pct: float
    threshold_pct: float
    is_triggered: bool
    triggered_at: datetime | None = None
    trigger_equity: float | None = None
    trigger_hwm: float | None = None
    trigger_reason: str | None = None
    session_id: str | None = None
    reset_at: datetime | None = None
    reset_reason: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert snapshot to dictionary for logging and audit serialization."""
        return {
            "state": self.state.value,
            "high_water_mark": self.high_water_mark,
            "current_equity": self.current_equity,
            "drawdown_pct": self.drawdown_pct,
            "threshold_pct": self.threshold_pct,
            "is_triggered": self.is_triggered,
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "trigger_equity": self.trigger_equity,
            "trigger_hwm": self.trigger_hwm,
            "trigger_reason": self.trigger_reason,
            "session_id": self.session_id,
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
            "reset_reason": self.reset_reason,
            "updated_at": self.updated_at.isoformat(),
        }


class PortfolioCircuitBreaker:
    """
    Persistent Portfolio Drawdown Circuit Breaker for ATHENA Paper Trading.

    Enforces maximum allowable portfolio drawdown calculated from the durable
    portfolio equity high-water mark (HWM):
      drawdown_fraction = (HWM - current_equity) / HWM
      drawdown_pct = drawdown_fraction * 100.0

    Trigger Rule:
      drawdown_pct >= threshold_pct (default: 20.0%)

    Safety & Durability Contracts:
      1. Hard Fail-Closed: Invalid equity, non-finite inputs, or persistence errors trigger the breaker.
      2. Durable Latching: Once triggered, the breaker latches. Restarts cannot clear it.
      3. Entry Blocker: Immediately blocks all new order creation and candidate processing.
      4. Safe Shutdown Support: Existing open positions continue to be monitored until flat.
      5. Explicit Operator Reset: Automatic re-arming or startup clearing is strictly prohibited.
    """

    def __init__(
        self,
        threshold_pct: float = MAX_PORTFOLIO_DRAWDOWN_PCT,
        initial_equity: float = 10000.0,
        storage: Any | None = None,
        session_id: str | None = None,
    ):
        if not isinstance(threshold_pct, (int, float)) or not math.isfinite(threshold_pct) or threshold_pct <= 0:
            raise ValueError(f"threshold_pct must be a strictly positive finite number, got {threshold_pct}")

        self.threshold_pct = float(threshold_pct)
        self.storage = storage
        self.session_id = session_id or f"session_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        self._lock = threading.RLock()
        self._state = CircuitBreakerState.ARMED
        self._high_water_mark = float(initial_equity) if math.isfinite(initial_equity) and initial_equity > 0 else 10000.0
        self._current_equity = self._high_water_mark
        self._drawdown_pct = 0.0
        self._is_triggered = False
        self._triggered_at: datetime | None = None
        self._trigger_equity: float | None = None
        self._trigger_hwm: float | None = None
        self._trigger_reason: str | None = None
        self._reset_at: datetime | None = None
        self._reset_reason: str | None = None
        self._updated_at = datetime.now(timezone.utc)

        # Restore from durable storage if available
        if self.storage is not None:
            self._restore_from_storage()

    @property
    def is_triggered(self) -> bool:
        with self._lock:
            return self._is_triggered

    @property
    def state(self) -> CircuitBreakerState:
        with self._lock:
            return self._state

    @property
    def high_water_mark(self) -> float:
        with self._lock:
            return self._high_water_mark

    @property
    def current_equity(self) -> float:
        with self._lock:
            return self._current_equity

    @property
    def drawdown_pct(self) -> float:
        with self._lock:
            return self._drawdown_pct

    def get_status(self) -> CircuitBreakerStatus:
        """Return an immutable snapshot of current breaker status."""
        with self._lock:
            return CircuitBreakerStatus(
                state=self._state,
                high_water_mark=self._high_water_mark,
                current_equity=self._current_equity,
                drawdown_pct=self._drawdown_pct,
                threshold_pct=self.threshold_pct,
                is_triggered=self._is_triggered,
                triggered_at=self._triggered_at,
                trigger_equity=self._trigger_equity,
                trigger_hwm=self._trigger_hwm,
                trigger_reason=self._trigger_reason,
                session_id=self.session_id,
                reset_at=self._reset_at,
                reset_reason=self._reset_reason,
                updated_at=self._updated_at,
            )

    def evaluate(self, current_equity: float, timestamp: datetime | None = None) -> CircuitBreakerStatus:
        """
        Evaluate current portfolio equity against the high-water mark and threshold.
        Thread-safe and fail-closed against non-finite or invalid numbers.
        """
        now_ts = timestamp or datetime.now(timezone.utc)

        with self._lock:
            # 1. Input Sanity & Fail-Closed Checks
            if not isinstance(current_equity, (int, float)) or not math.isfinite(current_equity):
                self._fail_closed(
                    reason=f"Invalid or non-finite current equity ({current_equity})",
                    timestamp=now_ts,
                )
                return self.get_status()

            if not math.isfinite(self._high_water_mark) or self._high_water_mark <= 0.0:
                self._fail_closed(
                    reason=f"Invalid or non-positive high-water mark ({self._high_water_mark})",
                    timestamp=now_ts,
                )
                return self.get_status()

            self._current_equity = float(current_equity)
            self._updated_at = now_ts

            # 2. If already latched in TRIGGERED state, do not clear or re-arm
            if self._is_triggered:
                # Recalculate drawdown for telemetry, but stay TRIGGERED
                if self._high_water_mark > 0:
                    dd_fraction = (self._high_water_mark - self._current_equity) / self._high_water_mark
                    self._drawdown_pct = max(0.0, dd_fraction * 100.0)
                self._persist_state()
                return self.get_status()

            # 3. High-Water Mark Expansion
            if self._current_equity > self._high_water_mark:
                old_hwm = self._high_water_mark
                self._high_water_mark = self._current_equity
                self._drawdown_pct = 0.0
                log(
                    f"[CIRCUIT BREAKER] New High-Water Mark Established: {self._high_water_mark:.2f} USDT "
                    f"(Previous: {old_hwm:.2f} USDT)"
                )
                self._persist_state()
                return self.get_status()

            # 4. Drawdown Calculation
            dd_fraction = (self._high_water_mark - self._current_equity) / self._high_water_mark
            self._drawdown_pct = max(0.0, dd_fraction * 100.0)

            # 5. Threshold Evaluation (>= threshold_pct triggers breaker)
            if self._drawdown_pct >= self.threshold_pct:
                self._trigger(
                    reason=(
                        f"Portfolio drawdown {self._drawdown_pct:.2f}% reached or exceeded "
                        f"threshold {self.threshold_pct:.2f}%"
                    ),
                    timestamp=now_ts,
                )
            else:
                self._persist_state()

            return self.get_status()

    def reset(self, operator: str, reason: str, timestamp: datetime | None = None) -> CircuitBreakerStatus:
        """
        Explicit, authorized operator reset of the circuit breaker latch.
        Requires explicit operator identification and audit rationale.
        Never called automatically on startup or equity rebound.
        """
        if not isinstance(operator, str) or not operator.strip():
            raise ValueError("Operator identification is required for circuit breaker reset")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Audit rationale reason is required for circuit breaker reset")

        now_ts = timestamp or datetime.now(timezone.utc)

        with self._lock:
            # Re-establish high-water mark to current equity upon authorized reset
            self._state = CircuitBreakerState.RESET
            self._is_triggered = False
            self._high_water_mark = max(self._current_equity, 0.0001)
            self._drawdown_pct = 0.0
            self._reset_at = now_ts
            self._reset_reason = f"Operator '{operator.strip()}': {reason.strip()}"
            self._updated_at = now_ts

            log(
                f"[CIRCUIT BREAKER RESET] Breaker latch cleared by Operator '{operator.strip()}'. "
                f"New HWM: {self._high_water_mark:.2f} USDT | Reason: {reason.strip()}"
            )

            self._persist_state()
            # Return to ARMED state ready for subsequent monitoring
            self._state = CircuitBreakerState.ARMED
            self._persist_state()

            return self.get_status()

    def _trigger(self, reason: str, timestamp: datetime) -> None:
        """Latch the circuit breaker into TRIGGERED state and persist immediately."""
        self._state = CircuitBreakerState.TRIGGERED
        self._is_triggered = True
        self._triggered_at = timestamp
        self._trigger_equity = self._current_equity
        self._trigger_hwm = self._high_water_mark
        self._trigger_reason = reason
        self._updated_at = timestamp

        log(
            f"[CIRCUIT BREAKER TRIGGERED] *** PORTFOLIO DRAWDOWN LIMIT REACHED *** | "
            f"Drawdown: {self._drawdown_pct:.2f}% >= {self.threshold_pct:.2f}% | "
            f"Current Equity: {self._current_equity:.2f} USDT | "
            f"High-Water Mark: {self._high_water_mark:.2f} USDT | "
            f"Reason: {reason}"
        )

        self._persist_state()

    def _fail_closed(self, reason: str, timestamp: datetime) -> None:
        """Emergency fail-closed state activation upon detecting invalid or corrupt accounting."""
        self._state = CircuitBreakerState.TRIGGERED
        self._is_triggered = True
        self._triggered_at = timestamp
        self._trigger_equity = self._current_equity if math.isfinite(self._current_equity) else -1.0
        self._trigger_hwm = self._high_water_mark if math.isfinite(self._high_water_mark) else -1.0
        self._trigger_reason = f"FAIL_CLOSED: {reason}"
        self._drawdown_pct = 100.0
        self._updated_at = timestamp

        log(f"[CIRCUIT BREAKER FAIL-CLOSED] Critical error: {reason}. Latching breaker to prevent entries.")
        self._persist_state()

    def _persist_state(self) -> None:
        """Persist current status to durable storage if available."""
        if self.storage is None:
            return

        status = self.get_status()
        try:
            if hasattr(self.storage, "save_circuit_breaker_state"):
                self.storage.save_circuit_breaker_state(status)
        except Exception as exc:
            log(f"[CIRCUIT BREAKER PERSISTENCE ERROR] Failed to save circuit breaker state: {exc}")
            # If persistence fails, latch in-memory to fail-closed
            self._state = CircuitBreakerState.TRIGGERED
            self._is_triggered = True
            self._trigger_reason = f"FAIL_CLOSED: Persistence failure ({exc})"

    def _restore_from_storage(self) -> None:
        """Restore circuit breaker state from durable SQLite storage during bootstrap."""
        try:
            if not hasattr(self.storage, "load_circuit_breaker_state"):
                return

            saved_status = self.storage.load_circuit_breaker_state()
            if saved_status is not None:
                self._state = saved_status.state
                self._high_water_mark = saved_status.high_water_mark
                self._current_equity = saved_status.current_equity
                self._drawdown_pct = saved_status.drawdown_pct
                self.threshold_pct = saved_status.threshold_pct
                self._is_triggered = saved_status.is_triggered
                self._triggered_at = saved_status.triggered_at
                self._trigger_equity = saved_status.trigger_equity
                self._trigger_hwm = saved_status.trigger_hwm
                self._trigger_reason = saved_status.trigger_reason
                self._reset_at = saved_status.reset_at
                self._reset_reason = saved_status.reset_reason
                self._updated_at = saved_status.updated_at

                if self._is_triggered:
                    log(
                        f"[CIRCUIT BREAKER RESTORE] Recovered LATCHED TRIGGERED state from database. "
                        f"Drawdown: {self._drawdown_pct:.2f}% | HWM: {self._high_water_mark:.2f} USDT | "
                        f"TriggeredAt: {self._triggered_at}"
                    )
                else:
                    log(
                        f"[CIRCUIT BREAKER RESTORE] Recovered ARMED state from database. "
                        f"HWM: {self._high_water_mark:.2f} USDT | Current Equity: {self._current_equity:.2f} USDT"
                    )
            else:
                # First initialization: persist initial ARMED status
                self._persist_state()
        except Exception as exc:
            log(f"[CIRCUIT BREAKER RESTORE ERROR] Failed to restore circuit breaker state: {exc}")
            self._fail_closed(
                reason=f"Recovery failure from storage: {exc}",
                timestamp=datetime.now(timezone.utc),
            )
