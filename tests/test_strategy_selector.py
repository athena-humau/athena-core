from datetime import datetime, timezone
import unittest

from ai.deterministic_selector import DeterministicStrategySelector
from ai.models import StrategyCandidate, StrategySelection
from ai.selector import StrategySelector
from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from regime.models import Regime, TimeframeRegime
from scanner.ranking_engine import RankedMarket
from strategy.context import StrategyContext
from strategy.ema_trend_following import EMATrendFollowingStrategy
from strategy.registry import StrategyRegistry
from strategy.rsi_mean_reversion import RSIMeanReversionStrategy


def make_test_analysis(symbol: str = "BTCUSDT", regime: Regime = Regime.BULLISH) -> MarketAnalysis:
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
        features_by_granularity={},
        regimes_by_granularity={"1H": tf_regime},
        success=True,
        error=None,
        analyzed_at=datetime.now(timezone.utc),
    )


class TestStrategySelectorFoundation(unittest.TestCase):

    def setUp(self):
        self.registry = StrategyRegistry()
        self.ema_strat = EMATrendFollowingStrategy()
        self.rsi_strat = RSIMeanReversionStrategy()
        self.registry.register(self.ema_strat)
        self.registry.register(self.rsi_strat)

        self.candidate_ema = StrategyCandidate(
            strategy_id="ema_trend_following",
            strategy_name="EMA Trend Following",
            description="Trend following using EMA 9/21",
        )
        self.candidate_rsi = StrategyCandidate(
            strategy_id="rsi_mean_reversion",
            strategy_name="RSI Mean Reversion",
            description="Mean reversion using RSI 14",
        )

        self.selector = DeterministicStrategySelector(registry=self.registry)

    # =========================================================================
    # CONTRACT AND MODEL VALIDATION
    # =========================================================================

    def test_selector_contract_implementation(self):
        self.assertIsInstance(self.selector, StrategySelector)
        self.assertEqual(self.selector.selector_id, "deterministic_regime_selector")

    def test_candidate_model_validation_and_immutability(self):
        cand = StrategyCandidate(
            strategy_id="test_id",
            strategy_name="Test Strategy",
            metadata={"type": "trend"},
        )
        self.assertEqual(cand.strategy_id, "test_id")
        self.assertTrue(cand.eligible)
        with self.assertRaises(TypeError):
            cand.metadata["new_key"] = "bad"
        with self.assertRaises(Exception):
            cand.strategy_id = "other"

        with self.assertRaises(ValueError):
            StrategyCandidate("", "Name")
        with self.assertRaises(ValueError):
            StrategyCandidate("id", "")

    def test_selection_model_validation_and_immutability(self):
        sel = StrategySelection(
            selected_strategy_id="ema_trend_following",
            selection_reason="Selected for trend",
            selector_id="test_selector",
            confidence=0.8,
            candidate_ids=("ema_trend_following", "rsi_mean_reversion"),
            metadata={"regime": "bullish"},
        )
        self.assertEqual(sel.selected_strategy_id, "ema_trend_following")
        self.assertEqual(sel.confidence, 0.8)
        with self.assertRaises(TypeError):
            sel.metadata["key"] = "val"
        with self.assertRaises(Exception):
            sel.selected_strategy_id = "other"

        with self.assertRaises(ValueError):
            StrategySelection("", "reason", "sel_id", 0.5, ("c1",))
        with self.assertRaises(ValueError):
            StrategySelection("strat", "reason", "sel_id", 1.5, ("c1",))
        with self.assertRaises(ValueError):
            StrategySelection("strat", "reason", "sel_id", -0.1, ("c1",))

    # =========================================================================
    # REGIME-BASED SELECTION LOGIC
    # =========================================================================

    def test_bullish_regime_selects_ema_trend_following(self):
        analysis = make_test_analysis(regime=Regime.BULLISH)
        ctx = StrategyContext(analysis=analysis)
        selection = self.selector.select(ctx, [self.candidate_ema, self.candidate_rsi])

        self.assertEqual(selection.selected_strategy_id, "ema_trend_following")
        self.assertEqual(selection.confidence, 0.8)
        self.assertIn("BULLISH", selection.selection_reason)

    def test_bearish_regime_selects_ema_trend_following(self):
        analysis = make_test_analysis(regime=Regime.BEARISH)
        ctx = StrategyContext(analysis=analysis)
        selection = self.selector.select(ctx, [self.candidate_ema, self.candidate_rsi])

        self.assertEqual(selection.selected_strategy_id, "ema_trend_following")
        self.assertEqual(selection.confidence, 0.8)
        self.assertIn("BEARISH", selection.selection_reason)

    def test_neutral_regime_selects_rsi_mean_reversion(self):
        analysis = make_test_analysis(regime=Regime.NEUTRAL)
        ctx = StrategyContext(analysis=analysis)
        selection = self.selector.select(ctx, [self.candidate_ema, self.candidate_rsi])

        self.assertEqual(selection.selected_strategy_id, "rsi_mean_reversion")
        self.assertEqual(selection.confidence, 0.8)
        self.assertIn("NEUTRAL", selection.selection_reason)

    # =========================================================================
    # CANDIDATE RESTRICTION & ORDER INDEPENDENCE
    # =========================================================================

    def test_candidate_restriction_only_ema_supplied(self):
        # Even if regime is NEUTRAL, only EMA is available -> must select EMA
        analysis = make_test_analysis(regime=Regime.NEUTRAL)
        ctx = StrategyContext(analysis=analysis)
        selection = self.selector.select(ctx, [self.candidate_ema])

        self.assertEqual(selection.selected_strategy_id, "ema_trend_following")
        self.assertIn("fallback", selection.selection_reason)

    def test_candidate_restriction_only_rsi_supplied(self):
        # Even if regime is BULLISH, only RSI is available -> must select RSI
        analysis = make_test_analysis(regime=Regime.BULLISH)
        ctx = StrategyContext(analysis=analysis)
        selection = self.selector.select(ctx, [self.candidate_rsi])

        self.assertEqual(selection.selected_strategy_id, "rsi_mean_reversion")
        self.assertIn("fallback", selection.selection_reason)

    def test_order_independence(self):
        # Same context evaluated with [EMA, RSI] vs [RSI, EMA] produces identical selection
        analysis = make_test_analysis(regime=Regime.BULLISH)
        ctx = StrategyContext(analysis=analysis)

        sel_forward = self.selector.select(ctx, [self.candidate_ema, self.candidate_rsi])
        sel_reversed = self.selector.select(ctx, [self.candidate_rsi, self.candidate_ema])

        self.assertEqual(sel_forward.selected_strategy_id, sel_reversed.selected_strategy_id)
        self.assertEqual(sel_forward.confidence, sel_reversed.confidence)

    def test_determinism_across_multiple_runs(self):
        analysis = make_test_analysis(regime=Regime.NEUTRAL)
        ctx = StrategyContext(analysis=analysis)

        results = [
            self.selector.select(ctx, [self.candidate_ema, self.candidate_rsi])
            for _ in range(5)
        ]
        for res in results:
            self.assertEqual(res.selected_strategy_id, "rsi_mean_reversion")
            self.assertEqual(res.confidence, 0.8)
            self.assertEqual(res.selection_reason, results[0].selection_reason)

    # =========================================================================
    # ERROR & EDGE CASE HANDLING
    # =========================================================================

    def test_empty_candidates_fails_safely(self):
        analysis = make_test_analysis()
        ctx = StrategyContext(analysis=analysis)
        with self.assertRaises(ValueError) as err:
            self.selector.select(ctx, [])
        self.assertIn("empty candidates", str(err.exception))

    def test_unregistered_candidate_fails_safely(self):
        analysis = make_test_analysis()
        ctx = StrategyContext(analysis=analysis)
        unknown_cand = StrategyCandidate("unknown_strat", "Unknown Strategy")

        with self.assertRaises(ValueError) as err:
            self.selector.select(ctx, [unknown_cand])
        self.assertIn("not registered", str(err.exception))

    def test_no_eligible_candidates_fails_safely(self):
        analysis = make_test_analysis()
        ctx = StrategyContext(analysis=analysis)
        ineligible_cand = StrategyCandidate(
            "ema_trend_following", "EMA", eligible=False
        )

        with self.assertRaises(ValueError) as err:
            self.selector.select(ctx, [ineligible_cand])
        self.assertIn("No eligible strategy", str(err.exception))

    # =========================================================================
    # END-TO-END INTEGRATION WITHOUT EXECUTION
    # =========================================================================

    def test_full_selector_registry_integration(self):
        analysis = make_test_analysis("BTCUSDT", regime=Regime.BULLISH)
        ctx = StrategyContext(analysis=analysis)

        candidates = [
            StrategyCandidate(s_id, self.registry.get(s_id).name)
            for s_id in self.registry.list_strategies()
        ]
        selection = self.selector.select(ctx, candidates)

        # Retrieve chosen strategy from registry
        chosen_strategy = self.registry.get(selection.selected_strategy_id)
        self.assertEqual(chosen_strategy.strategy_id, "ema_trend_following")
        self.assertIsInstance(chosen_strategy, EMATrendFollowingStrategy)


if __name__ == "__main__":
    unittest.main()
