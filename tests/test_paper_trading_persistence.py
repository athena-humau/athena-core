from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ai.models import (
    JudgeDecision,
    JudgeVerdict,
    ValidatedCandidate,
    ValidationStatus,
)
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
from execution.paper_persistence import (
    DatabaseCorruptionError,
    IncompatibleSchemaError,
    PaperStorage,
    PersistenceError,
    RecoveredLedgerState,
    TransactionError,
)
from execution.paper_workflow import (
    CandidateProcessingStatus,
    PaperTradingWorkflow,
)
from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from regime.models import Regime, TimeframeRegime
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
    policy_id: str = "deterministic_risk_v1",
) -> RiskAssessment:
    """Helper to build a deterministic passing RiskAssessment."""
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
        requested_leverage=2.0,
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


def make_test_market_analysis(
    symbol: str = "BTCUSDT",
    regime: Regime = Regime.BULLISH,
    last_close: float = 50000.0,
    ema_9: float = 47500.0,
    ema_21: float = 45000.0,
    rsi_14: float = 60.0,
    atr_14: float = 500.0,
    granularity: str = "1H",
) -> MarketAnalysis:
    """Helper to build a deterministic MarketAnalysis fixture."""
    now_ts = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    tf_regime = TimeframeRegime(
        symbol=symbol,
        granularity=granularity,
        timestamp=now_ts,
        candle_count=50,
        regime=regime,
    )
    feature_vals = {
        "last_close": last_close,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "rsi_14": rsi_14,
        "atr_14": atr_14,
    }
    tf_features = TimeframeFeatures(
        symbol=symbol,
        granularity=granularity,
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
        granularity=granularity,
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
        candles_by_granularity={granularity: [test_candle]},
        features_by_granularity={granularity: tf_features},
        regimes_by_granularity={granularity: tf_regime},
        success=True,
        analyzed_at=now_ts,
    )


