from datetime import datetime, timezone
import unittest

from ai.deterministic_evaluator import DeterministicStrategyEvaluator
from ai.evaluator import StrategyEvaluator
from ai.models import StrategyCandidate, StrategyEvaluation
from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from regime.models import Regime, TimeframeRegime
from scanner.ranking_engine import RankedMarket
from strategy.context import StrategyContext
from strategy.ema_trend_following import EMATrendFollowingStrategy
from strategy.registry import StrategyRegistry
from strategy.rsi_mean_reversion import RSIMeanReversionStrategy


def make_test_analysis(
    symbol: str = "BTCUSDT",
    regime: Regime = Regime.BULLISH,
    feature_values: dict = None,
    success: bool = True,
    error: str = None,
) -> MarketAnalysis:
    if feature_values is None:
        feature_values = {}

    tf_features = TimeframeFeatures(
        symbol=symbol,
        granularity="1H",
        timestamp=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
        candle_count=50,
        values=feature_values,
    )

    tf_regime = TimeframeRegime(
        symbol=symbol,
        granularity="1H",
        timestamp=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
        candle_count=50,
        regime=regime,
    )

    return MarketAnalysis(
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


class TestStrategyEvaluatorFoundation(unittest.TestCase):

    def setUp(self):
        self.registry = StrategyRegistry()
        self.ema_strat = EMATrendFollowingStrategy()
        self.rsi_strat = RSIMeanReversionStrategy()
        self.registry.register(self.ema_strat)
        self.registry.register(self.rsi_strat)

        self.candidate_ema = StrategyCandidate(
            strategy_id="ema_trend_following",
            strategy_name="EMA Trend Following",
        )
        self.candidate_rsi = StrategyCandidate(
            strategy_id="rsi_mean_reversion",
            strategy_name="RSI Mean Reversion",
        )

        self.evaluator = DeterministicStrategyEvaluator(registry=self.registry)

    # =========================================================================
    # CONTRACT AND MODEL VALIDATION
    # =========================================================================

    def test_evaluator_contract(self):
        self.assertIsInstance(self.evaluator, StrategyEvaluator)
        self.assertEqual(self.evaluator.evaluator_id, "deterministic_strategy_evaluator")

    def test_strategy_evaluation_model_validation_and_immutability(self):
        ev = StrategyEvaluation(
            strategy_id="ema_trend_following",
            eligible=True,
            score=0.85,
            regime_alignment=1.0,
            signal_alignment=0.75,
            data_quality=1.0,
            evaluation_reason="Valid test setup",
            metadata={"source": "test"},
        )
        self.assertEqual(ev.strategy_id, "ema_trend_following")
        self.assertTrue(ev.eligible)
        self.assertEqual(ev.score, 0.85)
        self.assertEqual(ev.regime_alignment, 1.0)
        self.assertEqual(ev.signal_alignment, 0.75)
        self.assertEqual(ev.data_quality, 1.0)
        self.assertEqual(ev.metadata["source"], "test")

        with self.assertRaises(TypeError):
            ev.metadata["source"] = "new"

        with self.assertRaises(Exception):
            ev.score = 0.5

        # Bounds validation
        with self.assertRaises(ValueError):
            StrategyEvaluation("id", True, 1.2, 1.0, 1.0, 1.0, "reason")
        with self.assertRaises(ValueError):
            StrategyEvaluation("id", True, -0.1, 1.0, 1.0, 1.0, "reason")

    # =========================================================================
    # EMA EVALUATION TESTS
    # =========================================================================

    def test_ema_bullish_regime_and_bullish_setup(self):
        # Bullish regime, EMA9 > EMA21 and price > EMA9
        # Regime alignment = 1.0 (40%), Signal alignment = 1.0 (40%), Data quality = 1.0 (20%)
        # Score = 0.40 + 0.40 + 0.20 = 1.0
        analysis = make_test_analysis(
            regime=Regime.BULLISH,
            feature_values={"ema_9": 105.0, "ema_21": 100.0, "last_close": 110.0},
        )
        ctx = StrategyContext(analysis=analysis)
        ev = self.evaluator.evaluate(ctx, self.candidate_ema)

        self.assertTrue(ev.eligible)
        self.assertEqual(ev.score, 1.0)
        self.assertEqual(ev.regime_alignment, 1.0)
        self.assertEqual(ev.signal_alignment, 1.0)
        self.assertEqual(ev.data_quality, 1.0)

    def test_ema_bearish_regime_and_bearish_setup(self):
        # Bearish regime, EMA9 < EMA21 and price < EMA9
        # Score = 1.0
        analysis = make_test_analysis(
            regime=Regime.BEARISH,
            feature_values={"ema_9": 95.0, "ema_21": 100.0, "last_close": 90.0},
        )
        ctx = StrategyContext(analysis=analysis)
        ev = self.evaluator.evaluate(ctx, self.candidate_ema)

        self.assertTrue(ev.eligible)
        self.assertEqual(ev.score, 1.0)
        self.assertEqual(ev.regime_alignment, 1.0)
        self.assertEqual(ev.signal_alignment, 1.0)

    def test_ema_contradictory_regime(self):
        # Bullish setup (EMA9 > EMA21, price > EMA9) in a BEARISH regime
        # Regime alignment = 1.0 (trend-friendly regime), but contradictory setup -> Signal alignment = 0.3
        # Score = (1.0 * 0.40) + (0.3 * 0.40) + (1.0 * 0.20) = 0.40 + 0.12 + 0.20 = 0.72
        analysis = make_test_analysis(
            regime=Regime.BEARISH,
            feature_values={"ema_9": 105.0, "ema_21": 100.0, "last_close": 110.0},
        )
        ctx = StrategyContext(analysis=analysis)
        ev = self.evaluator.evaluate(ctx, self.candidate_ema)

        self.assertTrue(ev.eligible)
        self.assertEqual(ev.score, 0.72)
        self.assertEqual(ev.signal_alignment, 0.3)

    def test_ema_flat_condition_reduces_score(self):
        # Bullish regime, but EMA9 > EMA21 and price <= EMA9 (no active signal -> signal alignment 0.0)
        # Score = (1.0 * 0.40) + (0.0 * 0.40) + (1.0 * 0.20) = 0.60
        analysis = make_test_analysis(
            regime=Regime.BULLISH,
            feature_values={"ema_9": 105.0, "ema_21": 100.0, "last_close": 102.0},
        )
        ctx = StrategyContext(analysis=analysis)
        ev = self.evaluator.evaluate(ctx, self.candidate_ema)

        self.assertTrue(ev.eligible)
        self.assertEqual(ev.score, 0.60)
        self.assertEqual(ev.signal_alignment, 0.0)

    def test_ema_missing_required_features_fails_eligibility(self):
        # Missing ema_21
        analysis = make_test_analysis(
            regime=Regime.BULLISH,
            feature_values={"ema_9": 105.0, "last_close": 110.0},
        )
        ctx = StrategyContext(analysis=analysis)
        ev = self.evaluator.evaluate(ctx, self.candidate_ema)

        self.assertFalse(ev.eligible)
        self.assertEqual(ev.score, 0.0)
        self.assertEqual(ev.data_quality, 0.0)
        self.assertIn("Missing or non-numeric", ev.evaluation_reason)

    # =========================================================================
    # RSI EVALUATION TESTS
    # =========================================================================

    def test_rsi_neutral_regime_and_oversold_setup(self):
        # Neutral regime, RSI = 25 (< 30)
        # Regime alignment = 1.0 (40%), Signal alignment = 1.0 (40%), Data quality = 1.0 (20%)
        # Score = 0.40 + 0.40 + 0.20 = 1.0
        analysis = make_test_analysis(
            regime=Regime.NEUTRAL,
            feature_values={"rsi_14": 25.0},
        )
        ctx = StrategyContext(analysis=analysis)
        ev = self.evaluator.evaluate(ctx, self.candidate_rsi)

        self.assertTrue(ev.eligible)
        self.assertEqual(ev.score, 1.0)
        self.assertEqual(ev.regime_alignment, 1.0)
        self.assertEqual(ev.signal_alignment, 1.0)

    def test_rsi_neutral_regime_and_neutral_rsi(self):
        # Neutral regime, RSI = 50 (neutral range -> signal alignment 0.0)
        # Score = (1.0 * 0.40) + (0.0 * 0.40) + (1.0 * 0.20) = 0.60
        analysis = make_test_analysis(
            regime=Regime.NEUTRAL,
            feature_values={"rsi_14": 50.0},
        )
        ctx = StrategyContext(analysis=analysis)
        ev = self.evaluator.evaluate(ctx, self.candidate_rsi)

        self.assertTrue(ev.eligible)
        self.assertEqual(ev.score, 0.60)
        self.assertEqual(ev.signal_alignment, 0.0)

    def test_rsi_trending_regime_reduces_score(self):
        # Bullish regime (unfavorable for mean reversion -> regime alignment 0.3), RSI = 25 (oversold -> 1.0)
        # Score = (0.3 * 0.40) + (1.0 * 0.40) + (1.0 * 0.20) = 0.12 + 0.40 + 0.20 = 0.72
        analysis = make_test_analysis(
            regime=Regime.BULLISH,
            feature_values={"rsi_14": 25.0},
        )
        ctx = StrategyContext(analysis=analysis)
        ev = self.evaluator.evaluate(ctx, self.candidate_rsi)

        self.assertTrue(ev.eligible)
        self.assertEqual(ev.score, 0.72)
        self.assertEqual(ev.regime_alignment, 0.3)

    def test_rsi_missing_required_features_fails_eligibility(self):
        analysis = make_test_analysis(
            regime=Regime.NEUTRAL,
            feature_values={},
        )
        ctx = StrategyContext(analysis=analysis)
        ev = self.evaluator.evaluate(ctx, self.candidate_rsi)

        self.assertFalse(ev.eligible)
        self.assertEqual(ev.score, 0.0)
        self.assertEqual(ev.data_quality, 0.0)

    # =========================================================================
    # DETERMINISM, IMMUTABILITY, AND INTEGRATION
    # =========================================================================

    def test_determinism_across_evaluations(self):
        analysis = make_test_analysis(
            regime=Regime.BULLISH,
            feature_values={"ema_9": 105.0, "ema_21": 100.0, "last_close": 110.0},
        )
        ctx = StrategyContext(analysis=analysis)

        results = [self.evaluator.evaluate(ctx, self.candidate_ema) for _ in range(5)]
        for r in results:
            self.assertEqual(r.score, results[0].score)
            self.assertEqual(r.evaluation_reason, results[0].evaluation_reason)

    def test_unregistered_candidate_rejected_safely(self):
        analysis = make_test_analysis()
        ctx = StrategyContext(analysis=analysis)
        unregistered = StrategyCandidate("unknown_strategy", "Unknown")

        with self.assertRaises(ValueError) as err:
            self.evaluator.evaluate(ctx, unregistered)
        self.assertIn("not registered", str(err.exception))

    def test_ineligible_candidate_returns_zero_score(self):
        analysis = make_test_analysis()
        ctx = StrategyContext(analysis=analysis)
        ineligible_cand = StrategyCandidate(
            "ema_trend_following", "EMA", eligible=False
        )
        ev = self.evaluator.evaluate(ctx, ineligible_cand)

        self.assertFalse(ev.eligible)
        self.assertEqual(ev.score, 0.0)


if __name__ == "__main__":
    unittest.main()
