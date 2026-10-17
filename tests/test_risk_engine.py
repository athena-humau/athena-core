from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import MappingProxyType
import unittest

from ai.deterministic_judge import DeterministicJudge
from ai.deterministic_validator import DeterministicSignalValidator
from ai.models import (
    FactorEvidence,
    JudgeDecision,
    JudgeVerdict,
    ValidatedCandidate,
    ValidationStatus,
)
from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from regime.models import Regime, TimeframeRegime
from risk.base import BaseRiskEngine, RiskEngine
from risk.deterministic_engine import DeterministicRiskEngine
from risk.models import (
    RiskAssessment,
    RiskPolicy,
    RiskVerdict,
    TradeProposal,
)
from scanner.ranking_engine import RankedMarket
from strategy.context import StrategyContext
from strategy.models import Signal, SignalDirection


def make_test_context(
    symbol: str = "BTCUSDT",
    regime: Regime = Regime.BULLISH,
    ema_9: float = 66000.0,
    ema_21: float = 64000.0,
    last_close: float = 67000.0,
    rsi_14: float = 55.0,
    atr_14: float = 800.0,
) -> StrategyContext:
    tf_regime = TimeframeRegime(
        symbol=symbol,
        granularity="1H",
        timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
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
        granularity="1H",
        timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
        candle_count=50,
        values=feature_vals,
    )
    analysis = MarketAnalysis(
        symbol=symbol,
        ranking=RankedMarket(
            symbol=symbol,
            score=85.0,
            liquidity_score=90.0,
            movement_score=80.0,
            spread_score=85.0,
            open_interest_score=75.0,
            funding_score=95.0,
        ),
        candles_by_granularity={},
        features_by_granularity={"1H": tf_features},
        regimes_by_granularity={"1H": tf_regime},
        success=True,
        analyzed_at=datetime.now(timezone.utc),
    )
    return StrategyContext(analysis=analysis)


def make_test_judge_decision(
    verdict: JudgeVerdict = JudgeVerdict.PASS,
    symbol: str = "BTCUSDT",
    strategy_id: str = "ema_trend_following",
    direction: SignalDirection = SignalDirection.LONG,
    confidence: float = 0.85,
    score: float = 0.80,
    reason_codes: tuple[str, ...] = ("MULTIDIMENSIONAL_CRITERIA_SATISFIED",),
) -> JudgeDecision:
    sig = Signal(
        symbol=symbol,
        direction=direction,
        strategy_id=strategy_id,
        timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
        confidence=confidence,
        reason="Test candidate signal",
    )
    cand = ValidatedCandidate(
        status=ValidationStatus.PASSED if verdict == JudgeVerdict.PASS else ValidationStatus.FAILED,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=confidence,
        signal=sig,
        failures=() if verdict == JudgeVerdict.PASS else ("VALIDATION_FAILED",),
    )
    return JudgeDecision(
        verdict=verdict,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=confidence,
        score=score,
        reason_codes=reason_codes,
        explanation="Test judge decision explanation",
        candidate=cand,
    )


