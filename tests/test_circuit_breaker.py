from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from ai.models import (
    JudgeDecision,
    JudgeVerdict,
    ValidatedCandidate,
    ValidationStatus,
)
from config.settings import MAX_PORTFOLIO_DRAWDOWN_PCT
from core.runtime import ContinuousRuntime, RuntimeConfig, RuntimeMode
from data.candle import Candle
from execution.models import (
    ExecutionRequest,
    ExecutionStatus,
    OrderState,
    OrderType,
)
from execution.paper_engine import PaperTradingEngine
from execution.paper_models import (
    PaperExecutionResult,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    PaperTrade,
)
from execution.paper_persistence import PaperStorage
from execution.paper_workflow import (
    CandidateProcessingStatus,
    CandidateWorkflowResult,
    PaperTradingWorkflow,
)
from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from regime.models import Regime, TimeframeRegime
from risk.circuit_breaker import (
    CircuitBreakerState,
    CircuitBreakerStatus,
    PortfolioCircuitBreaker,
)
from risk.deterministic_engine import DeterministicRiskEngine
from risk.models import (
    RiskAssessment,
    RiskPolicy,
    RiskVerdict,
    TradeProposal,
)
from scanner.ranking_engine import RankedMarket
from strategy.models import Signal, SignalDirection


def make_test_passing_risk(
    symbol: str = "BTCUSDT",
    strategy_id: str = "ema_trend_following",
    direction: SignalDirection = SignalDirection.LONG,
    entry_price: float = 50000.0,
    stop_loss_price: float = 49000.0,
    quantity: float = 0.1,
    equity: float = 10000.0,
    policy_id: str = "default_linear_paper_policy",
) -> RiskAssessment:
    sig = Signal(
        symbol=symbol,
        direction=direction,
        strategy_id=strategy_id,
        timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
        confidence=0.85,
        reason="Test candidate signal",
    )
    cand = ValidatedCandidate(
        status=ValidationStatus.PASSED,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=0.85,
        signal=sig,
        failures=(),
    )
    judge = JudgeDecision(
        verdict=JudgeVerdict.PASS,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=0.85,
        score=0.80,
        reason_codes=("CRITERIA_SATISFIED",),
        explanation="Judge passed",
        candidate=cand,
    )
    proposal = TradeProposal(
        judge_decision=judge,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        quantity=quantity,
        account_equity=equity,
        available_margin=5000.0,
        requested_leverage=1.0,
    )
    policy = RiskPolicy(
        policy_id=policy_id,
        max_risk_per_trade_pct=2.0,
        max_position_notional=10000.0,
        max_aggregate_exposure=25000.0,
        max_leverage=10.0,
    )
    risk_engine = DeterministicRiskEngine(policy=policy)
    return risk_engine.assess(proposal)


def make_test_analysis(symbol: str = "BTCUSDT", last_close: float = 50000.0) -> MarketAnalysis:
    now_ts = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    tf_regime = TimeframeRegime(
        symbol=symbol,
        granularity="1H",
        timestamp=now_ts,
        candle_count=50,
        regime=Regime.BULLISH,
    )
    feature_vals = {
        "last_close": last_close,
        "ema_9": last_close * 0.98,
        "ema_21": last_close * 0.95,
        "rsi_14": 55.0,
        "atr_14": 500.0,
    }
    tf_features = TimeframeFeatures(
        symbol=symbol,
        granularity="1H",
        timestamp=now_ts,
        candle_count=50,
        values=feature_vals,
    )
    test_candle = Candle(
        symbol=symbol,
        timestamp=now_ts,
        open=last_close * 0.995,
        high=last_close * 1.005,
        low=last_close * 0.990,
        close=last_close,
        volume=100.0,
        quote_volume=100.0 * last_close,
        granularity="1H",
    )
    ranked = RankedMarket(
        symbol=symbol,
        score=88.0,
        liquidity_score=92.0,
        movement_score=85.0,
        spread_score=90.0,
        open_interest_score=80.0,
        funding_score=95.0,
    )
    return MarketAnalysis(
        symbol=symbol,
        ranking=ranked,
        candles_by_granularity={"1H": [test_candle]},
        features_by_granularity={"1H": tf_features},
        regimes_by_granularity={"1H": tf_regime},
        success=True,
        analyzed_at=now_ts,
    )


