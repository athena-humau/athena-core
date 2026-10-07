from datetime import datetime, timezone
import unittest

from ai.deterministic_evaluator import DeterministicStrategyEvaluator
from ai.deterministic_ranker import DeterministicStrategyRanker
from ai.deterministic_selector import DeterministicStrategySelector
from ai.evaluator import StrategyEvaluator
from ai.models import (
    RankedStrategy,
    StrategyCandidate,
    StrategyEvaluation,
    StrategySelection,
)
from ai.ranker import StrategyRanker
from ai.selector import StrategySelector
from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from regime.models import Regime, TimeframeRegime
from scanner.ranking_engine import RankedMarket
from strategy.context import StrategyContext
from strategy.ema_trend_following import EMATrendFollowingStrategy
from strategy.models import Signal
from strategy.registry import StrategyRegistry
from strategy.rsi_mean_reversion import RSIMeanReversionStrategy


def make_context_with_features(
    symbol: str = "BTCUSDT",
    regime: Regime = Regime.BULLISH,
    ema_9: float | None = 65000.0,
    ema_21: float | None = 64000.0,
    last_close: float | None = 65500.0,
    rsi_14: float | None = 25.0,
) -> StrategyContext:
    tf_regime = TimeframeRegime(
        symbol=symbol,
        granularity="1H",
        timestamp=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
        candle_count=50,
        regime=regime,
    )
    feature_vals = {}
    if ema_9 is not None:
        feature_vals["ema_9"] = ema_9
    if ema_21 is not None:
        feature_vals["ema_21"] = ema_21
    if last_close is not None:
        feature_vals["last_close"] = last_close
    if rsi_14 is not None:
        feature_vals["rsi_14"] = rsi_14

    tf_features = TimeframeFeatures(
        symbol=symbol,
        granularity="1H",
        timestamp=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
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
        error=None,
        analyzed_at=datetime.now(timezone.utc),
    )
    return StrategyContext(analysis=analysis)


def make_dummy_evaluation(
    strategy_id: str,
    score: float,
    eligible: bool = True,
    reason: str = "Test evaluation",
) -> StrategyEvaluation:
    return StrategyEvaluation(
        strategy_id=strategy_id,
        eligible=eligible,
        score=score,
        regime_alignment=score,
        signal_alignment=score,
        data_quality=1.0 if eligible else 0.0,
        evaluation_reason=reason,
        metadata={"test": True},
    )