class TestPaperTradingPersistence(unittest.TestCase):
    """
    Comprehensive test suite for Sprint 33 Paper Trading Persistence and Restart Recovery.
    All tests execute against isolated temporary SQLite databases.
    """

    def setUp(self):
        # Create a unique temporary database file for each test
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_paper.db")
        self.passing_risk = make_test_passing_risk()

    def tearDown(self):
        # Ensure directory is cleaned up
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Database Initialization & Safety
    # -------------------------------------------------------------------------

    def test_01_fresh_database_initialization_and_schema(self):
        storage = PaperStorage(self.db_path)
        ver = storage.initialize_schema()
        self.assertEqual(ver, 1)

        conn = storage.get_connection()
        cursor = conn.cursor()

        # Check journal mode is WAL
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

        # Check foreign keys
        cursor.execute("PRAGMA foreign_keys;")
        fk = cursor.fetchone()[0]
        self.assertEqual(fk, 1)

        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn("paper_portfolio_state", tables)
        self.assertIn("paper_orders", tables)
        self.assertIn("paper_positions", tables)
        self.assertIn("paper_trades", tables)
        self.assertIn("idempotency_records", tables)
        self.assertIn("schema_version", tables)
        storage.close()

    def test_02_repeated_initialization_safety(self):
        storage = PaperStorage(self.db_path)
        ver1 = storage.initialize_schema()
        ver2 = storage.initialize_schema()
        self.assertEqual(ver1, ver2)

        # Record initial state
        storage.save_portfolio_initialization(10000.0, 10000.0, 0.0006, 0.0, 1.0)
        # Call again; must not overwrite
        storage.save_portfolio_initialization(50000.0, 50000.0, 0.001, 0.0, 1.0)

        recovered = storage.load_ledger_state()
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.starting_equity, 10000.0)
        self.assertEqual(recovered.cash, 10000.0)
        storage.close()

    def test_03_incompatible_newer_schema_fails_closed(self):
        storage = PaperStorage(self.db_path)
        storage.initialize_schema()
        conn = storage.get_connection()
        conn.execute("UPDATE schema_version SET version = 99;")
        conn.commit()
        storage.close()

        # Reopening with higher schema version must raise IncompatibleSchemaError
        reopened = PaperStorage(self.db_path)
        with self.assertRaises(IncompatibleSchemaError):
            reopened.initialize_schema()
        reopened.close()

    def test_04_corrupted_database_fails_closed(self):
        # Write corrupted garbage data to the file
        with open(self.db_path, "wb") as f:
            f.write(b"NOT_A_VALID_SQLITE_DATABASE_HEADER_CORRUPTED_DATA")

        with self.assertRaises(DatabaseCorruptionError):
            storage = PaperStorage(self.db_path)
            storage.initialize_schema()

    # -------------------------------------------------------------------------
    # 2. Position Opening & Recovery
    # -------------------------------------------------------------------------

    def test_05_position_opening_persists_and_recovers_across_restarts(self):
        # 1. Initialize engine with db_path and execute an order
        engine1 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_persist_01",
            idempotency_key="idem_persist_01",
            order_type=OrderType.MARKET,
        )
        res = engine1.execute(req, market_price=50000.0)
        self.assertEqual(res.status, ExecutionStatus.FILLED)
        snapshot1 = engine1.get_portfolio_snapshot()
        self.assertEqual(len(snapshot1.open_positions), 1)
        cash_before_restart = snapshot1.cash
        equity_before_restart = snapshot1.total_equity
        pos_id = res.position.position_id
        engine1.close()

        # 2. Simulate process restart by instantiating a completely new PaperTradingEngine
        engine2 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        snapshot2 = engine2.get_portfolio_snapshot()

        # 3. Assert recovered state matches exactly
        self.assertEqual(len(snapshot2.open_positions), 1)
        recovered_pos = engine2.get_position(pos_id)
        self.assertIsNotNone(recovered_pos)
        self.assertEqual(recovered_pos.position_id, pos_id)
        self.assertEqual(recovered_pos.symbol, "BTCUSDT")
        self.assertEqual(recovered_pos.direction, SignalDirection.LONG)
        self.assertEqual(recovered_pos.entry_price, 50000.0)
        self.assertEqual(recovered_pos.quantity, 0.1)
        self.assertEqual(recovered_pos.stop_loss_price, 49000.0)
        self.assertAlmostEqual(snapshot2.cash, cash_before_restart, places=4)
        self.assertAlmostEqual(snapshot2.total_equity, equity_before_restart, places=4)
        self.assertNotEqual(snapshot2.cash, 10000.0)  # Cash must NOT reset to starting equity!

        engine2.close()

    # -------------------------------------------------------------------------
    # 3. Partial Close & Full Close Recovery
    # -------------------------------------------------------------------------

    def test_06_recovery_after_partial_close(self):
        engine1 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_part_01",
            idempotency_key="idem_part_01",
            order_type=OrderType.MARKET,
        )
        res = engine1.execute(req, market_price=50000.0)
        pos_id = res.position.position_id

        # Partial close 50% (0.05 BTC) at 52000.0
        trade = engine1.close_position(pos_id, exit_price=52000.0, quantity=0.05)
        self.assertEqual(trade.quantity, 0.05)
        snap1 = engine1.get_portfolio_snapshot()
        self.assertEqual(len(snap1.open_positions), 1)
        self.assertEqual(len(snap1.closed_trades), 1)
        engine1.close()

        # Restart engine
        engine2 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        snap2 = engine2.get_portfolio_snapshot()
        self.assertEqual(len(snap2.open_positions), 1)
        self.assertEqual(len(snap2.closed_trades), 1)

        rec_pos = engine2.get_position(pos_id)
        self.assertAlmostEqual(rec_pos.quantity, 0.05, places=4)
        self.assertAlmostEqual(snap2.cash, snap1.cash, places=4)
        self.assertAlmostEqual(snap2.realized_pnl, snap1.realized_pnl, places=4)
        engine2.close()

    def test_07_recovery_after_full_close(self):
        engine1 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_full_01",
            idempotency_key="idem_full_01",
            order_type=OrderType.MARKET,
        )
        res = engine1.execute(req, market_price=50000.0)
        pos_id = res.position.position_id

        # Full close at 51000.0
        trade = engine1.close_position(pos_id, exit_price=51000.0)
        self.assertEqual(trade.quantity, 0.1)
        snap1 = engine1.get_portfolio_snapshot()
        self.assertEqual(len(snap1.open_positions), 0)
        self.assertEqual(len(snap1.closed_trades), 1)
        final_cash = snap1.cash
        engine1.close()

        # Restart
        engine2 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        snap2 = engine2.get_portfolio_snapshot()
        self.assertEqual(len(snap2.open_positions), 0)
        self.assertEqual(len(snap2.closed_trades), 1)
        self.assertAlmostEqual(snap2.cash, final_cash, places=4)
        self.assertAlmostEqual(snap2.total_equity, final_cash, places=4)
        self.assertAlmostEqual(snap2.reserved_margin, 0.0, places=4)
        engine2.close()

    # -------------------------------------------------------------------------
    # 4. Idempotency Across Restarts
    # -------------------------------------------------------------------------

    def test_08_duplicate_request_recognized_after_restart(self):
        engine1 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_idem_01",
            idempotency_key="key_restart_duplicate",
            order_type=OrderType.MARKET,
        )
        res1 = engine1.execute(req, market_price=50000.0)
        self.assertEqual(res1.status, ExecutionStatus.FILLED)
        engine1.close()

        # Restart
        engine2 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)

        # Re-execute the exact same request with the same idempotency key
        res2 = engine2.execute(req, market_price=50000.0)
        self.assertEqual(res2.status, ExecutionStatus.DUPLICATE)
        self.assertEqual(res2.order.order_id, res1.order.order_id)
        self.assertEqual(res2.position.position_id, res1.position.position_id)

        # Ensure no duplicate position was created
        snap = engine2.get_portfolio_snapshot()
        self.assertEqual(len(snap.open_positions), 1)
        engine2.close()

    def test_09_conflicting_request_rejected_after_restart(self):
        engine1 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        req1 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_idem_02",
            idempotency_key="key_restart_conflict",
            order_type=OrderType.MARKET,
            quantity=0.1,
        )
        res1 = engine1.execute(req1, market_price=50000.0)
        self.assertEqual(res1.status, ExecutionStatus.FILLED)
        engine1.close()

        # Restart
        engine2 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)

        # Submit different payload with the same idempotency key
        req2 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_idem_03",
            idempotency_key="key_restart_conflict",
            order_type=OrderType.MARKET,
            quantity=0.05,  # materially different quantity
        )
        res2 = engine2.execute(req2, market_price=50000.0)
        self.assertEqual(res2.status, ExecutionStatus.CONFLICT)
        engine2.close()

    # -------------------------------------------------------------------------
    # 5. Failure Safety & Rollback
    # -------------------------------------------------------------------------

    def test_10_database_failure_rolls_back_and_leaves_state_untouched(self):
        engine = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_fail_01",
            idempotency_key="key_fail_01",
            order_type=OrderType.MARKET,
        )

        # Patch record_order_fill to simulate database failure
        with patch.object(engine.storage, "record_order_fill", side_effect=sqlite3.OperationalError("Disk I/O error")):
            res = engine.execute(req, market_price=50000.0)

        # Assert rejection and reason code
        self.assertEqual(res.status, ExecutionStatus.REJECTED)
        self.assertEqual(res.order_state, OrderState.REJECTED)
        self.assertIn("PERSISTENCE_TRANSACTION_FAILED", res.reason_codes)

        # Assert in-memory state is completely clean and untouched
        snap = engine.get_portfolio_snapshot()
        self.assertEqual(snap.cash, 10000.0)
        self.assertEqual(len(snap.open_positions), 0)
        self.assertEqual(len(snap.closed_trades), 0)
        self.assertEqual(engine._order_counter, 0)
        self.assertEqual(engine._position_counter, 0)
        engine.close()

    def test_11_reset_prohibited_on_persistent_engine(self):
        engine = PaperTradingEngine(starting_cash=10000.0, db_path=self.db_path)
        with self.assertRaises(RuntimeError) as ctx:
            engine.reset()
        self.assertIn("Resetting a persistent paper portfolio is prohibited", str(ctx.exception))
        engine.close()

    # -------------------------------------------------------------------------
    # 6. End-to-End Workflow Integration with Persistence
    # -------------------------------------------------------------------------

    def test_12_workflow_integration_persists_and_recovers(self):
        # Run end-to-end workflow on candidate with persistent storage
        workflow = PaperTradingWorkflow(db_path=self.db_path)
        analysis = make_test_market_analysis(symbol="BTCUSDT", regime=Regime.BULLISH, last_close=50000.0)

        results = workflow.process_shortlist([analysis])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, CandidateProcessingStatus.PAPER_FILLED)
        snap1 = workflow.get_portfolio_snapshot()
        self.assertEqual(len(snap1.open_positions), 1)
        workflow.close()

        # Recreate workflow on the exact same database
        workflow2 = PaperTradingWorkflow(db_path=self.db_path)
        snap2 = workflow2.get_portfolio_snapshot()
        self.assertEqual(len(snap2.open_positions), 1)
        self.assertAlmostEqual(snap2.cash, snap1.cash, places=4)
        self.assertAlmostEqual(snap2.total_equity, snap1.total_equity, places=4)
        workflow2.close()

    def test_13_stop_loss_trigger_close_persists_across_restart(self):
        engine = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_sl_01",
            idempotency_key="key_sl_01",
            order_type=OrderType.MARKET,
        )
        engine.execute(req, market_price=50000.0)

        # Trigger Stop Loss by setting price to 48000.0 (SL is 49000.0)
        trades = engine.update_market_price("BTCUSDT", current_price=48000.0)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_reason, "STOP_LOSS_TRIGGERED")
        snap1 = engine.get_portfolio_snapshot()
        self.assertEqual(len(snap1.open_positions), 0)
        engine.close()

        # Reopen engine and verify closed state persisted
        engine2 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        snap2 = engine2.get_portfolio_snapshot()
        self.assertEqual(len(snap2.open_positions), 0)
        self.assertEqual(len(snap2.closed_trades), 1)
        self.assertEqual(snap2.closed_trades[0].exit_reason, "STOP_LOSS_TRIGGERED")
        engine2.close()

    def test_14_short_position_persists_and_recovers_with_accounting(self):
        # 1. Build SHORT proposal
        short_risk = make_test_passing_risk(
            symbol="ETHUSDT",
            strategy_id="rsi_mean_reversion",
            direction=SignalDirection.SHORT,
            entry_price=3000.0,
            stop_loss_price=3100.0,
            quantity=1.0,
        )
        engine1 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=short_risk,
            request_id="req_short_01",
            idempotency_key="key_short_01",
            order_type=OrderType.MARKET,
        )
        res = engine1.execute(req, market_price=3000.0)
        self.assertEqual(res.status, ExecutionStatus.FILLED)
        snap1 = engine1.get_portfolio_snapshot()
        self.assertEqual(len(snap1.open_positions), 1)
        self.assertEqual(res.position.direction, SignalDirection.SHORT)
        engine1.close()

        # 2. Reopen engine and check SHORT recovery
        engine2 = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, db_path=self.db_path)
        snap2 = engine2.get_portfolio_snapshot()
        self.assertEqual(len(snap2.open_positions), 1)
        pos = snap2.open_positions[0]
        self.assertEqual(pos.symbol, "ETHUSDT")
        self.assertEqual(pos.direction, SignalDirection.SHORT)
        self.assertEqual(pos.entry_price, 3000.0)
        self.assertEqual(pos.quantity, 1.0)
        self.assertAlmostEqual(snap2.cash, snap1.cash, places=4)
        engine2.close()

    def test_15_foreign_key_enforcement_and_record_integrity(self):
        storage = PaperStorage(self.db_path)
        storage.initialize_schema()
        conn = storage.get_connection()

        # Attempt to insert an orphaned paper_positions row referencing non-existent order_id
        with self.assertRaises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    """
                    INSERT INTO paper_positions (
                        position_id, order_id, symbol, direction, entry_price,
                        quantity, contract_multiplier, notional, required_margin,
                        leverage, entry_time, stop_loss_price, take_profit_price,
                        entry_fee, unrealized_pnl, is_closed, is_paper, metadata
                    ) VALUES (
                        'paper_pos_999999', 'non_existent_order', 'BTCUSDT', 'LONG',
                        50000.0, 0.1, 1.0, 5000.0, 2500.0, 2.0, '2026-10-17T12:00:00+00:00',
                        49000.0, NULL, 3.0, 0.0, 0, 1, '{}'
                    );
                    """
                )
        storage.close()

    def test_16_athena_app_with_custom_db_path(self):
        from core.app import Athena
        app = Athena(db_path=self.db_path)
        self.assertEqual(app.db_path, self.db_path)
        self.assertIsNotNone(app.paper_workflow)
        self.assertEqual(app.paper_workflow.paper_engine.storage.db_path, os.path.abspath(self.db_path))
        app.paper_workflow.close()