class TestPortfolioCircuitBreaker(unittest.TestCase):
    """
    Comprehensive unit and integration test suite for the Persistent Portfolio
    Drawdown Circuit Breaker in ATHENA (Sprint 35).
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_cb.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. State & Snapshot Contracts
    # -------------------------------------------------------------------------

    def test_01_initial_state_defaults(self):
        """Circuit breaker initializes in ARMED state with 0% drawdown and 20% default threshold."""
        cb = PortfolioCircuitBreaker(initial_equity=10000.0)
        status = cb.get_status()

        self.assertEqual(status.state, CircuitBreakerState.ARMED)
        self.assertEqual(status.high_water_mark, 10000.0)
        self.assertEqual(status.current_equity, 10000.0)
        self.assertEqual(status.drawdown_pct, 0.0)
        self.assertEqual(status.threshold_pct, MAX_PORTFOLIO_DRAWDOWN_PCT)
        self.assertFalse(status.is_triggered)
        self.assertFalse(cb.is_triggered)
        self.assertIsNone(status.triggered_at)

        status_dict = status.to_dict()
        self.assertEqual(status_dict["state"], "ARMED")
        self.assertEqual(status_dict["high_water_mark"], 10000.0)
        self.assertFalse(status_dict["is_triggered"])

    def test_02_invalid_threshold_pct(self):
        """Constructing breaker with invalid or non-positive threshold raises ValueError."""
        with self.assertRaises(ValueError):
            PortfolioCircuitBreaker(threshold_pct=0.0)
        with self.assertRaises(ValueError):
            PortfolioCircuitBreaker(threshold_pct=-5.0)
        with self.assertRaises(ValueError):
            PortfolioCircuitBreaker(threshold_pct=float("nan"))
        with self.assertRaises(ValueError):
            PortfolioCircuitBreaker(threshold_pct=float("inf"))

    # -------------------------------------------------------------------------
    # 2. High-Water Mark Expansion & Drawdown Accounting
    # -------------------------------------------------------------------------

    def test_03_high_water_mark_expansion(self):
        """Equity increases raise the high-water mark and maintain 0% drawdown."""
        cb = PortfolioCircuitBreaker(initial_equity=10000.0)

        s1 = cb.evaluate(10500.0)
        self.assertEqual(s1.high_water_mark, 10500.0)
        self.assertEqual(s1.current_equity, 10500.0)
        self.assertEqual(s1.drawdown_pct, 0.0)
        self.assertFalse(s1.is_triggered)

        s2 = cb.evaluate(12000.0)
        self.assertEqual(s2.high_water_mark, 12000.0)
        self.assertEqual(s2.drawdown_pct, 0.0)

        # Drawdown from new HWM
        s3 = cb.evaluate(10800.0)  # (12000 - 10800) / 12000 = 1200 / 12000 = 10.0%
        self.assertEqual(s3.high_water_mark, 12000.0)
        self.assertEqual(s3.current_equity, 10800.0)
        self.assertAlmostEqual(s3.drawdown_pct, 10.0, places=4)
        self.assertFalse(s3.is_triggered)

    def test_04_drawdown_below_threshold_remains_armed(self):
        """Drawdown strictly below 20.0% (e.g. 19.99%) leaves breaker ARMED."""
        cb = PortfolioCircuitBreaker(threshold_pct=20.0, initial_equity=10000.0)

        # 10% drawdown
        s1 = cb.evaluate(9000.0)
        self.assertAlmostEqual(s1.drawdown_pct, 10.0, places=4)
        self.assertFalse(s1.is_triggered)
        self.assertEqual(s1.state, CircuitBreakerState.ARMED)

        # 19.99% drawdown (equity = 8001.0)
        s2 = cb.evaluate(8001.0)
        self.assertAlmostEqual(s2.drawdown_pct, 19.99, places=2)
        self.assertFalse(s2.is_triggered)
        self.assertEqual(s2.state, CircuitBreakerState.ARMED)

    # -------------------------------------------------------------------------
    # 3. Exact Threshold Trigger & Severe Breach
    # -------------------------------------------------------------------------

    def test_05_exact_threshold_trigger(self):
        """Exact 20.0% drawdown (equity = 8000.0 from HWM 10000.0) triggers breaker."""
        cb = PortfolioCircuitBreaker(threshold_pct=20.0, initial_equity=10000.0)

        status = cb.evaluate(8000.0)
        self.assertTrue(status.is_triggered)
        self.assertTrue(cb.is_triggered)
        self.assertEqual(status.state, CircuitBreakerState.TRIGGERED)
        self.assertAlmostEqual(status.drawdown_pct, 20.0, places=4)
        self.assertEqual(status.trigger_equity, 8000.0)
        self.assertEqual(status.trigger_hwm, 10000.0)
        self.assertIsNotNone(status.triggered_at)
        self.assertIn("reached or exceeded threshold 20.00%", status.trigger_reason)

    def test_06_severe_breach_trigger(self):
        """Severe breach (e.g. 35.0% drawdown) triggers breaker immediately."""
        cb = PortfolioCircuitBreaker(threshold_pct=20.0, initial_equity=10000.0)

        status = cb.evaluate(6500.0)
        self.assertTrue(status.is_triggered)
        self.assertEqual(status.state, CircuitBreakerState.TRIGGERED)
        self.assertAlmostEqual(status.drawdown_pct, 35.0, places=4)
        self.assertEqual(status.trigger_equity, 6500.0)

    # -------------------------------------------------------------------------
    # 4. Latching & Durability (No Auto-Recovery)
    # -------------------------------------------------------------------------

    def test_07_latching_equity_rebound_does_not_clear(self):
        """Once triggered, equity rebound does NOT clear the trigger latch."""
        cb = PortfolioCircuitBreaker(threshold_pct=20.0, initial_equity=10000.0)

        # Trigger at 8000 (20%)
        cb.evaluate(8000.0)
        self.assertTrue(cb.is_triggered)

        # Equity rebounds to 9500 (5% drawdown from 10000)
        s_rebound = cb.evaluate(9500.0)
        self.assertTrue(s_rebound.is_triggered)
        self.assertTrue(cb.is_triggered)
        self.assertEqual(s_rebound.state, CircuitBreakerState.TRIGGERED)
        self.assertEqual(s_rebound.trigger_equity, 8000.0)

        # Equity rebounds above previous HWM to 11000
        s_above = cb.evaluate(11000.0)
        self.assertTrue(s_above.is_triggered)
        self.assertTrue(cb.is_triggered)
        self.assertEqual(s_above.state, CircuitBreakerState.TRIGGERED)

    # -------------------------------------------------------------------------
    # 5. Fail-Closed Contracts
    # -------------------------------------------------------------------------

    def test_08_fail_closed_on_non_finite_inputs(self):
        """Passing NaN, Inf, -Inf, or invalid type activates fail-closed latch."""
        cb1 = PortfolioCircuitBreaker(initial_equity=10000.0)
        s1 = cb1.evaluate(float("nan"))
        self.assertTrue(s1.is_triggered)
        self.assertEqual(s1.state, CircuitBreakerState.TRIGGERED)
        self.assertIn("FAIL_CLOSED", s1.trigger_reason)

        cb2 = PortfolioCircuitBreaker(initial_equity=10000.0)
        s2 = cb2.evaluate(float("inf"))
        self.assertTrue(s2.is_triggered)

        cb3 = PortfolioCircuitBreaker(initial_equity=10000.0)
        s3 = cb3.evaluate("corrupt_equity")
        self.assertTrue(s3.is_triggered)

    # -------------------------------------------------------------------------
    # 6. Durable Persistence & Reboot Recovery
    # -------------------------------------------------------------------------

    def test_09_durable_persistence_and_recovery_armed(self):
        """ARMED circuit breaker state survives restart and preserves HWM."""
        storage = PaperStorage(db_path=self.db_path)
        storage.initialize_schema()

        cb1 = PortfolioCircuitBreaker(threshold_pct=20.0, initial_equity=10000.0, storage=storage)
        cb1.evaluate(12500.0)  # Establish new HWM
        storage.close()

        # Simulate system reboot: create new storage and new breaker instance
        storage2 = PaperStorage(db_path=self.db_path)
        cb2 = PortfolioCircuitBreaker(threshold_pct=20.0, storage=storage2)

        status2 = cb2.get_status()
        self.assertEqual(status2.state, CircuitBreakerState.ARMED)
        self.assertFalse(status2.is_triggered)
        self.assertEqual(status2.high_water_mark, 12500.0)
        self.assertEqual(status2.current_equity, 12500.0)
        storage2.close()

    def test_10_durable_persistence_and_recovery_triggered_latch(self):
        """TRIGGERED circuit breaker state survives restart and remains latched."""
        storage = PaperStorage(db_path=self.db_path)
        storage.initialize_schema()

        cb1 = PortfolioCircuitBreaker(threshold_pct=20.0, initial_equity=10000.0, storage=storage)
        cb1.evaluate(7500.0)  # 25% drawdown -> triggers!
        self.assertTrue(cb1.is_triggered)
        storage.close()

        # Simulate restart on same DB
        storage2 = PaperStorage(db_path=self.db_path)
        cb2 = PortfolioCircuitBreaker(threshold_pct=20.0, storage=storage2)

        status2 = cb2.get_status()
        self.assertTrue(status2.is_triggered)
        self.assertTrue(cb2.is_triggered)
        self.assertEqual(status2.state, CircuitBreakerState.TRIGGERED)
        self.assertEqual(status2.high_water_mark, 10000.0)
        self.assertEqual(status2.trigger_equity, 7500.0)
        storage2.close()

    # -------------------------------------------------------------------------
    # 7. Authoritative Entry Blocking in PaperTradingEngine
    # -------------------------------------------------------------------------

    def test_11_paper_engine_entry_blocking_when_triggered(self):
        """PaperTradingEngine rejects orders with CIRCUIT_BREAKER_ACTIVE when breaker is triggered."""
        engine = PaperTradingEngine(starting_cash=10000.0, max_drawdown_pct=20.0)

        # Trigger circuit breaker directly
        engine.circuit_breaker.evaluate(8000.0)  # 20% drawdown
        self.assertTrue(engine.circuit_breaker.is_triggered)

        risk = make_test_passing_risk()
        req = ExecutionRequest(
            request_id="exec_req_001",
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            quantity=0.1,
            reference_price=50000.0,
            risk_assessment=risk,
            strategy_id="ema_trend_following",
            idempotency_key="idem_001",
            risk_policy_id="default_linear_paper_policy",
            notional=5000.0,
        )

        res = engine.execute(req)
        self.assertEqual(res.status, ExecutionStatus.REJECTED)
        self.assertEqual(res.order_state, OrderState.REJECTED)
        self.assertEqual(res.reason_codes, ("CIRCUIT_BREAKER_ACTIVE",))
        self.assertIn("circuit breaker is ACTIVE", res.explanation)
        self.assertEqual(len(engine.get_open_positions()), 0)
        self.assertEqual(engine.get_portfolio_snapshot().cash, 10000.0)

    # -------------------------------------------------------------------------
    # 8. Entry Blocking in PaperTradingWorkflow
    # -------------------------------------------------------------------------

    def test_12_workflow_shortlist_blocking_when_triggered(self):
        """PaperTradingWorkflow rejects all candidates when circuit breaker is active."""
        engine = PaperTradingEngine(starting_cash=10000.0, max_drawdown_pct=20.0)
        workflow = PaperTradingWorkflow(paper_engine=engine)

        # Trigger breaker
        engine.circuit_breaker.evaluate(8000.0)
        self.assertTrue(workflow.circuit_breaker.is_triggered)

        analysis1 = make_test_analysis("BTCUSDT", 50000.0)
        analysis2 = make_test_analysis("ETHUSDT", 3000.0)

        results = workflow.process_shortlist([analysis1, analysis2])
        self.assertEqual(len(results), 2)
        for res in results:
            self.assertEqual(res.status, CandidateProcessingStatus.RISK_REJECTED)
            self.assertEqual(res.reason_codes, ("CIRCUIT_BREAKER_ACTIVE",))
            self.assertIn("circuit breaker is ACTIVE", res.explanation)

    # -------------------------------------------------------------------------
    # 9. Position Monitoring Remains Active During Trigger
    # -------------------------------------------------------------------------

    def test_13_open_positions_monitored_and_closed_while_triggered(self):
        """Open positions can still be monitored and closed (realizing PnL) while breaker is active."""
        engine = PaperTradingEngine(starting_cash=10000.0, max_drawdown_pct=20.0)

        # Open a position while ARMED
        risk = make_test_passing_risk(entry_price=50000.0, quantity=0.1)
        req = ExecutionRequest(
            request_id="exec_req_002",
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            quantity=0.1,
            reference_price=50000.0,
            risk_assessment=risk,
            strategy_id="ema_trend_following",
            idempotency_key="idem_002",
            risk_policy_id="default_linear_paper_policy",
            notional=5000.0,
            metadata={"stop_loss_price": 49000.0, "take_profit_price": 52000.0},
        )
        fill_res = engine.execute(req)
        self.assertEqual(fill_res.status, ExecutionStatus.FILLED)
        self.assertEqual(len(engine.get_open_positions()), 1)

        # Trigger circuit breaker (e.g. external trigger or manual fail-closed)
        engine.circuit_breaker.evaluate(7900.0)
        self.assertTrue(engine.circuit_breaker.is_triggered)

        # Monitoring should trigger SL closure without error
        closed_trades = engine.update_market_price("BTCUSDT", 48500.0)
        self.assertEqual(len(closed_trades), 1)
        self.assertEqual(closed_trades[0].exit_reason, "STOP_LOSS_TRIGGERED")
        self.assertEqual(len(engine.get_open_positions()), 0)

    # -------------------------------------------------------------------------
    # 10. ContinuousRuntime Controlled Graceful Shutdown Once Flat
    # -------------------------------------------------------------------------

    def test_14_continuous_runtime_shutdown_when_breaker_active_and_flat(self):
        """ContinuousRuntime halts scanner and initiates graceful shutdown when breaker triggers and positions are flat."""
        config = RuntimeConfig(
            mode=RuntimeMode.CONTINUOUS,
            scan_interval=0.01,
            position_monitor_interval=0.01,
        )

        cb_triggered = [True]  # Simulate triggered breaker
        open_positions = [[]]  # Flat (0 positions)

        scan_called = []
        monitor_called = []

        def mock_scan():
            scan_called.append(1)
            return []

        def mock_monitor():
            monitor_called.append(1)
            return []

        runtime = ContinuousRuntime(
            config=config,
            scan_cycle_callback=mock_scan,
            position_monitor_callback=mock_monitor,
            circuit_breaker_callback=lambda: cb_triggered[0],
            open_positions_callback=lambda: open_positions[0],
        )

        metrics = runtime.run()
        self.assertEqual(metrics.exit_reason, "CIRCUIT_BREAKER_TRIGGERED")
        self.assertEqual(metrics.cycles_completed, 0)  # Scanner halted immediately

    def test_15_continuous_runtime_monitors_until_flat_then_shuts_down(self):
        """ContinuousRuntime keeps position monitor running until all positions are flat, then shuts down."""
        config = RuntimeConfig(
            mode=RuntimeMode.CONTINUOUS,
            scan_interval=0.01,
            position_monitor_interval=0.01,
        )

        cb_triggered = [True]
        # Simulate 1 position initially, closed on first monitor pass
        fake_pos = ["pos_1"]
        open_positions = [fake_pos]

        def mock_scan():
            return []

        def mock_monitor():
            # Close position
            open_positions[0] = []
            return [
                PaperTrade(
                    trade_id="paper_trd_000001",
                    position_id="paper_pos_000001",
                    order_id="paper_ord_000001",
                    symbol="BTCUSDT",
                    direction=SignalDirection.LONG,
                    strategy_id="ema_trend_following",
                    entry_price=50000.0,
                    exit_price=49000.0,
                    quantity=0.1,
                    contract_multiplier=1.0,
                    entry_time=datetime.now(timezone.utc),
                    exit_time=datetime.now(timezone.utc),
                    gross_pnl=-100.0,
                    entry_fee=3.0,
                    exit_fee=3.0,
                    net_pnl=-106.0,
                    exit_reason="STOP_LOSS_TRIGGERED",
                )
            ]

        runtime = ContinuousRuntime(
            config=config,
            scan_cycle_callback=mock_scan,
            position_monitor_callback=mock_monitor,
            circuit_breaker_callback=lambda: cb_triggered[0],
            open_positions_callback=lambda: open_positions[0],
        )

        metrics = runtime.run()
        self.assertEqual(metrics.exit_reason, "CIRCUIT_BREAKER_TRIGGERED")
        self.assertEqual(metrics.positions_closed_total, 1)

    # -------------------------------------------------------------------------
    # 11. Explicit Operator Reset
    # -------------------------------------------------------------------------

    def test_16_authorized_operator_reset(self):
        """Authorized operator reset clears latch, resets HWM to current equity, and re-arms breaker."""
        storage = PaperStorage(db_path=self.db_path)
        storage.initialize_schema()

        cb = PortfolioCircuitBreaker(threshold_pct=20.0, initial_equity=10000.0, storage=storage)
        cb.evaluate(8000.0)  # Trigger breaker
        self.assertTrue(cb.is_triggered)

        # Unidentified reset attempts fail
        with self.assertRaises(ValueError):
            cb.reset(operator="", reason="Valid reason")
        with self.assertRaises(ValueError):
            cb.reset(operator="ops_lead", reason="")

        # Authorized reset
        reset_status = cb.reset(operator="ops_lead", reason="Post-drawdown review complete; capital replenished")
        self.assertFalse(reset_status.is_triggered)
        self.assertFalse(cb.is_triggered)
        self.assertEqual(cb.state, CircuitBreakerState.ARMED)
        self.assertEqual(cb.high_water_mark, 8000.0)  # Re-anchored to current equity
        self.assertEqual(cb.drawdown_pct, 0.0)
        self.assertIn("ops_lead", cb.get_status().reset_reason)

        storage.close()

    # -------------------------------------------------------------------------
    # 12. Thread Safety
    # -------------------------------------------------------------------------

    def test_17_circuit_breaker_thread_safety(self):
        """Concurrent equity evaluations from multiple threads maintain coherent state without corruption."""
        cb = PortfolioCircuitBreaker(initial_equity=10000.0)
        barrier = threading.Barrier(10)
        errors = []

        def worker(eq_val):
            try:
                barrier.wait()
                for _ in range(50):
                    cb.evaluate(eq_val)
                    _ = cb.get_status()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(9000.0 + i * 100,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        status = cb.get_status()
        self.assertTrue(math.isfinite(status.high_water_mark))
        self.assertFalse(status.is_triggered)


if __name__ == "__main__":
    unittest.main()
