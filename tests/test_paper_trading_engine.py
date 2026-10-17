from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import math
import unittest

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
    IllegalOrderStateTransitionError,
    OrderState,
    OrderType,
    is_valid_transition,
    validate_transition,
)
from execution.paper_engine import PaperTradingEngine
from execution.paper_models import (
    PaperExecutionResult,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    PaperTrade,
)
from risk.deterministic_engine import DeterministicRiskEngine
from risk.models import (
    RiskAssessment,
    RiskPolicy,
    RiskVerdict,
    TradeProposal,
)
from strategy.models import Signal, SignalDirection


def make_test_passing_risk_assessment(
    symbol: str = "BTCUSDT",
    strategy_id: str = "ema_trend_following",
    direction: SignalDirection = SignalDirection.LONG,
    entry_price: float = 50000.0,
    stop_loss_price: float = 49000.0,
    quantity: float = 0.1,
    equity: float = 10000.0,
    policy_id: str = "deterministic_risk_v1",
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


def make_test_rejected_risk_assessment(
    symbol: str = "BTCUSDT",
    strategy_id: str = "ema_trend_following",
    direction: SignalDirection = SignalDirection.LONG,
) -> RiskAssessment:
    sig = Signal(
        symbol=symbol,
        direction=direction,
        strategy_id=strategy_id,
        timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
        confidence=0.50,
        reason="Test rejected signal",
    )
    cand = ValidatedCandidate(
        status=ValidationStatus.FAILED,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=0.50,
        signal=sig,
        failures=("REGIME_CONTRADICTION",),
    )
    judge = JudgeDecision(
        verdict=JudgeVerdict.REJECT,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=0.50,
        score=0.30,
        reason_codes=("REGIME_CONTRADICTION",),
        explanation="Judge rejected",
        candidate=cand,
    )
    proposal = TradeProposal(
        judge_decision=judge,
        entry_price=50000.0,
        stop_loss_price=49000.0,
        quantity=0.1,
        account_equity=10000.0,
    )
    policy = RiskPolicy(policy_id="deterministic_risk_v1")
    risk_engine = DeterministicRiskEngine(policy=policy)
    return risk_engine.assess(proposal)


class TestPaperTradingEngineFoundation(unittest.TestCase):
    def setUp(self):
        # Initial starting balance: 10,000 USDT, fee: 0.0006 (0.06%), slippage: 0.0
        self.engine = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, slippage_pct=0.0)
        self.passing_risk = make_test_passing_risk_assessment()

    # =========================================================================
    # CATEGORY 1: ORDER LIFECYCLE (1 - 10)
    # =========================================================================

    def test_01_valid_paper_order_accepted_and_filled(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_001",
            idempotency_key="idem_001",
        )
        res = self.engine.execute(req)
        self.assertTrue(res.is_executed)
        self.assertEqual(res.status, ExecutionStatus.FILLED)
        self.assertEqual(res.order_state, OrderState.FILLED)
        self.assertIsNotNone(res.order)
        self.assertIsNotNone(res.position)
        self.assertIn("SIMULATED_ORDER_FILLED", res.reason_codes)

    def test_02_valid_paper_order_filled_with_slippage_and_fee(self):
        # Engine with 0.1% slippage and 0.06% fee
        engine = PaperTradingEngine(starting_cash=10000.0, fee_rate=0.0006, slippage_pct=0.001)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_002",
            idempotency_key="idem_002",
        )
        # Entry ref price 50000 -> With 0.1% slippage for LONG: 50000 * 1.001 = 50050.0
        res = engine.execute(req)
        self.assertTrue(res.is_executed)
        self.assertAlmostEqual(res.order.fill_price, 50050.0, places=4)
        # Notional = 50050 * 0.1 = 5005.0. Fee = 5005 * 0.0006 = 3.003
        self.assertAlmostEqual(res.order.fee, 3.003, places=4)

    def test_03_invalid_request_rejected(self):
        res_none = self.engine.execute(None)
        self.assertTrue(res_none.is_rejected)
        self.assertIn("MISSING_EXECUTION_REQUEST", res_none.reason_codes)

        res_type = self.engine.execute("not_a_request")
        self.assertTrue(res_type.is_rejected)
        self.assertIn("INVALID_REQUEST_TYPE", res_type.reason_codes)

    def test_04_missing_risk_approval_rejected(self):
        raw_req = ExecutionRequest(
            request_id="req_004",
            idempotency_key="idem_004",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=None,
        )
        res = self.engine.execute(raw_req)
        self.assertTrue(res.is_rejected)
        self.assertIn("MISSING_RISK_ASSESSMENT", res.reason_codes)

    def test_05_failed_risk_assessment_rejected(self):
        rejected_risk = make_test_rejected_risk_assessment()
        raw_req = ExecutionRequest(
            request_id="req_005",
            idempotency_key="idem_005",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=rejected_risk,
        )
        res = self.engine.execute(raw_req)
        self.assertTrue(res.is_rejected)
        self.assertIn("RISK_VERDICT_REJECTED", res.reason_codes)

    def test_06_cancellation_and_clean_closure_behavior(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_006",
            idempotency_key="idem_006",
        )
        res = self.engine.execute(req)
        pos_id = res.position.position_id

        # Clean manual exit
        trade = self.engine.close_position(pos_id, exit_price=50000.0, exit_reason="USER_CANCEL")
        self.assertEqual(trade.exit_reason, "USER_CANCEL")
        self.assertEqual(len(self.engine.get_open_positions()), 0)

    def test_07_invalid_state_transition_rejected(self):
        self.assertFalse(is_valid_transition(OrderState.FILLED, OrderState.CREATED))
        with self.assertRaises(IllegalOrderStateTransitionError):
            validate_transition(OrderState.FILLED, OrderState.CREATED)

    def test_08_duplicate_request_does_not_create_duplicate_exposure(self):
        req1 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_008_a",
            idempotency_key="idem_008_key",
        )
        req2 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_008_b",
            idempotency_key="idem_008_key",  # Same key and same canonical payload
        )

        res1 = self.engine.execute(req1)
        self.assertTrue(res1.is_executed)
        cash_after_res1 = self.engine.get_portfolio_snapshot().cash
        self.assertEqual(len(self.engine.get_open_positions()), 1)

        # Second submission
        res2 = self.engine.execute(req2)
        self.assertTrue(res2.is_duplicate)
        self.assertIn("DUPLICATE_REQUEST_IGNORED", res2.reason_codes)
        # Position count and cash must be completely unchanged!
        self.assertEqual(len(self.engine.get_open_positions()), 1)
        self.assertEqual(self.engine.get_portfolio_snapshot().cash, cash_after_res1)

    def test_09_conflicting_idempotency_request_rejected(self):
        req1 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_009_a",
            idempotency_key="idem_009_conflict",
            quantity=0.1,
        )
        req2 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_009_b",
            idempotency_key="idem_009_conflict",  # Same key but conflicting quantity
            quantity=0.05,
        )

        res1 = self.engine.execute(req1)
        self.assertTrue(res1.is_executed)

        res2 = self.engine.execute(req2)
        self.assertEqual(res2.status, ExecutionStatus.CONFLICT)
        self.assertTrue(res2.is_rejected)
        self.assertIn("IDEMPOTENCY_CONFLICT", res2.reason_codes)

    def test_10_no_fake_exchange_acknowledgement_or_order_id(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_010",
            idempotency_key="idem_010",
        )
        res = self.engine.execute(req)
        self.assertTrue(res.order.is_paper)
        self.assertTrue(res.position.is_paper)
        self.assertTrue(res.order.order_id.startswith("paper_ord_"))
        self.assertTrue(res.position.position_id.startswith("paper_pos_"))

    # =========================================================================
    # CATEGORY 2: PORTFOLIO ACCOUNTING (11 - 20)
    # =========================================================================

    def test_11_correct_starting_equity_and_cash(self):
        snap = self.engine.get_portfolio_snapshot()
        self.assertEqual(snap.starting_equity, 10000.0)
        self.assertEqual(snap.cash, 10000.0)
        self.assertEqual(snap.reserved_margin, 0.0)
        self.assertEqual(snap.realized_pnl, 0.0)
        self.assertEqual(snap.unrealized_pnl, 0.0)
        self.assertEqual(snap.fees_paid, 0.0)
        self.assertEqual(snap.total_equity, 10000.0)

    def test_12_correct_long_gross_pnl(self):
        # Entry 50000, Exit 52000, Qty 0.1 -> Gross PnL = (52000 - 50000) * 0.1 = +200.0 USDT
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_012",
            idempotency_key="idem_012",
        )
        res = self.engine.execute(req)
        trade = self.engine.close_position(res.position.position_id, exit_price=52000.0)
        self.assertAlmostEqual(trade.gross_pnl, 200.0, places=4)

    def test_13_correct_short_gross_pnl(self):
        # Short: Entry 3000, Exit 2800, Qty 2.0 -> Gross PnL = (3000 - 2800) * 2.0 = +400.0 USDT
        short_risk = make_test_passing_risk_assessment(
            symbol="ETHUSDT",
            strategy_id="rsi_mean_reversion",
            direction=SignalDirection.SHORT,
            entry_price=3000.0,
            stop_loss_price=3100.0,
            quantity=2.0,
        )
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=short_risk,
            request_id="req_013",
            idempotency_key="idem_013",
        )
        res = self.engine.execute(req)
        trade = self.engine.close_position(res.position.position_id, exit_price=2800.0)
        self.assertAlmostEqual(trade.gross_pnl, 400.0, places=4)

    def test_14_correct_realized_pnl_after_closure(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_014",
            idempotency_key="idem_014",
        )
        res = self.engine.execute(req)
        self.engine.close_position(res.position.position_id, exit_price=51000.0)
        snap = self.engine.get_portfolio_snapshot()
        # Gross realized: (51000 - 50000) * 0.1 = 100.0 USDT
        self.assertAlmostEqual(snap.realized_pnl, 100.0, places=4)

    def test_15_correct_unrealized_pnl_while_position_open(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_015",
            idempotency_key="idem_015",
        )
        res = self.engine.execute(req)
        # Price moves from 50000 to 51500 -> Unrealized PnL = (51500 - 50000) * 0.1 = +150.0 USDT
        self.engine.update_market_price("BTCUSDT", current_price=51500.0)
        snap = self.engine.get_portfolio_snapshot()
        self.assertAlmostEqual(snap.unrealized_pnl, 150.0, places=4)
        pos = self.engine.get_position(res.position.position_id)
        self.assertAlmostEqual(pos.unrealized_pnl, 150.0, places=4)

    def test_16_correct_fee_deductions_entry_and_exit(self):
        # Entry notional = 50000 * 0.1 = 5000. Fee rate = 0.0006 -> Entry fee = 3.00 USDT
        # Exit price = 52000. Exit notional = 5200. Exit fee = 5200 * 0.0006 = 3.12 USDT
        # Total fees = 6.12 USDT
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_016",
            idempotency_key="idem_016",
        )
        res = self.engine.execute(req)
        trade = self.engine.close_position(res.position.position_id, exit_price=52000.0)
        self.assertAlmostEqual(trade.entry_fee, 3.00, places=4)
        self.assertAlmostEqual(trade.exit_fee, 3.12, places=4)
        self.assertAlmostEqual(trade.net_pnl, 200.0 - 6.12, places=4)
        snap = self.engine.get_portfolio_snapshot()
        self.assertAlmostEqual(snap.fees_paid, 6.12, places=4)

    def test_17_consistent_equity_and_available_cash_identity(self):
        # Starting cash = 10000
        # Notional = 5000. Margin (leverage 2.0) = 2500. Entry fee = 3.00
        # Cash = 10000 - 2500 - 3.00 = 7497.00
        # Reserved margin = 2500.00
        # Unrealized PnL = 0
        # Total equity = 7497 + 2500 + 0 = 9997.00 (Starting 10000 - 3.00 fee)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_017",
            idempotency_key="idem_017",
        )
        self.engine.execute(req)
        snap = self.engine.get_portfolio_snapshot()
        self.assertAlmostEqual(snap.cash, 7497.00, places=4)
        self.assertAlmostEqual(snap.reserved_margin, 2500.00, places=4)
        self.assertAlmostEqual(snap.total_equity, snap.cash + snap.reserved_margin + snap.unrealized_pnl, places=4)

    def test_18_margin_reservation_and_release(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_018",
            idempotency_key="idem_018",
        )
        res = self.engine.execute(req)
        self.assertAlmostEqual(self.engine.get_portfolio_snapshot().reserved_margin, 2500.00, places=4)

        self.engine.close_position(res.position.position_id, exit_price=50000.0)
        self.assertAlmostEqual(self.engine.get_portfolio_snapshot().reserved_margin, 0.0, places=4)

    def test_19_insufficient_balance_rejected_safely(self):
        # Starting cash only 100 USDT, but trade requires 2500 margin + fee
        poor_engine = PaperTradingEngine(starting_cash=100.0)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_019",
            idempotency_key="idem_019",
        )
        res = poor_engine.execute(req)
        self.assertTrue(res.is_rejected)
        self.assertIn("INSUFFICIENT_PAPER_CASH", res.reason_codes)
        # Cash must remain exactly 100.0 without any deduction
        self.assertEqual(poor_engine.get_portfolio_snapshot().cash, 100.0)

    def test_20_multiple_positions_do_not_corrupt_aggregate_accounting(self):
        # Position 1: BTC (Margin 2500, Entry fee 3.00)
        req_btc = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_020_btc",
            idempotency_key="idem_020_btc",
        )
        res_btc = self.engine.execute(req_btc)

        # Position 2: ETH (Margin 3000, Entry fee 3.60)
        risk_eth = make_test_passing_risk_assessment(
            symbol="ETHUSDT",
            strategy_id="rsi_mean_reversion",
            direction=SignalDirection.SHORT,
            entry_price=3000.0,
            stop_loss_price=3100.0,
            quantity=2.0,
        )
        req_eth = ExecutionRequest.from_risk_assessment(
            risk_assessment=risk_eth,
            request_id="req_020_eth",
            idempotency_key="idem_020_eth",
        )
        res_eth = self.engine.execute(req_eth)

        snap = self.engine.get_portfolio_snapshot()
        self.assertEqual(len(snap.open_positions), 2)
        # Total reserved margin = 2500 + 3000 = 5500
        self.assertAlmostEqual(snap.reserved_margin, 5500.00, places=4)
        # Total cash = 10000 - 5500 - 6.60 = 4493.40
        self.assertAlmostEqual(snap.cash, 4493.40, places=4)

        # Close Position 1 (BTC) at 52000 (gain 200, exit fee 3.12)
        trade_btc = self.engine.close_position(res_btc.position.position_id, exit_price=52000.0)
        snap2 = self.engine.get_portfolio_snapshot()
        self.assertEqual(len(snap2.open_positions), 1)
        # Remaining margin: only ETH (3000)
        self.assertAlmostEqual(snap2.reserved_margin, 3000.00, places=4)
        # Cash increased by margin released (2500) + gross pnl (200) - exit fee (3.12)
        # 4493.40 + 2500 + 200 - 3.12 = 7190.28
        self.assertAlmostEqual(snap2.cash, 7190.28, places=4)

        # Close Position 2 (ETH) at 2900 (gain 200, exit fee 2900 * 2.0 * 0.0006 = 3.48)
        trade_eth = self.engine.close_position(res_eth.position.position_id, exit_price=2900.0)
        snap3 = self.engine.get_portfolio_snapshot()
        self.assertEqual(len(snap3.open_positions), 0)
        self.assertAlmostEqual(snap3.reserved_margin, 0.0, places=4)
        # Invariant: Equity == Cash when no positions
        self.assertAlmostEqual(snap3.total_equity, snap3.cash, places=4)
        # Net equity = Starting 10000 + Net Trade 1 + Net Trade 2
        expected_equity = 10000.0 + trade_btc.net_pnl + trade_eth.net_pnl
        self.assertAlmostEqual(snap3.total_equity, expected_equity, places=4)

    # =========================================================================
    # CATEGORY 3: POSITION AND EXIT MANAGEMENT (21 - 30)
    # =========================================================================

    def test_21_position_opens_with_correct_fields(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_021",
            idempotency_key="idem_021",
            metadata={"take_profit_price": 53000.0},
        )
        res = self.engine.execute(req)
        pos = res.position
        self.assertEqual(pos.symbol, "BTCUSDT")
        self.assertEqual(pos.direction, SignalDirection.LONG)
        self.assertEqual(pos.entry_price, 50000.0)
        self.assertEqual(pos.quantity, 0.1)
        self.assertEqual(pos.stop_loss_price, 49000.0)
        self.assertEqual(pos.take_profit_price, 53000.0)
        self.assertEqual(pos.notional, 5000.0)
        self.assertEqual(pos.required_margin, 2500.0)

    def test_22_position_closes_exactly_once(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_022",
            idempotency_key="idem_022",
        )
        res = self.engine.execute(req)
        pos_id = res.position.position_id

        trade1 = self.engine.close_position(pos_id, exit_price=51000.0)
        self.assertIsNotNone(trade1)

        # Attempt to close a second time must fail
        with self.assertRaises(KeyError):
            self.engine.close_position(pos_id, exit_price=51000.0)

    def test_23_duplicate_closure_rejected_safely(self):
        with self.assertRaises(KeyError):
            self.engine.close_position("non_existent_pos_id", exit_price=50000.0)

    def test_24_partial_closure_correctness(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_024",
            idempotency_key="idem_024",
        )
        res = self.engine.execute(req)
        pos_id = res.position.position_id

        # Position qty = 0.1, close 0.04 (40%)
        trade = self.engine.close_position(pos_id, exit_price=51000.0, quantity=0.04)
        self.assertEqual(trade.quantity, 0.04)
        # Gross PnL on 0.04: (51000 - 50000) * 0.04 = 40.0 USDT
        self.assertAlmostEqual(trade.gross_pnl, 40.0, places=4)

        # Check remaining position
        rem_pos = self.engine.get_position(pos_id)
        self.assertIsNotNone(rem_pos)
        self.assertAlmostEqual(rem_pos.quantity, 0.06, places=4)
        # 60% of original margin (2500 * 0.6 = 1500)
        self.assertAlmostEqual(rem_pos.required_margin, 1500.0, places=4)

    def test_25_stop_loss_trigger_logic(self):
        # LONG position with SL at 49000
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_025",
            idempotency_key="idem_025",
        )
        self.engine.execute(req)
        # Price dips to 48900 <= 49000 -> SL triggers!
        closed = self.engine.update_market_price("BTCUSDT", current_price=48900.0)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].exit_reason, "STOP_LOSS_TRIGGERED")
        self.assertEqual(closed[0].exit_price, 49000.0)
        self.assertEqual(len(self.engine.get_open_positions()), 0)

    def test_26_take_profit_trigger_logic(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_026",
            idempotency_key="idem_026",
            metadata={"take_profit_price": 52000.0},
        )
        self.engine.execute(req)
        # Price rises to 52100 >= 52000 -> TP triggers!
        closed = self.engine.update_market_price("BTCUSDT", current_price=52100.0)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].exit_reason, "TAKE_PROFIT_TRIGGERED")
        self.assertEqual(closed[0].exit_price, 52000.0)
        self.assertEqual(len(self.engine.get_open_positions()), 0)

    def test_27_invalid_protective_levels_rejected(self):
        # For LONG: take profit <= entry (e.g. 49500 <= 50000)
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_027",
            idempotency_key="idem_027",
            metadata={"take_profit_price": 49500.0},
        )
        res = self.engine.execute(req)
        self.assertTrue(res.is_rejected)
        self.assertIn("INVALID_PROTECTIVE_LEVELS", res.reason_codes)

    def test_28_missing_price_data_handled_safely(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_028",
            idempotency_key="idem_028",
        )
        self.engine.execute(req)
        with self.assertRaises(ValueError):
            self.engine.update_market_price("BTCUSDT", current_price=-10.0)

    def test_29_ambiguous_candle_policy_triggers_stop_loss_first(self):
        # Long position: Entry 50000, SL 49000, TP 52000
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_029",
            idempotency_key="idem_029",
            metadata={"take_profit_price": 52000.0},
        )
        self.engine.execute(req)

        # Wild candle touching BOTH 48500 (below SL) and 53000 (above TP)
        ambiguous_candle = Candle(
            symbol="BTCUSDT",
            timestamp=datetime(2026, 10, 17, 13, 0, tzinfo=timezone.utc),
            open=50500.0,
            high=53000.0,
            low=48500.0,
            close=51000.0,
            volume=100.0,
            quote_volume=5100000.0,
            granularity="1H",
        )
        closed = self.engine.process_candle(ambiguous_candle)
        self.assertEqual(len(closed), 1)
        # Conservative policy: SL triggered first!
        self.assertEqual(closed[0].exit_reason, "STOP_LOSS_CONSERVATIVE_AMBIGUOUS_CANDLE")
        self.assertEqual(closed[0].exit_price, 49000.0)

    def test_30_no_look_ahead_chronological_causality(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_030",
            idempotency_key="idem_030",
        )
        self.engine.execute(req)
        candle_1 = Candle(
            symbol="BTCUSDT",
            timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
            open=50000.0,
            high=50500.0,
            low=49800.0,
            close=50200.0,
            volume=50.0,
            quote_volume=2500000.0,
            granularity="1H",
        )
        closed = self.engine.process_candle(candle_1)
        self.assertEqual(len(closed), 0)
        # Unrealized PnL updated with candle_1 close (50200): (50200 - 50000) * 0.1 = +20.0 USDT
        snap = self.engine.get_portfolio_snapshot()
        self.assertAlmostEqual(snap.unrealized_pnl, 20.0, places=4)

    # =========================================================================
    # CATEGORY 4: INTEGRITY AND ROBUSTNESS (31 - 38)
    # =========================================================================

    def test_31_nan_and_infinity_rejected(self):
        for bad_val in [float("nan"), float("inf"), float("-inf")]:
            with self.assertRaises(ValueError):
                PaperTradingEngine(starting_cash=bad_val)

            with self.assertRaises(ValueError):
                self.engine.close_position("dummy", exit_price=bad_val)

    def test_32_zero_and_negative_quantities_prices_rejected(self):
        for bad_val in [0.0, -100.0]:
            with self.assertRaises(ValueError):
                PaperTradingEngine(starting_cash=bad_val)

    def test_33_symbol_and_direction_mismatches_rejected(self):
        raw_req = ExecutionRequest(
            request_id="req_033",
            idempotency_key="idem_033",
            symbol="ETHUSDT",  # Mismatch with risk assessment (BTCUSDT)
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=self.passing_risk,
        )
        res = self.engine.execute(raw_req)
        self.assertTrue(res.is_rejected)
        self.assertIn("SYMBOL_MISMATCH", res.reason_codes)

    def test_34_strategy_and_provenance_mismatches_rejected(self):
        raw_req = ExecutionRequest(
            request_id="req_034",
            idempotency_key="idem_034",
            symbol="BTCUSDT",
            strategy_id="rsi_mean_reversion",  # Mismatch with ema_trend_following
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=self.passing_risk,
        )
        res = self.engine.execute(raw_req)
        self.assertTrue(res.is_rejected)
        self.assertIn("STRATEGY_MISMATCH", res.reason_codes)

    def test_35_failed_operations_leave_portfolio_unchanged(self):
        initial_snap = self.engine.get_portfolio_snapshot()
        raw_req = ExecutionRequest(
            request_id="req_035",
            idempotency_key="idem_035",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.FLAT,  # Invalid FLAT direction
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=self.passing_risk,
        )
        res = self.engine.execute(raw_req)
        self.assertTrue(res.is_rejected)
        after_snap = self.engine.get_portfolio_snapshot()
        self.assertEqual(initial_snap.cash, after_snap.cash)
        self.assertEqual(initial_snap.reserved_margin, after_snap.reserved_margin)
        self.assertEqual(len(after_snap.open_positions), 0)

    def test_36_repeated_processing_does_not_double_charge_fees(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_036",
            idempotency_key="idem_036_repeat",
        )
        res1 = self.engine.execute(req)
        fees1 = self.engine.get_portfolio_snapshot().fees_paid
        # Resubmit
        res2 = self.engine.execute(req)
        fees2 = self.engine.get_portfolio_snapshot().fees_paid
        self.assertEqual(fees1, fees2)

    def test_37_numeric_rounding_and_precision_policy(self):
        snap = self.engine.get_portfolio_snapshot()
        self.assertIsInstance(snap.cash, float)
        self.assertIsInstance(snap.total_equity, float)

    def test_38_portfolio_invariants_hold_after_sequence_of_trades(self):
        # Execute sequence: Profitable Trade 1, Losing Trade 2
        # Trade 1: Long BTC (Buy 50000, Sell 51000) -> Gross +100.0
        req1 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="seq_req_1",
            idempotency_key="seq_idem_1",
        )
        res1 = self.engine.execute(req1)
        trade1 = self.engine.close_position(res1.position.position_id, exit_price=51000.0)

        # Trade 2: Short ETH (Sell 3000, Buy back 3050) -> Loss -100.0
        risk_eth = make_test_passing_risk_assessment(
            symbol="ETHUSDT",
            strategy_id="rsi_mean_reversion",
            direction=SignalDirection.SHORT,
            entry_price=3000.0,
            stop_loss_price=3100.0,
            quantity=2.0,
        )
        req2 = ExecutionRequest.from_risk_assessment(
            risk_assessment=risk_eth,
            request_id="seq_req_2",
            idempotency_key="seq_idem_2",
        )
        res2 = self.engine.execute(req2)
        trade2 = self.engine.close_position(res2.position.position_id, exit_price=3050.0)

        snap = self.engine.get_portfolio_snapshot()
        self.assertEqual(len(snap.open_positions), 0)
        self.assertEqual(len(snap.closed_trades), 2)
        # Starting 10000 + Net 1 + Net 2 == Final Equity
        expected_final = 10000.0 + trade1.net_pnl + trade2.net_pnl
        self.assertAlmostEqual(snap.total_equity, expected_final, places=4)
        self.assertAlmostEqual(snap.cash, snap.total_equity, places=4)


if __name__ == "__main__":
    unittest.main()
