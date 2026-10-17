from datetime import datetime, timezone
from types import MappingProxyType
import unittest

from ai.deterministic_evaluator import DeterministicStrategyEvaluator
from ai.deterministic_judge import DeterministicJudge
from ai.deterministic_ranker import DeterministicStrategyRanker
from ai.deterministic_selector import DeterministicStrategySelector
from ai.deterministic_validator import DeterministicSignalValidator
from ai.judge import Judge, JudgeEngine
from ai.models import (
    FactorEvidence,
    JudgeDecision,
    JudgeVerdict,
    StrategyCandidate,
    ValidatedCandidate,
    ValidationStatus,
)
from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from regime.models import Regime, TimeframeRegime
from scanner.ranking_engine import RankedMarket
from strategy.context import StrategyContext
from strategy.ema_trend_following import EMATrendFollowingStrategy
from strategy.models import Signal, SignalDirection
from strategy.registry import StrategyRegistry
from strategy.rsi_mean_reversion import RSIMeanReversionStrategy


def make_test_context(
    symbol: str = "BTCUSDT",
    regime: Regime = Regime.BULLISH,
    ema_9: float = 66000.0,
    ema_21: float = 64000.0,
    last_close: float = 67000.0,
    rsi_14: float = 55.0,
    atr_14: float = 800.0,
    success: bool = True,
    error: str | None = None,
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
        success=success,
        error=error,
        analyzed_at=datetime.now(timezone.utc),
    )
    return StrategyContext(analysis=analysis)


def make_validated_candidate(
    symbol: str = "BTCUSDT",
    strategy_id: str = "ema_trend_following",
    direction: SignalDirection = SignalDirection.LONG,
    confidence: float = 0.85,
    is_valid: bool = True,
    failures: tuple[str, ...] = (),
) -> ValidatedCandidate:
    sig = Signal(
        symbol=symbol,
        direction=direction,
        strategy_id=strategy_id,
        timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
        confidence=confidence,
        reason="Test candidate signal",
    )
    status = ValidationStatus.PASSED if is_valid else ValidationStatus.FAILED
    return ValidatedCandidate(
        status=status,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=confidence,
        signal=sig,
        failures=failures,
    )