class TestRiskEngineFoundation(unittest.TestCase):
    def setUp(self):
        self.default_policy = RiskPolicy(
            policy_id="test_risk_v1",
            max_risk_per_trade_pct=2.0,
            max_position_notional=10000.0,
            max_aggregate_exposure=25000.0,
            max_leverage=10.0,
        )
        self.engine = DeterministicRiskEngine(policy=self.default_policy)
        self.passing_judge = make_test_judge_decision()

    # --- 1. MODEL INTEGRITY & IMMUTABILITY ---

    def test_risk_policy_valid_instantiation_and_immutability(self):
        policy = RiskPolicy(
            policy_id="conservative_v1",
            max_risk_per_trade_pct=1.5,
            max_position_notional=8000.0,
            max_aggregate_exposure=20000.0,
            max_leverage=5.0,
            max_symbol_exposure=10000.0,
            metadata={"source": "unit_test"},
        )
        self.assertEqual(policy.policy_id, "conservative_v1")
        self.assertEqual(policy.max_risk_per_trade_pct, 1.5)
        self.assertEqual(policy.max_position_notional, 8000.0)
        self.assertEqual(policy.max_aggregate_exposure, 20000.0)
        self.assertEqual(policy.max_leverage, 5.0)
        self.assertEqual(policy.max_symbol_exposure, 10000.0)
        self.assertIsInstance(policy.metadata, MappingProxyType)

        with self.assertRaises((FrozenInstanceError, AttributeError)):
            policy.max_risk_per_trade_pct = 3.0

        with self.assertRaises(TypeError):
            policy.metadata["new_key"] = "forbidden"

    def test_risk_policy_validation_failures(self):
        with self.assertRaises(ValueError):
            RiskPolicy(policy_id="")

        with self.assertRaises(ValueError):
            RiskPolicy(max_risk_per_trade_pct=0.0)

        with self.assertRaises(ValueError):
            RiskPolicy(max_risk_per_trade_pct=-1.0)

        with self.assertRaises(ValueError):
            RiskPolicy(max_risk_per_trade_pct=105.0)

        with self.assertRaises(ValueError):
            RiskPolicy(max_position_notional=0.0)

        with self.assertRaises(ValueError):
            RiskPolicy(max_aggregate_exposure=-100.0)

        with self.assertRaises(ValueError):
            RiskPolicy(max_leverage=0.5)

        with self.assertRaises(ValueError):
            RiskPolicy(max_symbol_exposure=0.0)

        with self.assertRaises(ValueError):
            RiskPolicy(min_risk_reward_ratio=-0.5)

        with self.assertRaises(ValueError):
            RiskPolicy(metadata=["not", "a", "mapping"])

    def test_trade_proposal_valid_instantiation_and_immutability(self):
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
            available_margin=5000.0,
            existing_exposure=2000.0,
            existing_symbol_exposure=1000.0,
            contract_multiplier=1.0,
            requested_leverage=2.0,
            proposal_id="prop_001",
            metadata={"origin": "signal_ranker"},
        )
        self.assertEqual(proposal.symbol, "BTCUSDT")
        self.assertEqual(proposal.strategy_id, "ema_trend_following")
        self.assertEqual(proposal.direction, SignalDirection.LONG)
        self.assertEqual(proposal.entry_price, 50000.0)
        self.assertEqual(proposal.stop_loss_price, 49000.0)
        self.assertEqual(proposal.quantity, 0.1)
        self.assertEqual(proposal.account_equity, 10000.0)
        self.assertEqual(proposal.available_margin, 5000.0)
        self.assertEqual(proposal.existing_exposure, 2000.0)
        self.assertEqual(proposal.contract_multiplier, 1.0)
        self.assertEqual(proposal.requested_leverage, 2.0)
        self.assertIsInstance(proposal.metadata, MappingProxyType)

        with self.assertRaises((FrozenInstanceError, AttributeError)):
            proposal.entry_price = 51000.0

        with self.assertRaises(TypeError):
            proposal.metadata["injected"] = True

    def test_risk_assessment_model_and_immutability(self):
        assessment = RiskAssessment(
            verdict=RiskVerdict.PASS,
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            notional=5000.0,
            estimated_loss=100.0,
            risk_percent=1.0,
            reason_codes=("RISK_CONSTRAINTS_SATISFIED",),
            explanation="All constraints passed",
            check_evidence={"test_check": {"status": "PASS"}},
            policy_id="test_risk_v1",
        )
        self.assertTrue(assessment.is_passed)
        self.assertFalse(assessment.is_rejected)
        self.assertEqual(assessment.verdict, RiskVerdict.PASS)
        self.assertEqual(assessment.notional, 5000.0)
        self.assertEqual(assessment.estimated_loss, 100.0)
        self.assertEqual(assessment.risk_percent, 1.0)
        self.assertIsInstance(assessment.check_evidence, MappingProxyType)

        with self.assertRaises((FrozenInstanceError, AttributeError)):
            assessment.verdict = RiskVerdict.REJECT

        with self.assertRaises(TypeError):
            assessment.check_evidence["mutate"] = 123

    # --- 2. ENGINE CONTRACT & ALIASES ---

    def test_engine_abstract_contract_and_alias(self):
        self.assertIs(BaseRiskEngine, RiskEngine)

        with self.assertRaises(TypeError):
            RiskEngine()

        class IncompleteEngine(RiskEngine):
            pass

        with self.assertRaises(TypeError):
            IncompleteEngine()

    # --- 3. INPUT VALIDATION & FAIL-CLOSED CHECKS ---

    def test_assess_none_proposal(self):
        assessment = self.engine.assess(None)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("MISSING_PROPOSAL", assessment.reason_codes)
        self.assertIsNone(assessment.proposal)

    def test_assess_invalid_proposal_type(self):
        assessment = self.engine.assess({"not": "a proposal"})
        self.assertTrue(assessment.is_rejected)
        self.assertIn("INVALID_PROPOSAL_TYPE", assessment.reason_codes)

    def test_assess_missing_judge_decision(self):
        proposal = TradeProposal(
            judge_decision=None,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("MISSING_JUDGE_DECISION", assessment.reason_codes)

    def test_assess_invalid_judge_decision_type(self):
        proposal = TradeProposal(
            judge_decision="invalid_decision_string",
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("INVALID_JUDGE_DECISION", assessment.reason_codes)

    def test_assess_rejected_judge_decision(self):
        rejected_judge = make_test_judge_decision(
            verdict=JudgeVerdict.REJECT,
            reason_codes=("REGIME_ALIGNMENT_CONTRADICTION",),
        )
        proposal = TradeProposal(
            judge_decision=rejected_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("JUDGE_VERDICT_REJECTED", assessment.reason_codes)

    def test_assess_flat_signal_direction(self):
        flat_judge = make_test_judge_decision(
            verdict=JudgeVerdict.PASS,
            direction=SignalDirection.FLAT,
        )
        proposal = TradeProposal(
            judge_decision=flat_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("FLAT_SIGNAL_NOT_ACTIONABLE", assessment.reason_codes)

    def test_assess_invalid_equity(self):
        proposal_zero = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=0.0,
        )
        assessment_zero = self.engine.assess(proposal_zero)
        self.assertTrue(assessment_zero.is_rejected)
        self.assertIn("INVALID_EQUITY", assessment_zero.reason_codes)

        proposal_negative = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=-5000.0,
        )
        assessment_neg = self.engine.assess(proposal_negative)
        self.assertTrue(assessment_neg.is_rejected)
        self.assertIn("INVALID_EQUITY", assessment_neg.reason_codes)

    def test_assess_invalid_prices_and_quantity(self):
        invalid_cases = [
            (0.0, 49000.0, 0.1, 1.0),
            (-50000.0, 49000.0, 0.1, 1.0),
            (50000.0, 0.0, 0.1, 1.0),
            (50000.0, -100.0, 0.1, 1.0),
            (50000.0, 49000.0, 0.0, 1.0),
            (50000.0, 49000.0, -0.5, 1.0),
            (50000.0, 49000.0, 0.1, 0.0),
        ]
        for ep, sl, qty, mult in invalid_cases:
            proposal = TradeProposal(
                judge_decision=self.passing_judge,
                entry_price=ep,
                stop_loss_price=sl,
                quantity=qty,
                contract_multiplier=mult,
                account_equity=10000.0,
            )
            assessment = self.engine.assess(proposal)
            self.assertTrue(assessment.is_rejected)
            self.assertIn("INVALID_PRICE_OR_QUANTITY", assessment.reason_codes)

    # --- 4. STOP PLACEMENT VERIFICATION ---

    def test_assess_long_wrong_side_stop(self):
        # Stop above entry
        proposal_above = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=51000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        assessment_above = self.engine.assess(proposal_above)
        self.assertTrue(assessment_above.is_rejected)
        self.assertIn("WRONG_SIDE_STOP", assessment_above.reason_codes)

        # Stop equal to entry
        proposal_equal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=50000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        assessment_equal = self.engine.assess(proposal_equal)
        self.assertTrue(assessment_equal.is_rejected)
        self.assertIn("WRONG_SIDE_STOP", assessment_equal.reason_codes)

    def test_assess_short_wrong_side_stop(self):
        short_judge = make_test_judge_decision(
            direction=SignalDirection.SHORT,
            strategy_id="rsi_mean_reversion",
        )
        # Stop below entry for short
        proposal_below = TradeProposal(
            judge_decision=short_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        assessment_below = self.engine.assess(proposal_below)
        self.assertTrue(assessment_below.is_rejected)
        self.assertIn("WRONG_SIDE_STOP", assessment_below.reason_codes)

        # Stop equal to entry for short
        proposal_equal = TradeProposal(
            judge_decision=short_judge,
            entry_price=50000.0,
            stop_loss_price=50000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        assessment_equal = self.engine.assess(proposal_equal)
        self.assertTrue(assessment_equal.is_rejected)
        self.assertIn("WRONG_SIDE_STOP", assessment_equal.reason_codes)

    # --- 5. QUANTITATIVE CALCULATIONS & PASS SCENARIOS ---

    def test_assess_valid_long_trade_pass(self):
        # Entry: 50,000, Stop: 49,000, Qty: 0.1, Equity: 10,000
        # Notional = 50,000 * 0.1 = 5,000 USDT
        # Gross loss = (50,000 - 49,000) * 0.1 = 100 USDT
        # Risk % = (100 / 10,000) * 100 = 1.0%
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
            available_margin=5000.0,
            requested_leverage=2.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_passed)
        self.assertEqual(assessment.verdict, RiskVerdict.PASS)
        self.assertAlmostEqual(assessment.notional, 5000.0, places=4)
        self.assertAlmostEqual(assessment.estimated_loss, 100.0, places=4)
        self.assertAlmostEqual(assessment.risk_percent, 1.0, places=4)
        self.assertEqual(assessment.reason_codes, ("RISK_CONSTRAINTS_SATISFIED",))
        self.assertEqual(assessment.check_evidence["risk_percent"]["status"], "PASS")
        self.assertEqual(assessment.check_evidence["position_notional"]["status"], "PASS")

    def test_assess_valid_short_trade_pass(self):
        # Entry: 3,000, Stop: 3,060, Qty: 2.0, Equity: 10,000
        # Notional = 3,000 * 2.0 = 6,000 USDT
        # Gross loss = (3,060 - 3,000) * 2.0 = 120 USDT
        # Risk % = (120 / 10,000) * 100 = 1.2%
        short_judge = make_test_judge_decision(
            direction=SignalDirection.SHORT,
            symbol="ETHUSDT",
            strategy_id="rsi_mean_reversion",
        )
        proposal = TradeProposal(
            judge_decision=short_judge,
            entry_price=3000.0,
            stop_loss_price=3060.0,
            quantity=2.0,
            account_equity=10000.0,
            requested_leverage=3.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_passed)
        self.assertEqual(assessment.verdict, RiskVerdict.PASS)
        self.assertEqual(assessment.symbol, "ETHUSDT")
        self.assertEqual(assessment.direction, SignalDirection.SHORT)
        self.assertAlmostEqual(assessment.notional, 6000.0, places=4)
        self.assertAlmostEqual(assessment.estimated_loss, 120.0, places=4)
        self.assertAlmostEqual(assessment.risk_percent, 1.2, places=4)

    # --- 6. RISK LIMIT VIOLATIONS ---

    def test_assess_exceeds_max_risk_per_trade(self):
        # Entry: 50,000, Stop: 47,000 (3,000 delta), Qty: 0.1, Equity: 10,000
        # Loss = 300 USDT -> 3.0% risk > 2.0% max
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=47000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("EXCEEDS_MAX_RISK_PER_TRADE", assessment.reason_codes)
        self.assertAlmostEqual(assessment.risk_percent, 3.0, places=4)
        self.assertEqual(assessment.check_evidence["risk_percent"]["status"], "FAIL")

    def test_assess_exceeds_position_notional_limit(self):
        # Entry: 50,000, Stop: 49,900, Qty: 0.3 (notional = 15,000 > 10,000 max)
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49900.0,
            quantity=0.3,
            account_equity=10000.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("EXCEEDS_POSITION_NOTIONAL_LIMIT", assessment.reason_codes)
        self.assertAlmostEqual(assessment.notional, 15000.0, places=4)

    def test_assess_exceeds_aggregate_exposure_limit(self):
        # Existing exposure = 22,000. Trade notional = 5,000. New agg = 27,000 > 25,000 max
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
            existing_exposure=22000.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("EXCEEDS_AGGREGATE_EXPOSURE_LIMIT", assessment.reason_codes)

    def test_assess_exceeds_symbol_exposure_limit(self):
        policy_with_sym = RiskPolicy(
            policy_id="sym_limit_policy",
            max_symbol_exposure=6000.0,
            max_position_notional=10000.0,
            max_aggregate_exposure=25000.0,
        )
        engine = DeterministicRiskEngine(policy=policy_with_sym)
        # Existing symbol exposure = 2,000. Trade notional = 5,000. New sym = 7,000 > 6,000 max
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
            existing_symbol_exposure=2000.0,
        )
        assessment = engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("EXCEEDS_SYMBOL_EXPOSURE_LIMIT", assessment.reason_codes)

    def test_assess_exceeds_max_leverage(self):
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
            requested_leverage=15.0,  # Max is 10.0
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("EXCEEDS_MAX_LEVERAGE", assessment.reason_codes)

    def test_assess_invalid_leverage_below_one(self):
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
            requested_leverage=0.5,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("INVALID_LEVERAGE", assessment.reason_codes)

    def test_assess_insufficient_margin(self):
        # Notional = 5,000, Leverage = 2.0 -> Required margin = 2,500
        # Available margin = 1,000 (< 2,500)
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
            available_margin=1000.0,
            requested_leverage=2.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("INSUFFICIENT_MARGIN", assessment.reason_codes)

    def test_assess_multiple_simultaneous_violations(self):
        # Exceeds position notional AND exceeds max risk per trade
        # Qty = 0.3 -> Notional = 15,000 (> 10,000)
        # Stop delta = 2,000 -> Loss = 600 USDT -> Risk = 6.0% (> 2.0%)
        # Leverage = 20.0 (> 10.0)
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=48000.0,
            quantity=0.3,
            account_equity=10000.0,
            requested_leverage=20.0,
        )
        assessment = self.engine.assess(proposal)
        self.assertTrue(assessment.is_rejected)
        self.assertIn("EXCEEDS_POSITION_NOTIONAL_LIMIT", assessment.reason_codes)
        self.assertIn("EXCEEDS_MAX_RISK_PER_TRADE", assessment.reason_codes)
        self.assertIn("EXCEEDS_MAX_LEVERAGE", assessment.reason_codes)
        self.assertEqual(len(assessment.reason_codes), 3)

    # --- 7. DETERMINISM & ORDER INDEPENDENCE ---

    def test_pure_determinism_across_multiple_runs(self):
        proposal = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
            available_margin=5000.0,
            requested_leverage=2.0,
        )
        reference = self.engine.assess(proposal)
        for _ in range(10):
            res = self.engine.assess(proposal)
            self.assertEqual(res.verdict, reference.verdict)
            self.assertEqual(res.notional, reference.notional)
            self.assertEqual(res.estimated_loss, reference.estimated_loss)
            self.assertEqual(res.risk_percent, reference.risk_percent)
            self.assertEqual(res.reason_codes, reference.reason_codes)
            self.assertEqual(res.explanation, reference.explanation)

    def test_order_independence(self):
        prop_pass = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        prop_fail = TradeProposal(
            judge_decision=self.passing_judge,
            entry_price=50000.0,
            stop_loss_price=45000.0,
            quantity=0.1,
            account_equity=10000.0,
        )

        res_pass_1 = self.engine.assess(prop_pass)
        res_fail_1 = self.engine.assess(prop_fail)

        res_fail_2 = self.engine.assess(prop_fail)
        res_pass_2 = self.engine.assess(prop_pass)

        self.assertEqual(res_pass_1.verdict, res_pass_2.verdict)
        self.assertEqual(res_pass_1.reason_codes, res_pass_2.reason_codes)
        self.assertEqual(res_fail_1.verdict, res_fail_2.verdict)
        self.assertEqual(res_fail_1.reason_codes, res_fail_2.reason_codes)

    # --- 8. END-TO-END PIPELINE INTEGRATION ---

    def test_end_to_end_pipeline_integration(self):
        # 1. Market context with Bullish regime and matching indicators
        context = make_test_context(
            symbol="BTCUSDT",
            regime=Regime.BULLISH,
            ema_9=66000.0,
            ema_21=64000.0,
            last_close=67000.0,
            rsi_14=55.0,
            atr_14=800.0,
        )

        # 2. Raw signal generated by strategy
        raw_signal = Signal(
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            strategy_id="ema_trend_following",
            timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
            confidence=0.85,
            reason="Bullish EMA crossover confirmed",
        )

        # 3. Validation step
        validator = DeterministicSignalValidator()
        validated_candidate = validator.validate(raw_signal, context)
        self.assertTrue(validated_candidate.is_valid)

        # 4. Multi-Factor Judge step
        judge = DeterministicJudge()
        judge_decision = judge.judge(validated_candidate, context)
        self.assertTrue(judge_decision.is_passed)
        self.assertEqual(judge_decision.verdict, JudgeVerdict.PASS)

        # 5. Risk assessment step
        proposal = TradeProposal(
            judge_decision=judge_decision,
            entry_price=67000.0,
            stop_loss_price=65660.0,  # 2x ATR stop (2 * 670)
            quantity=0.1,
            account_equity=10000.0,
            available_margin=5000.0,
            requested_leverage=2.0,
        )
        risk_assessment = self.engine.assess(proposal)
        self.assertTrue(risk_assessment.is_passed)
        self.assertEqual(risk_assessment.verdict, RiskVerdict.PASS)
        self.assertEqual(risk_assessment.symbol, "BTCUSDT")
        self.assertEqual(risk_assessment.strategy_id, "ema_trend_following")
        self.assertEqual(risk_assessment.direction, SignalDirection.LONG)
        self.assertAlmostEqual(risk_assessment.notional, 6700.0, places=2)
        self.assertAlmostEqual(risk_assessment.estimated_loss, 134.0, places=2)
        self.assertAlmostEqual(risk_assessment.risk_percent, 1.34, places=2)

    def test_end_to_end_pipeline_reject_cascade(self):
        # 1. Market context with Bearish regime
        context = make_test_context(
            symbol="BTCUSDT",
            regime=Regime.BEARISH,
            ema_9=64000.0,
            ema_21=66000.0,
            last_close=63000.0,
        )

        # 2. Contradictory LONG signal
        raw_signal = Signal(
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            strategy_id="ema_trend_following",
            timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
            confidence=0.75,
            reason="Contradictory signal",
        )

        # 3. Validation passes (signal is syntactically sound)
        validator = DeterministicSignalValidator()
        validated_candidate = validator.validate(raw_signal, context)
        self.assertTrue(validated_candidate.is_valid)

        # 4. Multi-Factor Judge rejects due to regime contradiction
        judge = DeterministicJudge()
        judge_decision = judge.judge(validated_candidate, context)
        self.assertTrue(judge_decision.is_rejected)
        self.assertEqual(judge_decision.verdict, JudgeVerdict.REJECT)

        # 5. Risk Engine fails closed because Judge rejected
        proposal = TradeProposal(
            judge_decision=judge_decision,
            entry_price=63000.0,
            stop_loss_price=62000.0,
            quantity=0.1,
            account_equity=10000.0,
        )
        risk_assessment = self.engine.assess(proposal)
        self.assertTrue(risk_assessment.is_rejected)
        self.assertIn("JUDGE_VERDICT_REJECTED", risk_assessment.reason_codes)


if __name__ == "__main__":
    unittest.main()
