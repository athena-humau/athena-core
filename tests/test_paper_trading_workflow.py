from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import math
import unittest

from ai.deterministic_evaluator import DeterministicStrategyEvaluator
from ai.deterministic_judge import DeterministicJudge
from ai.deterministic_ranker import DeterministicStrategyRanker
from ai.deterministic_selector import DeterministicStrategySelector
from ai.deterministic_validator import DeterministicSignalValidator
from ai.models import (
    JudgeDecision,
    JudgeVerdict,
    StrategyCandidate,
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
from execution.paper_workflow import (
    CandidateProcessingStatus,
    CandidateWorkflowResult,
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
from strategy.context import StrategyContext
from strategy.ema_trend_following import EMATrendFollowingStrategy
from strategy.models import Signal, SignalDirection
from strategy.registry import StrategyRegistry
from strategy.rsi_mean_reversion import RSIMeanReversionStrategy


def make_test_market_analysis(
    symbol: str = "BTCUSDT",
    regime: Regime = Regime.BULLISH,
    last_close: float = 50000.0,
    ema_9: float = 47500.0,
    ema_21: float = 45000.0,
    rsi_14: float = 60.0,
    atr_14: float = 500.0,
    granularity: str = "1H",
    success: bool = True,
    error: str | None = None,
    candle_count: int = 50,
) -> MarketAnalysis:
    """Helper to build a deterministic MarketAnalysis fixture."""
    now_ts = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)

    tf_regime = TimeframeRegime(
        symbol=symbol,
        granularity=granularity,
        timestamp=now_ts,
        candle_count=candle_count,
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
        candle_count=candle_count,
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
        success=success,
        error=error,
        analyzed_at=now_ts,
    )


class TestEndToEndPaperTradingWorkflow(unittest.TestCase):
    """
    Sprint 31 End-to-End Paper Trading Integration Test Suite.
    Verifies Scenarios A through J with strict determinism and zero network calls.
    """

    def setUp(self):
        # Fresh isolated PaperTradingEngine and PaperTradingWorkflow for each test
        self.paper_engine = PaperTradingEngine(
            starting_cash=10000.0,
            fee_rate=0.0006,  # 0.06%
            slippage_pct=0.0,
            contract_multiplier=1.0,
        )
        self.workflow = PaperTradingWorkflow(
            paper_engine=self.paper_engine,
            reference_timeframe="1H",
            default_leverage=1.0,
            default_risk_pct=2.0,
        )

    # -------------------------------------------------------------------------
    # Scenario A: Valid LONG trade full flow -> filled -> price move -> exit
    # -------------------------------------------------------------------------
    def test_scenario_a_valid_long_trade_full_lifecycle(self):
        # 1. Market analysis: Bullish, EMA9 > EMA21 and price > EMA9
        # price=50000, ema9=47500, ema21=45000, atr=500
        analysis = make_test_market_analysis(
            symbol="BTCUSDT",
            regime=Regime.BULLISH,
            last_close=50000.0,
            ema_9=47500.0,
            ema_21=45000.0,
            rsi_14=60.0,
            atr_14=500.0,
        )

        # 2. Process through end-to-end workflow
        res = self.workflow.process_market_analysis(analysis)

        # 3. Assert candidate result
        self.assertEqual(res.status, CandidateProcessingStatus.PAPER_FILLED)
        self.assertTrue(res.is_filled)
        self.assertFalse(res.is_rejected)
        self.assertEqual(res.stage_reached, "FILLED")
        self.assertIsNotNone(res.selection)
        self.assertEqual(res.selection.selected_strategy_id, "ema_trend_following")
        self.assertIsNotNone(res.signal)
        self.assertEqual(res.signal.direction, SignalDirection.LONG)
        self.assertIsNotNone(res.validated_candidate)
        self.assertTrue(res.validated_candidate.is_valid)
        self.assertIsNotNone(res.judge_decision)
        self.assertEqual(res.judge_decision.verdict, JudgeVerdict.PASS)
        self.assertIsNotNone(res.risk_assessment)
        self.assertEqual(res.risk_assessment.verdict, RiskVerdict.PASS)
        self.assertIsNotNone(res.order)
        self.assertEqual(res.order.state, OrderState.FILLED)
        self.assertIsNotNone(res.position)
        self.assertEqual(res.position.direction, SignalDirection.LONG)
        self.assertEqual(res.position.entry_price, 50000.0)

        # 4. Verify portfolio accounting after entry
        snapshot_entry = self.workflow.get_portfolio_snapshot()
        self.assertEqual(len(snapshot_entry.open_positions), 1)
        expected_entry_fee = res.position.entry_fee
        self.assertGreater(expected_entry_fee, 0.0)
        self.assertAlmostEqual(
            snapshot_entry.cash,
            10000.0 - res.position.required_margin - expected_entry_fee,
            places=4,
        )
        self.assertAlmostEqual(snapshot_entry.reserved_margin, res.position.required_margin, places=4)
        self.assertAlmostEqual(snapshot_entry.open_notional, res.position.notional, places=4)

        # 5. Price move upwards (favorable price movement)
        trades = self.workflow.update_market_price("BTCUSDT", current_price=51000.0)
        self.assertEqual(len(trades), 0)  # No exit triggered yet (below TP)

        pos = self.workflow.get_open_positions()[0]
        expected_u_pnl = (51000.0 - 50000.0) * pos.quantity
        snapshot_mid = self.workflow.get_portfolio_snapshot()
        self.assertAlmostEqual(snapshot_mid.unrealized_pnl, expected_u_pnl, places=4)

        # 6. Clean manual close
        close_trade = self.workflow.close_position(
            position_id=pos.position_id,
            exit_price=51000.0,
            exit_reason="MANUAL_TAKE_PROFIT",
        )
        self.assertIsNotNone(close_trade)
        self.assertEqual(close_trade.symbol, "BTCUSDT")
        self.assertEqual(close_trade.direction, SignalDirection.LONG)
        self.assertAlmostEqual(close_trade.gross_pnl, expected_u_pnl, places=4)
        self.assertAlmostEqual(
            close_trade.net_pnl,
            close_trade.gross_pnl - close_trade.entry_fee - close_trade.exit_fee,
            places=4,
        )

        # 7. Verify portfolio accounting after full exit
        snapshot_final = self.workflow.get_portfolio_snapshot()
        self.assertEqual(len(snapshot_final.open_positions), 0)
        self.assertEqual(snapshot_final.reserved_margin, 0.0)
        self.assertEqual(snapshot_final.open_notional, 0.0)
        self.assertEqual(snapshot_final.unrealized_pnl, 0.0)
        self.assertAlmostEqual(snapshot_final.total_equity, 10000.0 + close_trade.net_pnl, places=4)
        self.assertAlmostEqual(snapshot_final.cash, snapshot_final.total_equity, places=4)

    # -------------------------------------------------------------------------
    # Scenario B: Valid SHORT trade full flow -> filled -> exit -> accounting
    # -------------------------------------------------------------------------
    def test_scenario_b_valid_short_trade_full_lifecycle(self):
        # Bearish setup: EMA9 < EMA21 and price < EMA9
        # price=3000, ema9=3200, ema21=3400, atr=30
        analysis = make_test_market_analysis(
            symbol="ETHUSDT",
            regime=Regime.BEARISH,
            last_close=3000.0,
            ema_9=3200.0,
            ema_21=3400.0,
            rsi_14=40.0,
            atr_14=30.0,
        )

        res = self.workflow.process_market_analysis(analysis)

        self.assertEqual(res.status, CandidateProcessingStatus.PAPER_FILLED)
        self.assertTrue(res.is_filled)
        self.assertEqual(res.signal.direction, SignalDirection.SHORT)
        self.assertEqual(res.position.direction, SignalDirection.SHORT)
        self.assertEqual(res.position.entry_price, 3000.0)
        self.assertGreater(res.position.stop_loss_price, 3000.0)  # Stop above entry for SHORT

        pos = res.position

        # Price move downwards to 2900 (profitable short)
        close_trade = self.workflow.close_position(
            position_id=pos.position_id,
            exit_price=2900.0,
            exit_reason="MANUAL_CLOSE",
        )
        expected_gross = (3000.0 - 2900.0) * pos.quantity
        self.assertAlmostEqual(close_trade.gross_pnl, expected_gross, places=4)
        self.assertAlmostEqual(
            close_trade.net_pnl,
            expected_gross - close_trade.entry_fee - close_trade.exit_fee,
            places=4,
        )

        # Portfolio accounting after exit
        snapshot = self.workflow.get_portfolio_snapshot()
        self.assertEqual(len(snapshot.open_positions), 0)
        self.assertAlmostEqual(snapshot.total_equity, 10000.0 + close_trade.net_pnl, places=4)

    # -------------------------------------------------------------------------
    # Scenario C: Strategy/Validator rejection -> no position, portfolio unchanged
    # -------------------------------------------------------------------------
    def test_scenario_c_strategy_and_validator_rejection(self):
        snapshot_start = self.workflow.get_portfolio_snapshot()

        # Subtest 1: FLAT condition (EMA9 == EMA21)
        analysis_flat = make_test_market_analysis(
            symbol="FLATUSDT",
            regime=Regime.BULLISH,
            last_close=100.0,
            ema_9=95.0,
            ema_21=95.0,  # fast equals slow -> FLAT
            rsi_14=50.0,
            atr_14=1.0,
        )
        res_flat = self.workflow.process_market_analysis(analysis_flat)
        self.assertEqual(res_flat.status, CandidateProcessingStatus.NO_ACTIONABLE_SIGNAL)
        self.assertTrue(res_flat.is_rejected)
        self.assertIsNone(res_flat.position)

        # Portfolio strictly unchanged
        snap1 = self.workflow.get_portfolio_snapshot()
        self.assertEqual(snap1.cash, snapshot_start.cash)
        self.assertEqual(snap1.total_equity, snapshot_start.total_equity)
        self.assertEqual(len(snap1.open_positions), 0)

        # Subtest 2: Ineligible analysis (analysis.success = False)
        analysis_fail = make_test_market_analysis(
            symbol="BADUSDT",
            success=False,
            error="Candle fetch timeout",
        )
        res_fail = self.workflow.process_market_analysis(analysis_fail)
        self.assertEqual(res_fail.status, CandidateProcessingStatus.INELIGIBLE)
        self.assertTrue(res_fail.is_rejected)
        self.assertIn("ANALYSIS_UNSUCCESSFUL", res_fail.reason_codes)

        # Portfolio still strictly unchanged
        snap2 = self.workflow.get_portfolio_snapshot()
        self.assertEqual(snap2.cash, snapshot_start.cash)
        self.assertEqual(len(snap2.open_positions), 0)

    # -------------------------------------------------------------------------
    # Scenario D: Risk rejection -> no position, portfolio unchanged
    # -------------------------------------------------------------------------
    def test_scenario_d_risk_rejection(self):
        snapshot_start = self.workflow.get_portfolio_snapshot()

        # Long analysis
        analysis = make_test_market_analysis(
            symbol="BTCUSDT",
            regime=Regime.BULLISH,
            last_close=50000.0,
            ema_9=47500.0,
            ema_21=45000.0,
            atr_14=500.0,
        )

        # Subtest 1: Wrong side stop (stop loss >= entry price for LONG)
        res_bad_stop = self.workflow.process_market_analysis(
            analysis,
            stop_loss_price=51000.0,  # Wrong: above entry price
        )
        self.assertEqual(res_bad_stop.status, CandidateProcessingStatus.RISK_REJECTED)
        self.assertTrue(res_bad_stop.is_rejected)
        self.assertIn("WRONG_SIDE_STOP", res_bad_stop.reason_codes)

        # Subtest 2: Excessive quantity exceeding max position notional
        res_too_large = self.workflow.process_market_analysis(
            analysis,
            quantity=1.0,  # 1 BTC * 50000 = 50,000 notional > 10,000 policy limit
            stop_loss_price=49000.0,
        )
        self.assertEqual(res_too_large.status, CandidateProcessingStatus.RISK_REJECTED)
        self.assertTrue(res_too_large.is_rejected)
        self.assertIn("EXCEEDS_POSITION_NOTIONAL_LIMIT", res_too_large.reason_codes)

        # Portfolio completely unchanged
        snapshot_after = self.workflow.get_portfolio_snapshot()
        self.assertEqual(snapshot_after.cash, snapshot_start.cash)
        self.assertEqual(snapshot_after.reserved_margin, 0.0)
        self.assertEqual(len(snapshot_after.open_positions), 0)

    # -------------------------------------------------------------------------
    # Scenario E: Duplicate request -> duplicate ignored, no double margin/fees
    # -------------------------------------------------------------------------
    def test_scenario_e_duplicate_request_idempotency(self):
        analysis = make_test_market_analysis(
            symbol="BTCUSDT",
            regime=Regime.BULLISH,
            last_close=50000.0,
            ema_9=47500.0,
            ema_21=45000.0,
            atr_14=500.0,
        )

        # First execution with explicit idempotency key
        res1 = self.workflow.process_market_analysis(
            analysis,
            quantity=0.05,
            idempotency_key="unique_idem_test_001",
            request_id="req_001",
        )
        self.assertEqual(res1.status, CandidateProcessingStatus.PAPER_FILLED)
        snap1 = self.workflow.get_portfolio_snapshot()
        self.assertEqual(len(snap1.open_positions), 1)
        cash_after_res1 = snap1.cash
        margin_after_res1 = snap1.reserved_margin
        fees_after_res1 = snap1.fees_paid

        # Second execution with identical idempotency key and identical payload
        res2 = self.workflow.process_market_analysis(
            analysis,
            quantity=0.05,
            idempotency_key="unique_idem_test_001",
            request_id="req_001",
        )
        self.assertEqual(res2.status, CandidateProcessingStatus.DUPLICATE)
        self.assertTrue(res2.is_duplicate)
        self.assertIn("DUPLICATE_REQUEST_IGNORED", res2.reason_codes)

        # Assert no double deduction or position duplication
        snap2 = self.workflow.get_portfolio_snapshot()
        self.assertEqual(len(snap2.open_positions), 1)
        self.assertEqual(snap2.cash, cash_after_res1)
        self.assertEqual(snap2.reserved_margin, margin_after_res1)
        self.assertEqual(snap2.fees_paid, fees_after_res1)

    # -------------------------------------------------------------------------
    # Scenario F: Idempotency conflict -> rejected as conflict
    # -------------------------------------------------------------------------
    def test_scenario_f_idempotency_conflict(self):
        analysis = make_test_market_analysis(
            symbol="BTCUSDT",
            regime=Regime.BULLISH,
            last_close=50000.0,
            ema_9=47500.0,
            ema_21=45000.0,
            atr_14=500.0,
        )

        # First request with quantity 0.05
        res1 = self.workflow.process_market_analysis(
            analysis,
            quantity=0.05,
            idempotency_key="conflict_test_key",
            request_id="req_conf_1",
        )
        self.assertEqual(res1.status, CandidateProcessingStatus.PAPER_FILLED)

        # Conflicting second request with same key but different quantity (0.10)
        res2 = self.workflow.process_market_analysis(
            analysis,
            quantity=0.10,
            idempotency_key="conflict_test_key",
            request_id="req_conf_2",
        )
        self.assertEqual(res2.status, CandidateProcessingStatus.CONFLICT)
        self.assertTrue(res2.is_rejected)
        self.assertIn("IDEMPOTENCY_CONFLICT", res2.reason_codes)

        # Portfolio state remains containing only 1 position
        snap = self.workflow.get_portfolio_snapshot()
        self.assertEqual(len(snap.open_positions), 1)

    # -------------------------------------------------------------------------
    # Scenario G: Stop-loss and take-profit triggers & ambiguous candle
    # -------------------------------------------------------------------------
    def test_scenario_g_sl_tp_triggers_and_ambiguous_candle_policy(self):
        analysis = make_test_market_analysis(
            symbol="SOLUSDT",
            regime=Regime.BULLISH,
            last_close=100.0,
            ema_9=95.0,
            ema_21=90.0,
            atr_14=2.0,
        )

        # 1. Open LONG position: entry=100.0, stop_loss=96.0, take_profit=106.0
        res = self.workflow.process_market_analysis(
            analysis,
            entry_price=100.0,
            stop_loss_price=96.0,
            take_profit_price=106.0,
            quantity=10.0,
        )
        self.assertEqual(res.status, CandidateProcessingStatus.PAPER_FILLED)
        pos = res.position
        self.assertEqual(pos.stop_loss_price, 96.0)
        self.assertEqual(pos.take_profit_price, 106.0)

        # 2. Ambiguous candle: Low <= 96.0 AND High >= 106.0 in the SAME candle!
        ambiguous_candle = Candle(
            symbol="SOLUSDT",
            timestamp=datetime(2026, 10, 17, 13, 0, tzinfo=timezone.utc),
            open=100.0,
            high=108.0,  # touches TP
            low=94.0,   # touches SL
            close=102.0,
            volume=50.0,
            quote_volume=5000.0,
            granularity="1H",
        )

        trades = self.workflow.process_candle(ambiguous_candle)
        self.assertEqual(len(trades), 1)
        triggered_trade = trades[0]

        # Conservative rule guarantees STOP LOSS triggers first
        self.assertEqual(triggered_trade.exit_reason, "STOP_LOSS_CONSERVATIVE_AMBIGUOUS_CANDLE")
        self.assertEqual(triggered_trade.exit_price, 96.0)
        self.assertLess(triggered_trade.gross_pnl, 0.0)

        # Position is cleanly closed
        self.assertEqual(len(self.workflow.get_open_positions()), 0)

        # 3. Take Profit direct trigger test
        res_tp = self.workflow.process_market_analysis(
            analysis,
            entry_price=100.0,
            stop_loss_price=96.0,
            take_profit_price=106.0,
            quantity=10.0,
        )
        self.assertEqual(res_tp.status, CandidateProcessingStatus.PAPER_FILLED)

        # Price ticks up directly to 106.50
        tp_trades = self.workflow.update_market_price("SOLUSDT", current_price=106.50)
        self.assertEqual(len(tp_trades), 1)
        self.assertEqual(tp_trades[0].exit_reason, "TAKE_PROFIT_TRIGGERED")
        self.assertEqual(tp_trades[0].exit_price, 106.0)
        self.assertGreater(tp_trades[0].gross_pnl, 0.0)

    # -------------------------------------------------------------------------
    # Scenario H: Multiple candidates processed with mixed outcomes
    # -------------------------------------------------------------------------
    def test_scenario_h_multiple_candidates_mixed_outcomes(self):
        c1_bull = make_test_market_analysis(
            symbol="BTCUSDT",
            regime=Regime.BULLISH,
            last_close=50000.0,
            ema_9=47500.0,
            ema_21=45000.0,
            atr_14=500.0,
        )
        c2_fail = make_test_market_analysis(
            symbol="BADUSDT",
            success=False,
            error="Data parse failure",
        )
        c3_flat = make_test_market_analysis(
            symbol="FLATUSDT",
            regime=Regime.BULLISH,
            last_close=100.0,
            ema_9=95.0,
            ema_21=95.0,
            atr_14=1.0,
        )
        c4_bear = make_test_market_analysis(
            symbol="ETHUSDT",
            regime=Regime.BEARISH,
            last_close=3000.0,
            ema_9=3200.0,
            ema_21=3400.0,
            atr_14=30.0,
        )

        results = self.workflow.process_shortlist([c1_bull, c2_fail, c3_flat, c4_bear])

        self.assertEqual(len(results), 4)

        # Candidate 1: Filled LONG
        self.assertEqual(results[0].symbol, "BTCUSDT")
        self.assertEqual(results[0].status, CandidateProcessingStatus.PAPER_FILLED)
        self.assertEqual(results[0].position.direction, SignalDirection.LONG)

        # Candidate 2: Ineligible
        self.assertEqual(results[1].symbol, "BADUSDT")
        self.assertEqual(results[1].status, CandidateProcessingStatus.INELIGIBLE)

        # Candidate 3: Flat
        self.assertEqual(results[2].symbol, "FLATUSDT")
        self.assertEqual(results[2].status, CandidateProcessingStatus.NO_ACTIONABLE_SIGNAL)

        # Candidate 4: Filled SHORT
        self.assertEqual(results[3].symbol, "ETHUSDT")
        self.assertEqual(results[3].status, CandidateProcessingStatus.PAPER_FILLED)
        self.assertEqual(results[3].position.direction, SignalDirection.SHORT)

        # Portfolio cleanly holds exactly 2 active positions
        open_positions = self.workflow.get_open_positions()
        self.assertEqual(len(open_positions), 2)
        symbols = {p.symbol for p in open_positions}
        self.assertEqual(symbols, {"BTCUSDT", "ETHUSDT"})

    # -------------------------------------------------------------------------
    # Scenario I: Missing/invalid candle or price data handled safely
    # -------------------------------------------------------------------------
    def test_scenario_i_missing_or_invalid_price_data(self):
        # Empty candles and missing ranking
        analysis_empty = MarketAnalysis(
            symbol="EMPTYUSDT",
            ranking=None,
            candles_by_granularity={},
            features_by_granularity={},
            regimes_by_granularity={},
            success=True,
            error=None,
        )

        # Process should fail gracefully without throwing exceptions
        res = self.workflow.process_market_analysis(analysis_empty)
        self.assertTrue(res.is_rejected)
        # Verify no open positions and portfolio clean
        self.assertEqual(len(self.workflow.get_open_positions()), 0)

        # Price update with invalid non-numeric price should raise ValueError safely
        with self.assertRaises(ValueError):
            self.workflow.update_market_price("BTCUSDT", float("nan"))

        with self.assertRaises(ValueError):
            self.workflow.update_market_price("BTCUSDT", -50.0)

    # -------------------------------------------------------------------------
    # Scenario J: Safety lock: strictly offline, no exchange credentials required
    # -------------------------------------------------------------------------
    def test_scenario_j_strictly_offline_and_paper_isolated(self):
        analysis = make_test_market_analysis(
            symbol="BTCUSDT",
            regime=Regime.BULLISH,
            last_close=50000.0,
            ema_9=47500.0,
            ema_21=45000.0,
            atr_14=500.0,
        )

        res = self.workflow.process_market_analysis(analysis)
        self.assertTrue(res.is_paper)
        self.assertTrue(res.order.is_paper)
        self.assertTrue(res.position.is_paper)
        self.assertTrue(res.execution_result.portfolio.is_paper)

        # Verify no mock exchange order ID or private endpoint was generated
        self.assertFalse(res.order.order_id.startswith("exchange_"))
        self.assertTrue(res.order.order_id.startswith("paper_ord_"))
        self.assertTrue(res.position.position_id.startswith("paper_pos_"))

        # Close position and check paper trade isolation
        trade = self.workflow.close_position(
            position_id=res.position.position_id,
            exit_price=50500.0,
        )
        self.assertTrue(trade.is_paper)
        self.assertTrue(trade.trade_id.startswith("paper_trd_"))

    # -------------------------------------------------------------------------
    # Model Immutability & Contract Invariants
    # -------------------------------------------------------------------------
    def test_candidate_workflow_result_immutability(self):
        analysis = make_test_market_analysis(symbol="BTCUSDT")
        res = self.workflow.process_market_analysis(analysis)

        with self.assertRaises(FrozenInstanceError):
            res.status = CandidateProcessingStatus.INELIGIBLE  # Mutating frozen dataclass

        with self.assertRaises(TypeError):
            res.metadata["tampered"] = True  # Mutating MappingProxyType


if __name__ == "__main__":
    unittest.main()