class TestStrategyRankingAndSelection(unittest.TestCase):

    def setUp(self):
        self.registry = StrategyRegistry()
        self.ema_strat = EMATrendFollowingStrategy()
        self.rsi_strat = RSIMeanReversionStrategy()
        self.registry.register(self.ema_strat)
        self.registry.register(self.rsi_strat)

        self.candidate_ema = StrategyCandidate(
            strategy_id="ema_trend_following",
            strategy_name="EMA Trend Following",
            description="EMA 9/21 trend following strategy",
        )
        self.candidate_rsi = StrategyCandidate(
            strategy_id="rsi_mean_reversion",
            strategy_name="RSI Mean Reversion",
            description="RSI 14 mean reversion strategy",
        )

        self.evaluator = DeterministicStrategyEvaluator(registry=self.registry)
        self.ranker = DeterministicStrategyRanker()
        self.selector = DeterministicStrategySelector(
            registry=self.registry,
            evaluator=self.evaluator,
            ranker=self.ranker,
        )

    # =========================================================================
    # 1. RANKEDSTRATEGY MODEL & RANKER CONTRACT
    # =========================================================================

    def test_ranked_strategy_model_validation_and_immutability(self):
        ev = make_dummy_evaluation("ema_trend_following", 0.85)
        ranked = RankedStrategy(rank=1, evaluation=ev)

        self.assertEqual(ranked.rank, 1)
        self.assertEqual(ranked.strategy_id, "ema_trend_following")
        self.assertEqual(ranked.score, 0.85)
        self.assertTrue(ranked.eligible)
        self.assertEqual(ranked.evaluation, ev)

        # Frozen immutability
        with self.assertRaises(Exception):
            ranked.rank = 2

        # Invalid rank values
        with self.assertRaises(ValueError):
            RankedStrategy(rank=0, evaluation=ev)
        with self.assertRaises(ValueError):
            RankedStrategy(rank=-1, evaluation=ev)
        with self.assertRaises(ValueError):
            RankedStrategy(rank="1", evaluation=ev)
        with self.assertRaises(ValueError):
            RankedStrategy(rank=True, evaluation=ev)
        with self.assertRaises(ValueError):
            RankedStrategy(rank=1, evaluation="not_eval")

    def test_ranker_contract(self):
        self.assertIsInstance(self.ranker, StrategyRanker)
        self.assertEqual(self.ranker.ranker_id, "deterministic_strategy_ranker")
        self.assertIn("Deterministic", self.ranker.name)

    # =========================================================================
    # 2. DETERMINISTICSTRATEGYRANKER RULES
    # =========================================================================

    def test_ranker_descending_score(self):
        ev1 = make_dummy_evaluation("rsi_mean_reversion", 0.61)
        ev2 = make_dummy_evaluation("ema_trend_following", 0.82)

        ranked = self.ranker.rank([ev1, ev2])
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0].rank, 1)
        self.assertEqual(ranked[0].strategy_id, "ema_trend_following")
        self.assertEqual(ranked[0].score, 0.82)

        self.assertEqual(ranked[1].rank, 2)
        self.assertEqual(ranked[1].strategy_id, "rsi_mean_reversion")
        self.assertEqual(ranked[1].score, 0.61)

    def test_ranker_eligible_before_ineligible(self):
        # Ineligible candidate with higher score must NEVER outrank eligible candidate
        ev_ineligible = make_dummy_evaluation("ema_trend_following", 0.95, eligible=False)
        ev_eligible = make_dummy_evaluation("rsi_mean_reversion", 0.40, eligible=True)

        ranked = self.ranker.rank([ev_ineligible, ev_eligible])
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0].rank, 1)
        self.assertEqual(ranked[0].strategy_id, "rsi_mean_reversion")
        self.assertTrue(ranked[0].eligible)

        self.assertEqual(ranked[1].rank, 2)
        self.assertEqual(ranked[1].strategy_id, "ema_trend_following")
        self.assertFalse(ranked[1].eligible)

    def test_ranker_deterministic_tie_break(self):
        # Identical score: tie-break by strategy_id ascending
        ev_rsi = make_dummy_evaluation("rsi_mean_reversion", 0.80)
        ev_ema = make_dummy_evaluation("ema_trend_following", 0.80)

        # Pass in reverse order: [RSI, EMA]
        ranked = self.ranker.rank([ev_rsi, ev_ema])
        self.assertEqual(ranked[0].strategy_id, "ema_trend_following")
        self.assertEqual(ranked[1].strategy_id, "rsi_mean_reversion")

    def test_ranker_input_order_independence(self):
        ev1 = make_dummy_evaluation("ema_trend_following", 0.75)
        ev2 = make_dummy_evaluation("rsi_mean_reversion", 0.75)

        ranked_order1 = self.ranker.rank([ev1, ev2])
        ranked_order2 = self.ranker.rank([ev2, ev1])

        self.assertEqual(
            [r.strategy_id for r in ranked_order1],
            [r.strategy_id for r in ranked_order2],
        )
        self.assertEqual(
            [r.score for r in ranked_order1],
            [r.score for r in ranked_order2],
        )

    def test_ranker_empty_and_invalid_evaluations(self):
        self.assertEqual(self.ranker.rank([]), ())

        with self.assertRaises(ValueError):
            self.ranker.rank("invalid")
        with self.assertRaises(ValueError):
            self.ranker.rank([make_dummy_evaluation("ema_trend_following", 0.5), "invalid"])

    def test_ranker_input_immutability(self):
        ev1 = make_dummy_evaluation("rsi_mean_reversion", 0.40)
        ev2 = make_dummy_evaluation("ema_trend_following", 0.90)
        original_list = [ev1, ev2]

        _ = self.ranker.rank(original_list)
        # Original caller-owned list must not be sorted in-place
        self.assertEqual(original_list[0].strategy_id, "rsi_mean_reversion")
        self.assertEqual(original_list[1].strategy_id, "ema_trend_following")

    # =========================================================================
    # 3. SELECTOR INTEGRATION WITH RANKING & EVALUATION
    # =========================================================================

    def test_selector_picks_highest_suitability_score(self):
        # EMA setup strong, RSI neutral mid-range
        ctx = make_context_with_features(
            regime=Regime.BULLISH,
            ema_9=65000.0,
            ema_21=64000.0,
            last_close=66000.0,
            rsi_14=50.0,
        )
        selection = self.selector.select(ctx, [self.candidate_ema, self.candidate_rsi])

        self.assertEqual(selection.selected_strategy_id, "ema_trend_following")
        self.assertEqual(selection.selector_id, "deterministic_regime_selector")
        self.assertEqual(selection.metadata["ranking_position"], 1)
        self.assertAlmostEqual(selection.confidence, 1.0, places=4)
        self.assertIn("highest suitability score", selection.selection_reason)

    def test_selector_picks_rsi_when_rsi_scores_higher(self):
        # Neutral regime, flat EMA (score low), RSI strongly oversold (<30)
        ctx = make_context_with_features(
            regime=Regime.NEUTRAL,
            ema_9=64000.0,
            ema_21=64000.0,
            last_close=64000.0,
            rsi_14=20.0,
        )
        selection = self.selector.select(ctx, [self.candidate_ema, self.candidate_rsi])

        self.assertEqual(selection.selected_strategy_id, "rsi_mean_reversion")
        self.assertEqual(selection.metadata["ranking_position"], 1)
        self.assertAlmostEqual(selection.confidence, 1.0, places=4)

    def test_selector_tie_breaker(self):
        # Both evaluate to identical scores
        ev1 = make_dummy_evaluation("rsi_mean_reversion", 0.70)
        ev2 = make_dummy_evaluation("ema_trend_following", 0.70)

        # Direct evaluation selection
        ctx = make_context_with_features()
        selection = self.selector.select(ctx, [ev1, ev2])
        self.assertEqual(selection.selected_strategy_id, "ema_trend_following")

    def test_selector_candidate_restriction_single_candidate(self):
        ctx = make_context_with_features(regime=Regime.BULLISH)
        # Only RSI candidate supplied
        selection_rsi = self.selector.select(ctx, [self.candidate_rsi])
        self.assertEqual(selection_rsi.selected_strategy_id, "rsi_mean_reversion")

        # Only EMA candidate supplied
        selection_ema = self.selector.select(ctx, [self.candidate_ema])
        self.assertEqual(selection_ema.selected_strategy_id, "ema_trend_following")

    def test_selector_order_independence(self):
        ctx = make_context_with_features(regime=Regime.BULLISH)
        sel1 = self.selector.select(ctx, [self.candidate_ema, self.candidate_rsi])
        sel2 = self.selector.select(ctx, [self.candidate_rsi, self.candidate_ema])

        self.assertEqual(sel1.selected_strategy_id, sel2.selected_strategy_id)
        self.assertEqual(sel1.confidence, sel2.confidence)
        self.assertEqual(sel1.selection_reason, sel2.selection_reason)

    def test_selector_select_from_ranked(self):
        ev1 = make_dummy_evaluation("rsi_mean_reversion", 0.85)
        ev2 = make_dummy_evaluation("ema_trend_following", 0.75)
        ranked = self.ranker.rank([ev1, ev2])

        selection = self.selector.select_from_ranked(ranked)
        self.assertEqual(selection.selected_strategy_id, "rsi_mean_reversion")
        self.assertEqual(selection.confidence, 0.85)
        self.assertEqual(selection.metadata["ranking_position"], 1)

    def test_selector_all_ineligible_fails_safely(self):
        ctx = make_context_with_features()
        cand_ema_ineligible = StrategyCandidate("ema_trend_following", "EMA", eligible=False)
        cand_rsi_ineligible = StrategyCandidate("rsi_mean_reversion", "RSI", eligible=False)

        with self.assertRaises(ValueError) as err:
            self.selector.select(ctx, [cand_ema_ineligible, cand_rsi_ineligible])
        self.assertIn("No eligible strategy", str(err.exception))

    def test_selector_empty_candidates_fails_safely(self):
        ctx = make_context_with_features()
        with self.assertRaises(ValueError) as err:
            self.selector.select(ctx, [])
        self.assertIn("empty candidates", str(err.exception))

    def test_selector_unregistered_candidate_fails_safely(self):
        ctx = make_context_with_features()
        unknown_cand = StrategyCandidate("unregistered_strat", "Unknown")

        with self.assertRaises(ValueError) as err:
            self.selector.select(ctx, [unknown_cand])
        self.assertIn("not registered", str(err.exception))

    # =========================================================================
    # 4. END-TO-END PIPELINE & SIGNAL RESOLUTION
    # =========================================================================

    def test_end_to_end_pipeline_flow_and_signal_generation(self):
        """
        MarketAnalysis -> StrategyContext -> Candidates -> Evaluator ->
        Ranker -> Selector -> Selected Strategy -> Existing Signal
        """
        # Bullish market with active EMA trend setup
        ctx = make_context_with_features(
            regime=Regime.BULLISH,
            ema_9=65000.0,
            ema_21=64000.0,
            last_close=65500.0,
            rsi_14=45.0,
        )

        candidates = [self.candidate_ema, self.candidate_rsi]

        # 1. Evaluate
        evaluations = [self.evaluator.evaluate(ctx, c) for c in candidates]
        self.assertEqual(len(evaluations), 2)

        # 2. Rank
        ranked = self.ranker.rank(evaluations)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0].rank, 1)
        self.assertEqual(ranked[0].strategy_id, "ema_trend_following")

        # 3. Select
        selection = self.selector.select_from_ranked(ranked, context=ctx)
        self.assertEqual(selection.selected_strategy_id, "ema_trend_following")

        # 4. Resolve selected strategy and generate Signal (Sprint 20/22 contract)
        selected_strategy = self.registry.get(selection.selected_strategy_id)
        self.assertIsInstance(selected_strategy, EMATrendFollowingStrategy)

        signal = selected_strategy.evaluate(ctx)
        self.assertIsInstance(signal, Signal)
        self.assertEqual(signal.strategy_id, "ema_trend_following")
        self.assertEqual(signal.symbol, "BTCUSDT")

    def test_pure_determinism_across_multiple_runs(self):
        ctx = make_context_with_features(
            regime=Regime.NEUTRAL,
            ema_9=64000.0,
            ema_21=64000.0,
            last_close=64000.0,
            rsi_14=20.0,
        )
        candidates = [self.candidate_rsi, self.candidate_ema]

        results = [
            self.selector.select(ctx, candidates)
            for _ in range(10)
        ]
        for res in results:
            self.assertEqual(res.selected_strategy_id, "rsi_mean_reversion")
            self.assertEqual(res.confidence, results[0].confidence)
            self.assertEqual(res.selection_reason, results[0].selection_reason)


if __name__ == "__main__":
    unittest.main()
