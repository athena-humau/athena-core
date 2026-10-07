from datetime import datetime, timezone
import unittest

from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from scanner.ranking_engine import RankedMarket
from strategy.context import StrategyContext
from strategy.ema_trend_following import EMATrendFollowingStrategy
from strategy.models import Signal, SignalDirection
from strategy.registry import StrategyRegistry
from strategy.rsi_mean_reversion import RSIMeanReversionStrategy


def make_test_analysis(
    symbol: str = "BTCUSDT",
    timeframe: str = "1H",
    feature_values: dict = None,
    success: bool = True,
    error: str = None,
) -> MarketAnalysis:
    if feature_values is None:
        feature_values = {}

    tf_features = TimeframeFeatures(
        symbol=symbol,
        granularity=timeframe,
        timestamp=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
        candle_count=50,
        values=feature_values,
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
        features_by_granularity={timeframe: tf_features},
        regimes_by_granularity={},
        success=success,
        error=error,
        analyzed_at=datetime.now(timezone.utc),
    )


class TestConcreteStrategies(unittest.TestCase):

    # =========================================================================
    # EMA TREND FOLLOWING TESTS
    # =========================================================================

    def test_ema_long_condition(self):
        # EMA9 = 105, EMA21 = 100, Price = 110 -> Price > EMA9 > EMA21 -> LONG
        strategy = EMATrendFollowingStrategy()
        analysis = make_test_analysis(
            feature_values={"ema_9": 105.0, "ema_21": 100.0, "last_close": 110.0}
        )
        ctx = StrategyContext(analysis=analysis)
        sig = strategy.evaluate(ctx)

        self.assertEqual(sig.direction, SignalDirection.LONG)
        self.assertEqual(sig.strategy_id, "ema_trend_following")
        self.assertGreater(sig.confidence, 0.0)
        self.assertLessEqual(sig.confidence, 1.0)
        self.assertIn("LONG because ema_9 > ema_21", sig.reason)
        self.assertEqual(sig.metadata["fast_ema"], 105.0)
        self.assertEqual(sig.metadata["slow_ema"], 100.0)
        self.assertEqual(sig.metadata["price"], 110.0)

    def test_ema_short_condition(self):
        # EMA9 = 95, EMA21 = 100, Price = 90 -> Price < EMA9 < EMA21 -> SHORT
        strategy = EMATrendFollowingStrategy()
        analysis = make_test_analysis(
            feature_values={"ema_9": 95.0, "ema_21": 100.0, "last_close": 90.0}
        )
        ctx = StrategyContext(analysis=analysis)
        sig = strategy.evaluate(ctx)

        self.assertEqual(sig.direction, SignalDirection.SHORT)
        self.assertEqual(sig.strategy_id, "ema_trend_following")
        self.assertGreater(sig.confidence, 0.0)
        self.assertLessEqual(sig.confidence, 1.0)
        self.assertIn("SHORT because ema_9 < ema_21", sig.reason)

    def test_ema_flat_price_not_confirming_fast_ema(self):
        # Case C: EMA9 > EMA21 (105 > 100), but Price <= EMA9 (102 <= 105) -> FLAT
        strategy = EMATrendFollowingStrategy()
        analysis_c = make_test_analysis(
            feature_values={"ema_9": 105.0, "ema_21": 100.0, "last_close": 102.0}
        )
        sig_c = strategy.evaluate(StrategyContext(analysis=analysis_c))
        self.assertEqual(sig_c.direction, SignalDirection.FLAT)
        self.assertIn("price <= ema_9", sig_c.reason)

        # Case D: EMA9 < EMA21 (95 < 100), but Price >= EMA9 (98 >= 95) -> FLAT
        analysis_d = make_test_analysis(
            feature_values={"ema_9": 95.0, "ema_21": 100.0, "last_close": 98.0}
        )
        sig_d = strategy.evaluate(StrategyContext(analysis=analysis_d))
        self.assertEqual(sig_d.direction, SignalDirection.FLAT)
        self.assertIn("price >= ema_9", sig_d.reason)

    def test_ema_flat_boundary_equality(self):
        # Case E: EMA9 == EMA21 -> FLAT
        strategy = EMATrendFollowingStrategy()
        analysis = make_test_analysis(
            feature_values={"ema_9": 100.0, "ema_21": 100.0, "last_close": 105.0}
        )
        sig = strategy.evaluate(StrategyContext(analysis=analysis))
        self.assertEqual(sig.direction, SignalDirection.FLAT)
        self.assertIn("ema_9 equals ema_21", sig.reason)

    def test_ema_missing_or_malformed_features_fails_safely(self):
        strategy = EMATrendFollowingStrategy()

        # Missing ema_21
        analysis_missing = make_test_analysis(
            feature_values={"ema_9": 105.0, "last_close": 110.0}
        )
        sig_missing = strategy.evaluate(StrategyContext(analysis=analysis_missing))
        self.assertEqual(sig_missing.direction, SignalDirection.FLAT)
        self.assertIn("Missing required EMA", sig_missing.reason)

        # Non-numeric value
        analysis_bad = make_test_analysis(
            feature_values={"ema_9": "bad_value", "ema_21": 100.0, "last_close": 110.0}
        )
        sig_bad = strategy.evaluate(StrategyContext(analysis=analysis_bad))
        self.assertEqual(sig_bad.direction, SignalDirection.FLAT)
        self.assertIn("Non-numeric", sig_bad.reason)

        # Analysis unsuccessful
        analysis_failed = make_test_analysis(success=False, error="API timeout")
        sig_failed = strategy.evaluate(StrategyContext(analysis=analysis_failed))
        self.assertEqual(sig_failed.direction, SignalDirection.FLAT)
        self.assertIn("Market analysis failed", sig_failed.reason)

    # =========================================================================
    # RSI MEAN REVERSION TESTS
    # =========================================================================

    def test_rsi_oversold_long(self):
        # RSI = 25 (< 30) -> LONG
        strategy = RSIMeanReversionStrategy()
        analysis = make_test_analysis(feature_values={"rsi_14": 25.0})
        sig = strategy.evaluate(StrategyContext(analysis=analysis))

        self.assertEqual(sig.direction, SignalDirection.LONG)
        self.assertEqual(sig.strategy_id, "rsi_mean_reversion")
        self.assertGreater(sig.confidence, 0.0)
        self.assertLessEqual(sig.confidence, 1.0)
        self.assertIn("LONG because rsi_14 < 30.0", sig.reason)

    def test_rsi_overbought_short(self):
        # RSI = 75 (> 70) -> SHORT
        strategy = RSIMeanReversionStrategy()
        analysis = make_test_analysis(feature_values={"rsi_14": 75.0})
        sig = strategy.evaluate(StrategyContext(analysis=analysis))

        self.assertEqual(sig.direction, SignalDirection.SHORT)
        self.assertEqual(sig.strategy_id, "rsi_mean_reversion")
        self.assertGreater(sig.confidence, 0.0)
        self.assertLessEqual(sig.confidence, 1.0)
        self.assertIn("SHORT because rsi_14 > 70.0", sig.reason)

    def test_rsi_neutral_flat(self):
        # RSI = 50 -> FLAT
        strategy = RSIMeanReversionStrategy()
        analysis = make_test_analysis(feature_values={"rsi_14": 50.0})
        sig = strategy.evaluate(StrategyContext(analysis=analysis))

        self.assertEqual(sig.direction, SignalDirection.FLAT)
        self.assertEqual(sig.confidence, 0.0)
        self.assertIn("FLAT because rsi_14 is between 30.0 and 70.0", sig.reason)

    def test_rsi_exact_boundary_conditions(self):
        strategy = RSIMeanReversionStrategy()

        # Exact boundary 30.0 -> FLAT
        sig_30 = strategy.evaluate(StrategyContext(analysis=make_test_analysis(feature_values={"rsi_14": 30.0})))
        self.assertEqual(sig_30.direction, SignalDirection.FLAT)

        # 29.99 -> LONG
        sig_2999 = strategy.evaluate(StrategyContext(analysis=make_test_analysis(feature_values={"rsi_14": 29.99})))
        self.assertEqual(sig_2999.direction, SignalDirection.LONG)

        # 30.01 -> FLAT
        sig_3001 = strategy.evaluate(StrategyContext(analysis=make_test_analysis(feature_values={"rsi_14": 30.01})))
        self.assertEqual(sig_3001.direction, SignalDirection.FLAT)

        # Exact boundary 70.0 -> FLAT
        sig_70 = strategy.evaluate(StrategyContext(analysis=make_test_analysis(feature_values={"rsi_14": 70.0})))
        self.assertEqual(sig_70.direction, SignalDirection.FLAT)

        # 69.99 -> FLAT
        sig_6999 = strategy.evaluate(StrategyContext(analysis=make_test_analysis(feature_values={"rsi_14": 69.99})))
        self.assertEqual(sig_6999.direction, SignalDirection.FLAT)

        # 70.01 -> SHORT
        sig_7001 = strategy.evaluate(StrategyContext(analysis=make_test_analysis(feature_values={"rsi_14": 70.01})))
        self.assertEqual(sig_7001.direction, SignalDirection.SHORT)

        # Extremes: 0 -> LONG, 100 -> SHORT
        sig_0 = strategy.evaluate(StrategyContext(analysis=make_test_analysis(feature_values={"rsi_14": 0.0})))
        self.assertEqual(sig_0.direction, SignalDirection.LONG)
        self.assertEqual(sig_0.confidence, 1.0)

        sig_100 = strategy.evaluate(StrategyContext(analysis=make_test_analysis(feature_values={"rsi_14": 100.0})))
        self.assertEqual(sig_100.direction, SignalDirection.SHORT)
        self.assertEqual(sig_100.confidence, 1.0)

    def test_rsi_missing_or_malformed_features(self):
        strategy = RSIMeanReversionStrategy()

        # Missing rsi_14
        sig_missing = strategy.evaluate(StrategyContext(analysis=make_test_analysis(feature_values={})))
        self.assertEqual(sig_missing.direction, SignalDirection.FLAT)
        self.assertIn("Missing required feature 'rsi_14'", sig_missing.reason)

        # Non-numeric
        sig_bad = strategy.evaluate(StrategyContext(analysis=make_test_analysis(feature_values={"rsi_14": "nan"})))
        self.assertEqual(sig_bad.direction, SignalDirection.FLAT)
        self.assertIn("Non-numeric", sig_bad.reason)

    # =========================================================================
    # STRATEGY REGISTRY INTEGRATION
    # =========================================================================

    def test_strategy_registry_integration(self):
        registry = StrategyRegistry()
        ema_strat = EMATrendFollowingStrategy()
        rsi_strat = RSIMeanReversionStrategy()

        registry.register(ema_strat)
        registry.register(rsi_strat)

        self.assertEqual(len(registry), 2)
        self.assertTrue(registry.has("ema_trend_following"))
        self.assertTrue(registry.has("rsi_mean_reversion"))
        self.assertEqual(registry.get("ema_trend_following"), ema_strat)
        self.assertEqual(registry.get("rsi_mean_reversion"), rsi_strat)
        self.assertEqual(registry.list_strategies(), ["ema_trend_following", "rsi_mean_reversion"])

    # =========================================================================
    # DETERMINISM AND NO LOOK-AHEAD VERIFICATION
    # =========================================================================

    def test_determinism_across_multiple_evaluations(self):
        ema_strat = EMATrendFollowingStrategy()
        rsi_strat = RSIMeanReversionStrategy()

        analysis = make_test_analysis(
            feature_values={"ema_9": 105.0, "ema_21": 100.0, "last_close": 110.0, "rsi_14": 22.0}
        )
        ctx = StrategyContext(analysis=analysis, timestamp=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc))

        # Evaluate EMA 5 times
        ema_signals = [ema_strat.evaluate(ctx) for _ in range(5)]
        for s in ema_signals:
            self.assertEqual(s.direction, SignalDirection.LONG)
            self.assertEqual(s.confidence, ema_signals[0].confidence)
            self.assertEqual(s.reason, ema_signals[0].reason)

        # Evaluate RSI 5 times
        rsi_signals = [rsi_strat.evaluate(ctx) for _ in range(5)]
        for s in rsi_signals:
            self.assertEqual(s.direction, SignalDirection.LONG)
            self.assertEqual(s.confidence, rsi_signals[0].confidence)
            self.assertEqual(s.reason, rsi_signals[0].reason)

    def test_immutability_of_context_and_signal(self):
        strategy = EMATrendFollowingStrategy()
        analysis = make_test_analysis(
            feature_values={"ema_9": 105.0, "ema_21": 100.0, "last_close": 110.0}
        )
        ctx = StrategyContext(analysis=analysis)
        sig = strategy.evaluate(ctx)

        with self.assertRaises(TypeError):
            sig.metadata["new_key"] = 123

        with self.assertRaises(Exception):
            sig.direction = SignalDirection.SHORT


if __name__ == "__main__":
    unittest.main()
