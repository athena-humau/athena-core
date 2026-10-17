from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
import signal
import threading
import time
from typing import Any, Callable, Sequence

from config.settings import TRADING_MODE
from core.logger import log
from data.candle import Candle
from execution.paper_models import PaperPosition, PaperTrade
from execution.paper_workflow import CandidateWorkflowResult, PaperTradingWorkflow
from exchange.market_data import MarketData
from exchange.parser import BitgetParser


class RuntimeMode(str, Enum):
    """Supported operating modes for ATHENA application runtime."""
    ONE_SHOT = "ONE_SHOT"
    CONTINUOUS = "CONTINUOUS"


@dataclass(frozen=True)
class RuntimeConfig:
    """
    Configuration parameters governing continuous runtime scheduling, monitoring, and retries.
    """
    mode: RuntimeMode = RuntimeMode.ONE_SHOT
    scan_interval: float = 5.0
    position_monitor_interval: float = 1.0
    max_retries: int = 3
    retry_backoff: float = 1.0
    max_cycles: int | None = None
    max_duration_seconds: float | None = None

    def __post_init__(self):
        if not isinstance(self.mode, RuntimeMode):
            object.__setattr__(self, "mode", RuntimeMode(str(self.mode).upper()))

        if not isinstance(self.scan_interval, (int, float)) or self.scan_interval <= 0:
            raise ValueError(f"scan_interval must be a positive number (> 0), got {self.scan_interval}")

        if not isinstance(self.position_monitor_interval, (int, float)) or self.position_monitor_interval <= 0:
            raise ValueError(
                f"position_monitor_interval must be a positive number (> 0), got {self.position_monitor_interval}"
            )

        if not isinstance(self.max_retries, int) or self.max_retries < 0:
            raise ValueError(f"max_retries must be a non-negative integer (>= 0), got {self.max_retries}")

        if not isinstance(self.retry_backoff, (int, float)) or self.retry_backoff < 0:
            raise ValueError(f"retry_backoff must be a non-negative number (>= 0), got {self.retry_backoff}")

        if self.max_cycles is not None and (not isinstance(self.max_cycles, int) or self.max_cycles <= 0):
            raise ValueError(f"max_cycles must be a positive integer or None, got {self.max_cycles}")

        if self.max_duration_seconds is not None and (
            not isinstance(self.max_duration_seconds, (int, float)) or self.max_duration_seconds <= 0
        ):
            raise ValueError(
                f"max_duration_seconds must be a positive number or None, got {self.max_duration_seconds}"
            )


@dataclass
class RuntimeMetrics:
    """
    Runtime telemetry and execution counters capturing operational health.
    """
    cycles_completed: int = 0
    cycles_failed: int = 0
    monitor_checks_completed: int = 0
    monitor_checks_failed: int = 0
    positions_closed_by_sl: int = 0
    positions_closed_by_tp: int = 0
    positions_closed_total: int = 0
    total_candidates_scanned: int = 0
    total_orders_filled: int = 0
    start_time: datetime | None = None
    stop_time: datetime | None = None
    last_scan_duration: float = 0.0
    last_monitor_duration: float = 0.0
    is_running: bool = False
    exit_reason: str = "NONE"