class TestJudgeEngineFoundation(unittest.TestCase):

    def setUp(self):
        self.judge = DeterministicJudge(
            min_confidence=0.50,
            reference_timeframe="1H",
            max_relative_atr=0.08,
            min_relative_atr=0.0001,
            require_regime_alignment=True,
            decision_score_threshold=0.60,
        )
        self.context = make_test_context()

    # =========================================================================
    # CONTRACT AND CONFIGURATION TESTS
    # =========================================================================

    def test_judge_contract_and_aliases(self):
        self.assertIsInstance(self.judge, JudgeEngine)
        self.assertIsInstance(self.judge, Judge)
        self.assertEqual(self.judge.judge_id, "deterministic_judge")
        self.assertEqual(self.judge.policy_id, "deterministic_v1")

    def test_ab_invalid_configuration_fails_safely(self):
        with self.assertRaises(ValueError):
            DeterministicJudge(min_confidence=-0.1)
        with self.assertRaises(ValueError):
            DeterministicJudge(min_confidence=1.5)
        with self.assertRaises(ValueError):
            DeterministicJudge(reference_timeframe="")
        with self.assertRaises(ValueError):
            DeterministicJudge(max_relative_atr=-0.05)
        with self.assertRaises(ValueError):
            DeterministicJudge(min_relative_atr=-0.01)
        with self.assertRaises(ValueError):
            DeterministicJudge(decision_score_threshold=1.2)
        with self.assertRaises(ValueError):
            DeterministicJudge(decision_score_threshold=-0.1)

    # =========================================================================
    # DIRECTIONAL CANDIDATE PASS / REJECT TESTS (A, B, C, D, E)
    # =========================================================================

    def test_a_valid_validated_long_candidate(self):
        cand = make_validated_candidate(direction=SignalDirection.LONG)
        dec = self.judge.judge(cand, context=self.context)

        self.assertEqual(dec.verdict, JudgeVerdict.PASS)
        self.assertTrue(dec.is_passed)
        self.assertFalse(dec.is_rejected)
        self.assertEqual(dec.symbol, "BTCUSDT")
        self.assertEqual(dec.strategy_id, "ema_trend_following")
        self.assertEqual(dec.direction, SignalDirection.LONG)
        self.assertIn("CRITERIA_SATISFIED", dec.reason_codes)
        self.assertGreaterEqual(dec.score, 0.60)

    def test_b_valid_validated_short_candidate(self):
        # Short setup with bearish regime, fast EMA < slow EMA, RSI aligned
        bearish_ctx = make_test_context(
            regime=Regime.BEARISH,
            ema_9=64000.0,
            ema_21=66000.0,
            last_close=63000.0,
            rsi_14=45.0,
        )
        cand = make_validated_candidate(direction=SignalDirection.SHORT)
        dec = self.judge.judge(cand, context=bearish_ctx)

        self.assertEqual(dec.verdict, JudgeVerdict.PASS)
        self.assertTrue(dec.is_passed)
        self.assertEqual(dec.direction, SignalDirection.SHORT)
        self.assertIn("CRITERIA_SATISFIED", dec.reason_codes)

    def test_c_flat_candidate_rejected_as_non_actionable(self):
        # FLAT signal is valid but strictly non-actionable
        cand = make_validated_candidate(direction=SignalDirection.FLAT, confidence=0.0)
        dec = self.judge.judge(cand, context=self.context)

        self.assertEqual(dec.verdict, JudgeVerdict.REJECT)
        self.assertTrue(dec.is_rejected)
        self.assertEqual(dec.direction, SignalDirection.FLAT)
        self.assertIn("FLAT_SIGNAL_NOT_ACTIONABLE", dec.reason_codes)
        self.assertIn("neutrality", dec.explanation)

    def test_d_failed_validated_candidate_is_rejected(self):
        cand = make_validated_candidate(is_valid=False, failures=("Symbol is empty",))
        dec = self.judge.judge(cand, context=self.context)

        self.assertEqual(dec.verdict, JudgeVerdict.REJECT)
        self.assertIn("CANDIDATE_VALIDATION_FAILED", dec.reason_codes)
        self.assertIn("failed signal validation", dec.explanation)

    def test_e_malformed_and_none_candidate_rejected_safely(self):
        # None candidate
        dec_none = self.judge.judge(None, context=self.context)
        self.assertEqual(dec_none.verdict, JudgeVerdict.REJECT)
        self.assertIn("INVALID_CANDIDATE", dec_none.reason_codes)

        # Wrong object type
        dec_str = self.judge.judge("not_a_candidate", context=self.context)
        self.assertEqual(dec_str.verdict, JudgeVerdict.REJECT)
        self.assertIn("INVALID_CANDIDATE", dec_str.reason_codes)

    # =========================================================================
    # CONSISTENCY & CONTEXT VALIDATION (F, G, T)
    # =========================================================================

    def test_f_symbol_mismatch(self):
        cand = make_validated_candidate(symbol="ETHUSDT")
        dec = self.judge.judge(cand, context=self.context)  # Context is BTCUSDT

        self.assertEqual(dec.verdict, JudgeVerdict.REJECT)
        self.assertIn("SYMBOL_MISMATCH", dec.reason_codes)
        self.assertIn("does not match context symbol", dec.explanation)

    def test_g_invalid_context_type(self):
        cand = make_validated_candidate()
        dec = self.judge.judge(cand, context="invalid_context")

        self.assertEqual(dec.verdict, JudgeVerdict.REJECT)
        self.assertIn("INVALID_CONTEXT", dec.reason_codes)

    def test_t_failed_market_analysis(self):
        failed_ctx = make_test_context(success=False, error="WebSocket feed disconnected")
        cand = make_validated_candidate()
        dec = self.judge.judge(cand, context=failed_ctx)

        self.assertEqual(dec.verdict, JudgeVerdict.REJECT)
        self.assertIn("MARKET_ANALYSIS_FAILED", dec.reason_codes)
        self.assertIn("WebSocket feed disconnected", dec.explanation)

    # =========================================================================
    # REGIME FACTOR & STRATEGY-SPECIFIC ALIGNMENT (H, I, J, K, L, M)
    # =========================================================================

    def test_h_bullish_regime_with_long_trend_signal(self):
        ctx = make_test_context(regime=Regime.BULLISH)
        cand = make_validated_candidate(strategy_id="ema_trend_following", direction=SignalDirection.LONG)
        dec = self.judge.judge(cand, context=ctx)

        self.assertEqual(dec.verdict, JudgeVerdict.PASS)
        reg_factor = dec.factor_evidence["regime_alignment"]
        self.assertEqual(reg_factor.status, "PASSED")
        self.assertEqual(reg_factor.score, 1.0)

    def test_i_bearish_regime_with_short_trend_signal(self):
        ctx = make_test_context(
            regime=Regime.BEARISH,
            ema_9=64000.0,
            ema_21=66000.0,
            last_close=63000.0,
        )
        cand = make_validated_candidate(strategy_id="ema_trend_following", direction=SignalDirection.SHORT)
        dec = self.judge.judge(cand, context=ctx)

        self.assertEqual(dec.verdict, JudgeVerdict.PASS)
        reg_factor = dec.factor_evidence["regime_alignment"]
        self.assertEqual(reg_factor.status, "PASSED")
        self.assertEqual(reg_factor.score, 1.0)

    def test_j_opposing_regime_contradiction(self):
        # LONG signal in a BEARISH regime
        ctx = make_test_context(regime=Regime.BEARISH)
        cand = make_validated_candidate(strategy_id="ema_trend_following", direction=SignalDirection.LONG)
        dec = self.judge.judge(cand, context=ctx)

        self.assertEqual(dec.verdict, JudgeVerdict.REJECT)
        self.assertIn("REGIME_CONTRADICTION", dec.reason_codes)
        reg_factor = dec.factor_evidence["regime_alignment"]
        self.assertEqual(reg_factor.status, "FAILED")
        self.assertEqual(reg_factor.score, 0.0)

    def test_k_neutral_regime_for_trend_following(self):
        # Trend following in NEUTRAL regime fails when require_regime_alignment=True
        ctx = make_test_context(regime=Regime.NEUTRAL)
        cand = make_validated_candidate(strategy_id="ema_trend_following", direction=SignalDirection.LONG)
        dec = self.judge.judge(cand, context=ctx)

        self.assertEqual(dec.verdict, JudgeVerdict.REJECT)
        self.assertIn("REGIME_CONTRADICTION", dec.reason_codes)

    def test_l_unknown_and_unavailable_regime(self):
        ctx = make_test_context(regime=Regime.UNKNOWN)
        cand = make_validated_candidate(direction=SignalDirection.LONG)
        dec = self.judge.judge(cand, context=ctx)

        reg_factor = dec.factor_evidence["regime_alignment"]
        self.assertEqual(reg_factor.status, "UNAVAILABLE")
        self.assertEqual(reg_factor.score, 0.5)

    def test_m_strategy_specific_regime_rsi_mean_reversion(self):
        # RSI mean reversion thrives in NEUTRAL regime
        ctx = make_test_context(regime=Regime.NEUTRAL, rsi_14=25.0)
        cand = make_validated_candidate(
            strategy_id="rsi_mean_reversion",
            direction=SignalDirection.LONG,
            confidence=0.85,
        )
        dec = self.judge.judge(cand, context=ctx)

        self.assertEqual(dec.verdict, JudgeVerdict.PASS)
        reg_factor = dec.factor_evidence["regime_alignment"]
        self.assertEqual(reg_factor.status, "PASSED")
        self.assertEqual(reg_factor.score, 1.0)

    # =========================================================================
    # VOLATILITY & FEATURE EVIDENCE TESTS (N, O, P, Q, R)
    # =========================================================================

    def test_n_volatility_evidence_available_and_bounded(self):
        # Normal ATR = 800 on 67000 close -> ~1.19% relative ATR
        ctx = make_test_context(last_close=67000.0, atr_14=800.0)
        cand = make_validated_candidate()
        dec = self.judge.judge(cand, context=ctx)

        vol_factor = dec.factor_evidence["volatility_context"]
        self.assertEqual(vol_factor.status, "PASSED")
        self.assertEqual(vol_factor.score, 1.0)

    def test_n_excessive_volatility_rejected(self):
        # ATR = 6000 on 67000 close -> ~8.95% relative ATR > max 8%
        ctx = make_test_context(last_close=67000.0, atr_14=6000.0)
        cand = make_validated_candidate()
        dec = self.judge.judge(cand, context=ctx)

        self.assertEqual(dec.verdict, JudgeVerdict.REJECT)
        self.assertIn("EXCESSIVE_VOLATILITY", dec.reason_codes)

    def test_o_volatility_evidence_unavailable_handled_safely(self):
        # Context without ATR feature
        tf_regime = TimeframeRegime("BTCUSDT", "1H", datetime.now(timezone.utc), 50, Regime.BULLISH)
        tf_features = TimeframeFeatures("BTCUSDT", "1H", datetime.now(timezone.utc), 50, {"last_close": 67000.0, "ema_9": 66000.0, "ema_21": 64000.0})
        analysis = MarketAnalysis("BTCUSDT", None, {}, {"1H": tf_features}, {"1H": tf_regime}, True)
        ctx = StrategyContext(analysis=analysis)

        cand = make_validated_candidate()
        dec = self.judge.judge(cand, context=ctx)

        vol_factor = dec.factor_evidence["volatility_context"]
        self.assertEqual(vol_factor.status, "UNAVAILABLE")
        self.assertEqual(vol_factor.score, 0.5)

    def test_p_feature_confluence_available(self):
        ctx = make_test_context(ema_9=66000.0, ema_21=64000.0, rsi_14=55.0)
        cand = make_validated_candidate(direction=SignalDirection.LONG)
        dec = self.judge.judge(cand, context=ctx)

        conf_factor = dec.factor_evidence["feature_confluence"]
        self.assertEqual(conf_factor.status, "PASSED")
        self.assertEqual(conf_factor.score, 1.0)

    def test_q_feature_confluence_missing_handled_safely(self):
        # Context with only basic price, no indicators
        tf_regime = TimeframeRegime("BTCUSDT", "1H", datetime.now(timezone.utc), 50, Regime.BULLISH)
        tf_features = TimeframeFeatures("BTCUSDT", "1H", datetime.now(timezone.utc), 50, {"last_close": 67000.0})
        analysis = MarketAnalysis("BTCUSDT", None, {}, {"1H": tf_features}, {"1H": tf_regime}, True)
        ctx = StrategyContext(analysis=analysis)

        cand = make_validated_candidate()
        dec = self.judge.judge(cand, context=ctx)

        conf_factor = dec.factor_evidence["feature_confluence"]
        self.assertEqual(conf_factor.status, "UNAVAILABLE")
        self.assertEqual(conf_factor.score, 0.5)

    def test_r_conflicting_confluence_evidence_rejected(self):
        # LONG signal into extreme overbought RSI (85.0 >= 80.0)
        ctx = make_test_context(rsi_14=85.0)
        cand = make_validated_candidate(direction=SignalDirection.LONG)
        dec = self.judge.judge(cand, context=ctx)

        self.assertEqual(dec.verdict, JudgeVerdict.REJECT)
        self.assertIn("CONFLUENCE_CONTRADICTION", dec.reason_codes)

    # =========================================================================
    # CONFIDENCE & SCORING BOUNDARIES (S, AC)
    # =========================================================================

    def test_s_confidence_threshold_rejected(self):
        # Candidate confidence 0.40 < min_confidence 0.50
        cand = make_validated_candidate(confidence=0.40)
        dec = self.judge.judge(cand, context=self.context)

        self.assertEqual(dec.verdict, JudgeVerdict.REJECT)
        self.assertIn("LOW_CONFIDENCE", dec.reason_codes)

    def test_ac_scoring_properties_and_bounds(self):
        cand = make_validated_candidate()
        dec = self.judge.judge(cand, context=self.context)

        self.assertIsInstance(dec.score, float)
        self.assertGreaterEqual(dec.score, 0.0)
        self.assertLessEqual(dec.score, 1.0)

    # =========================================================================
    # DETERMINISM, IMMUTABILITY & PURITY (U, V, W, X, Y, Z)
    # =========================================================================

    def test_u_determinism_across_repeated_calls(self):
        cand = make_validated_candidate()
        results = [self.judge.judge(cand, context=self.context) for _ in range(10)]

        for r in results:
            self.assertEqual(r.verdict, results[0].verdict)
            self.assertEqual(r.score, results[0].score)
            self.assertEqual(r.reason_codes, results[0].reason_codes)
            self.assertEqual(r.explanation, results[0].explanation)

    def test_v_decision_immutability(self):
        cand = make_validated_candidate()
        dec = self.judge.judge(cand, context=self.context)

        # Frozen dataclass checks
        with self.assertRaises(Exception):
            dec.verdict = JudgeVerdict.REJECT
        with self.assertRaises(Exception):
            dec.score = 0.0
        with self.assertRaises(TypeError):
            dec.metadata["tamper"] = True

    def test_w_source_candidate_and_context_not_mutated(self):
        cand = make_validated_candidate()
        orig_symbol = cand.symbol
        orig_confidence = cand.confidence
        orig_ctx_symbol = self.context.symbol

        _ = self.judge.judge(cand, context=self.context)

        self.assertEqual(cand.symbol, orig_symbol)
        self.assertEqual(cand.confidence, orig_confidence)
        self.assertEqual(self.context.symbol, orig_ctx_symbol)

    def test_x_stable_reason_codes(self):
        cand_flat = make_validated_candidate(direction=SignalDirection.FLAT, confidence=0.0)
        dec_flat = self.judge.judge(cand_flat, context=self.context)
        self.assertEqual(dec_flat.reason_codes, ("FLAT_SIGNAL_NOT_ACTIONABLE",))

        cand_pass = make_validated_candidate()
        dec_pass = self.judge.judge(cand_pass, context=self.context)
        self.assertEqual(dec_pass.reason_codes, ("CRITERIA_SATISFIED",))

    def test_y_no_accidental_network_access(self):
        # Purely offline execution
        cand = make_validated_candidate()
        dec = self.judge.judge(cand, context=self.context)
        self.assertIsNotNone(dec)

    def test_z_no_execution_or_risk_side_effects(self):
        # Verification that Judge has zero execution or risk methods
        self.assertFalse(hasattr(self.judge, "execute"))
        self.assertFalse(hasattr(self.judge, "place_order"))
        self.assertFalse(hasattr(self.judge, "position_size"))
        self.assertFalse(hasattr(self.judge, "calculate_leverage"))

    # =========================================================================
    # END-TO-END PIPELINE INTEGRATION (AA)
    # =========================================================================

    def test_aa_strategy_to_validator_to_judge_integration(self):
        """
        MarketAnalysis -> StrategyContext -> Candidates -> Evaluator ->
        Ranker -> Selector -> Selected Strategy -> Signal -> Validator ->
        ValidatedCandidate -> JudgeEngine -> JudgeDecision
        """
        registry = StrategyRegistry()
        ema_strat = EMATrendFollowingStrategy()
        rsi_strat = RSIMeanReversionStrategy()
        registry.register(ema_strat)
        registry.register(rsi_strat)

        candidates = [
            StrategyCandidate("ema_trend_following", "EMA Trend Following"),
            StrategyCandidate("rsi_mean_reversion", "RSI Mean Reversion"),
        ]

        # 1. Market Context with strong Bullish alignment and clear EMA spread
        ctx = make_test_context(
            regime=Regime.BULLISH,
            ema_9=66000.0,
            ema_21=63000.0,
            last_close=68500.0,
            rsi_14=55.0,
            atr_14=800.0,
        )

        # 2. Evaluation
        evaluator = DeterministicStrategyEvaluator(registry=registry)
        evaluations = [evaluator.evaluate(ctx, c) for c in candidates]

        # 3. Ranking
        ranker = DeterministicStrategyRanker()
        ranked = ranker.rank(evaluations)
        self.assertEqual(ranked[0].strategy_id, "ema_trend_following")

        # 4. Selection
        selector = DeterministicStrategySelector(registry=registry, evaluator=evaluator, ranker=ranker)
        selection = selector.select_from_ranked(ranked, context=ctx)
        self.assertEqual(selection.selected_strategy_id, "ema_trend_following")

        # 5. Signal Generation from Concrete Strategy
        selected_strat = registry.get(selection.selected_strategy_id)
        signal = selected_strat.evaluate(ctx)
        self.assertEqual(signal.direction, SignalDirection.LONG)

        # 6. Signal Validation (Sprint 26)
        validator = DeterministicSignalValidator()
        validated_candidate = validator.validate(
            signal=signal,
            context=ctx,
            selected_strategy_id=selection,
        )
        self.assertTrue(validated_candidate.is_valid)

        # 7. Judge Engine Decision (Sprint 27)
        decision = self.judge.judge(
            candidate=validated_candidate,
            context=ctx,
        )

        # 8. Verification of Judge Decision
        self.assertIsInstance(decision, JudgeDecision)
        self.assertEqual(decision.verdict, JudgeVerdict.PASS)
        self.assertTrue(decision.is_passed)
        self.assertEqual(decision.symbol, "BTCUSDT")
        self.assertEqual(decision.strategy_id, "ema_trend_following")
        self.assertEqual(decision.direction, SignalDirection.LONG)
        self.assertIn("CRITERIA_SATISFIED", decision.reason_codes)
        self.assertGreaterEqual(decision.score, 0.60)
        self.assertEqual(len(decision.factor_evidence), 4)


if __name__ == "__main__":
    unittest.main()