class PositionMonitor:
    """
    Active position monitoring worker for open paper trading positions.

    Periodically retrieves active paper positions from the workflow, identifies
    unique symbols, fetches fresh public prices/candles, and triggers Stop-Loss
    and Take-Profit rules in the PaperTradingEngine.
    """

    def __init__(
        self,
        market: MarketData,
        workflow: PaperTradingWorkflow,
        lock: threading.RLock | None = None,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
    ):
        self.market = market
        self.workflow = workflow
        self.lock = lock or threading.RLock()
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    def check_open_positions(self) -> list[PaperTrade]:
        """
        Poll current prices for all symbols with open paper positions and process SL/TP rules.
        Returns a list of all PaperTrade objects resulting from triggered closures.
        """
        open_positions = self.workflow.get_open_positions()
        if not open_positions:
            return []

        # Identify unique symbols requiring price updates
        unique_symbols = sorted(list({pos.symbol for pos in open_positions}))
        closed_trades: list[PaperTrade] = []
        now_utc = datetime.now(timezone.utc)

        for symbol in unique_symbols:
            price = self._fetch_symbol_price_with_retry(symbol)
            if price is None:
                log(f"[POSITION MONITOR] Skipping price update for '{symbol}' due to data fetch failure")
                continue

            # Thread-safe evaluation and execution of protective exits
            with self.lock:
                try:
                    trades = self.workflow.update_market_price(
                        symbol=symbol,
                        current_price=price,
                        timestamp=now_utc,
                    )
                    if trades:
                        for trade in trades:
                            closed_trades.append(trade)
                            log(
                                f"[POSITION MONITOR] Protective Exit Triggered: {trade.symbol} | "
                                f"Reason={trade.exit_reason} | ExitPrice={trade.exit_price:.4f} | "
                                f"NetPnL={trade.net_pnl:.2f} USDT"
                            )
                except Exception as exc:
                    log(f"[POSITION MONITOR] Error updating market price for '{symbol}': {exc}")

        return closed_trades

    def check_candle(self, candle: Candle) -> list[PaperTrade]:
        """
        Evaluate an incoming candle against open paper positions under thread safety.
        """
        if not isinstance(candle, Candle):
            raise ValueError(f"Expected Candle instance, got {type(candle).__name__}")

        with self.lock:
            trades = self.workflow.process_candle(candle)
            if trades:
                for trade in trades:
                    log(
                        f"[POSITION MONITOR] Candle Protective Exit: {trade.symbol} | "
                        f"Reason={trade.exit_reason} | ExitPrice={trade.exit_price:.4f} | "
                        f"NetPnL={trade.net_pnl:.2f} USDT"
                    )
            return trades

    def _fetch_symbol_price_with_retry(self, symbol: str) -> float | None:
        """Fetch current ticker price from public market data with bounded retries."""
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.market.get_ticker(symbol)
                ticker = BitgetParser.parse_ticker(response)
                price = float(ticker.price)
                if not math.isfinite(price) or price <= 0.0:
                    raise ValueError(f"Received non-positive or non-finite price ({price}) for '{symbol}'")
                return price
            except Exception as exc:
                if attempt == self.max_retries:
                    log(
                        f"[POSITION MONITOR] Failed to fetch market price for '{symbol}' "
                        f"(attempt {attempt}/{self.max_retries}): {exc}"
                    )
                    return None
                time.sleep(self.retry_backoff * (0.5 ** (attempt - 1)))
        return None


class ContinuousRuntime:
    """
    Coordinated Continuous Runtime Orchestrator for ATHENA Paper Trading.

    Coordinates:
      1. Scan Worker: Executes repeated market scan & opportunity analysis on scan_interval.
      2. Position Monitor Worker: Polls open positions on position_monitor_interval.
      3. Thread Synchronization: Serializes ledger mutations via RLock to prevent race conditions.
      4. Responsive Shutdown: Event-driven wait interrupts sleep immediately upon SIGINT/SIGTERM.
      5. Simulation Lock: Strictly enforces TRADING_MODE == 'SIMULATION'.
    """

    def __init__(
        self,
        config: RuntimeConfig,
        scan_cycle_callback: Callable[[], list[CandidateWorkflowResult]],
        position_monitor_callback: Callable[[], list[PaperTrade]],
        cleanup_callback: Callable[[], None] | None = None,
        lock: threading.RLock | None = None,
        circuit_breaker_callback: Callable[[], bool] | None = None,
        open_positions_callback: Callable[[], Sequence[Any]] | None = None,
    ):
        self.config = config
        self.scan_cycle_callback = scan_cycle_callback
        self.position_monitor_callback = position_monitor_callback
        self.cleanup_callback = cleanup_callback
        self.lock = lock or threading.RLock()
        self.circuit_breaker_callback = circuit_breaker_callback
        self.open_positions_callback = open_positions_callback

        self._stop_event = threading.Event()
        self.metrics = RuntimeMetrics()

        self._scan_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._orig_sigint = None
        self._orig_sigterm = None

    @property
    def is_running(self) -> bool:
        return self.metrics.is_running and not self._stop_event.is_set()

    def start(self) -> None:
        """
        Validate safety locks, start continuous workers, and register signal handlers.
        """
        # Strict Paper Safety Check
        if TRADING_MODE != "SIMULATION":
            raise RuntimeError(
                f"CRITICAL SAFETY LOCK: TRADING_MODE is '{TRADING_MODE}', but continuous runtime strictly "
                "requires 'SIMULATION'. Live trading is prohibited."
            )

        if self.metrics.is_running:
            log("[RUNTIME] Continuous runtime is already running")
            return

        self._stop_event.clear()
        self.metrics = RuntimeMetrics(
            start_time=datetime.now(timezone.utc),
            is_running=True,
            exit_reason="RUNNING",
        )

        self._register_signal_handlers()

        # Start managed non-daemon worker threads
        self._monitor_thread = threading.Thread(
            target=self._run_position_monitor_loop,
            name="AthenaPositionMonitor",
            daemon=False,
        )
        self._scan_thread = threading.Thread(
            target=self._run_scan_loop,
            name="AthenaScanWorker",
            daemon=False,
        )

        log(
            f"[RUNTIME] Starting Continuous Paper Runtime | Mode={self.config.mode.value} | "
            f"ScanInterval={self.config.scan_interval}s | MonitorInterval={self.config.position_monitor_interval}s"
        )

        self._monitor_thread.start()
        self._scan_thread.start()

    def stop(self, reason: str = "USER_REQUEST") -> None:
        """
        Signal workers to stop, unblock sleeping threads, join workers, and run cleanup.
        """
        if not self.metrics.is_running and self.metrics.stop_time is not None:
            return

        log(f"[RUNTIME] Initiating Graceful Shutdown (Reason: {reason})...")
        self._stop_event.set()

        # Join workers with timeout to ensure complete termination
        curr_thread = threading.current_thread()
        if self._scan_thread and self._scan_thread.is_alive() and curr_thread != self._scan_thread:
            self._scan_thread.join(timeout=5.0)

        if self._monitor_thread and self._monitor_thread.is_alive() and curr_thread != self._monitor_thread:
            self._monitor_thread.join(timeout=5.0)

        # Execute safe cleanup
        if self.cleanup_callback:
            try:
                self.cleanup_callback()
            except Exception as exc:
                log(f"[RUNTIME] Error during cleanup callback: {exc}")

        self._restore_signal_handlers()

        self.metrics.is_running = False
        self.metrics.stop_time = datetime.now(timezone.utc)
        if self.metrics.exit_reason in ("NONE", "RUNNING") or reason != "USER_REQUEST":
            self.metrics.exit_reason = reason
        log(
            f"[RUNTIME] Shutdown Complete | CyclesCompleted={self.metrics.cycles_completed} | "
            f"PositionsClosed={self.metrics.positions_closed_total} | Reason={self.metrics.exit_reason}"
        )

    def wait_until_stopped(self, timeout: float | None = None) -> RuntimeMetrics:
        """
        Wait for runtime to terminate naturally, reach a deadline, or receive a stop signal.
        """
        deadline = None
        if self.config.max_duration_seconds is not None:
            deadline = time.time() + self.config.max_duration_seconds
        elif timeout is not None:
            deadline = time.time() + timeout

        try:
            while not self._stop_event.is_set():
                if deadline is not None and time.time() >= deadline:
                    self.stop(reason="DEADLINE_REACHED")
                    break
                # Responsive poll
                if self._stop_event.wait(timeout=0.05):
                    break
        except KeyboardInterrupt:
            self.stop(reason="KEYBOARD_INTERRUPT")

        # Ensure cleanup and metrics finalization if stopped via internal event (e.g. max_cycles)
        if self.metrics.is_running or self.metrics.stop_time is None:
            reason = self.metrics.exit_reason if self.metrics.exit_reason not in ("NONE", "RUNNING") else "STOPPED"
            self.stop(reason=reason)

        return self.metrics

    def run(self) -> RuntimeMetrics:
        """
        Synchronously start, monitor, and wait until runtime stops.
        """
        self.start()
        return self.wait_until_stopped()

    # -------------------------------------------------------------------------
    # Worker Loops
    # -------------------------------------------------------------------------

    def _run_scan_loop(self) -> None:
        """Worker executing market scan cycles on configured interval."""
        log("[SCAN WORKER] Active scan worker loop initialized")

        while not self._stop_event.is_set():
            # Check circuit breaker before starting scan cycle
            if self.circuit_breaker_callback is not None and self.circuit_breaker_callback():
                log("[SCAN WORKER] Portfolio circuit breaker is TRIGGERED. Halting scan worker.")
                break

            # Check cycle limit before starting
            if self.config.max_cycles is not None and self.metrics.cycles_completed >= self.config.max_cycles:
                self.metrics.exit_reason = "MAX_CYCLES_REACHED"
                self._stop_event.set()
                break

            cycle_start = time.time()
            try:
                log(f"[SCAN WORKER] Starting Scan Cycle #{self.metrics.cycles_completed + 1}...")
                results = self.scan_cycle_callback()
                self.metrics.cycles_completed += 1
                self.metrics.total_candidates_scanned += len(results)
                self.metrics.total_orders_filled += sum(1 for r in results if getattr(r, "is_filled", False))
                self.metrics.last_scan_duration = time.time() - cycle_start

                filled = sum(1 for r in results if getattr(r, "is_filled", False))
                rejected = sum(1 for r in results if getattr(r, "is_rejected", False))
                log(
                    f"[SCAN WORKER] Cycle #{self.metrics.cycles_completed} Complete | "
                    f"Scanned={len(results)} | Filled={filled} | Rejected={rejected} | "
                    f"Duration={self.metrics.last_scan_duration:.2f}s"
                )
            except Exception as exc:
                self.metrics.cycles_failed += 1
                self.metrics.last_scan_duration = time.time() - cycle_start
                log(f"[SCAN WORKER ERROR] Cycle failed with exception: {exc}")

            # Check circuit breaker after cycle completion
            if self.circuit_breaker_callback is not None and self.circuit_breaker_callback():
                log("[SCAN WORKER] Portfolio circuit breaker triggered after scan cycle. Halting scan worker.")
                break

            # Check cycle limit after completion
            if self.config.max_cycles is not None and self.metrics.cycles_completed >= self.config.max_cycles:
                self.metrics.exit_reason = "MAX_CYCLES_REACHED"
                self._stop_event.set()
                break

            # Responsive wait for next interval
            if self._stop_event.wait(timeout=self.config.scan_interval):
                break

        log("[SCAN WORKER] Scan worker terminated cleanly")

    def _run_position_monitor_loop(self) -> None:
        """Worker executing active position monitoring and SL/TP triggers."""
        log("[MONITOR WORKER] Active position monitor worker loop initialized")

        while not self._stop_event.is_set():
            cb_triggered = self.circuit_breaker_callback is not None and self.circuit_breaker_callback()
            open_pos = self.open_positions_callback() if self.open_positions_callback is not None else ()

            if cb_triggered and len(open_pos) == 0:
                log(
                    "[MONITOR WORKER] Circuit breaker is TRIGGERED and portfolio is flat (0 open positions). "
                    "Initiating controlled graceful shutdown."
                )
                self.metrics.exit_reason = "CIRCUIT_BREAKER_TRIGGERED"
                self._stop_event.set()
                break

            monitor_start = time.time()
            try:
                closed_trades = self.position_monitor_callback()
                self.metrics.monitor_checks_completed += 1
                self.metrics.last_monitor_duration = time.time() - monitor_start

                if closed_trades:
                    self.metrics.positions_closed_total += len(closed_trades)
                    for trade in closed_trades:
                        if "STOP_LOSS" in trade.exit_reason:
                            self.metrics.positions_closed_by_sl += 1
                        elif "TAKE_PROFIT" in trade.exit_reason:
                            self.metrics.positions_closed_by_tp += 1
            except Exception as exc:
                self.metrics.monitor_checks_failed += 1
                self.metrics.last_monitor_duration = time.time() - monitor_start
                log(f"[MONITOR WORKER ERROR] Position monitor check failed: {exc}")

            # Re-check after monitor check: if breaker is triggered and now all positions are closed, shutdown!
            if cb_triggered:
                open_pos_after = self.open_positions_callback() if self.open_positions_callback is not None else ()
                if len(open_pos_after) == 0:
                    log(
                        "[MONITOR WORKER] Circuit breaker is TRIGGERED and all positions closed (flat). "
                        "Initiating controlled graceful shutdown."
                    )
                    self.metrics.exit_reason = "CIRCUIT_BREAKER_TRIGGERED"
                    self._stop_event.set()
                    break

            # Responsive wait for next interval
            if self._stop_event.wait(timeout=self.config.position_monitor_interval):
                break

        log("[MONITOR WORKER] Position monitor worker terminated cleanly")

    # -------------------------------------------------------------------------
    # Signal Handling Helpers
    # -------------------------------------------------------------------------

    def _register_signal_handlers(self) -> None:
        """Register graceful shutdown signal handlers if executing in main thread."""
        if threading.current_thread() is threading.main_thread():
            try:
                def _sig_handler(signum, frame):
                    sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
                    log(f"[RUNTIME] Received {sig_name} signal")
                    self.stop(reason=f"SIGNAL_{sig_name}")

                self._orig_sigint = signal.signal(signal.SIGINT, _sig_handler)
                if hasattr(signal, "SIGTERM"):
                    self._orig_sigterm = signal.signal(signal.SIGTERM, _sig_handler)
            except (ValueError, AttributeError):
                pass

    def _restore_signal_handlers(self) -> None:
        """Restore previous signal handlers upon shutdown."""
        if threading.current_thread() is threading.main_thread():
            try:
                if self._orig_sigint is not None:
                    signal.signal(signal.SIGINT, self._orig_sigint)
                    self._orig_sigint = None
                if self._orig_sigterm is not None and hasattr(signal, "SIGTERM"):
                    signal.signal(signal.SIGTERM, self._orig_sigterm)
                    self._orig_sigterm = None
            except (ValueError, AttributeError):
                pass
