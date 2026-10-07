from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from data.candle import Candle
from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from market.pipeline import MarketAnalysisPipeline
from regime.models import Regime, TimeframeRegime
from scanner.ranking_engine import RankedMarket


def make_candle(symbol, granularity):
    return Candle(
        symbol=symbol,
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=10.0,
        quote_volume=1000.0,
        granularity=granularity,
    )


def make_ranked_market(symbol, score=85.0):
    return RankedMarket(
        symbol=symbol,
        score=score,
        liquidity_score=90.0,
        movement_score=80.0,
        spread_score=85.0,
        open_interest_score=75.0,
        funding_score=95.0,
    )


class TestMarketAnalysisPipeline(unittest.TestCase):

    def setUp(self):
        self.mtf_manager = Mock()
        self.feature_engine = Mock()
        self.regime_engine = Mock()

        self.pipeline = MarketAnalysisPipeline(
            mtf_manager=self.mtf_manager,
            feature_engine=self.feature_engine,
            regime_engine=self.regime_engine,
            granularities=("15m", "1H"),
            lookback_ms=24 * 60 * 60 * 1000,
        )

    def test_pipeline_argument_validation(self):
        with self.assertRaises(ValueError):
            MarketAnalysisPipeline(lookback_ms=0)

        with self.assertRaises(ValueError):
            self.pipeline.analyze_symbol("")

        with self.assertRaises(ValueError):
            self.pipeline.analyze_shortlist("invalid")

        with self.assertRaises(ValueError):
            self.pipeline.analyze_shortlist([make_ranked_market("BTCUSDT")], limit=0)

        with self.assertRaises(ValueError):
            self.pipeline.analyze_shortlist(["not_ranked_market"])

    def test_end_to_end_successful_symbol_analysis(self):
        symbol = "BTCUSDT"
        ranking = make_ranked_market(symbol)
        candles = {
            "15m": [make_candle(symbol, "15m")],
            "1H": [make_candle(symbol, "1H")],
        }
        features = {
            "15m": TimeframeFeatures(symbol, "15m", None, 1, {"last_close": 105.0}),
            "1H": TimeframeFeatures(symbol, "1H", None, 1, {"last_close": 105.0}),
        }
        regimes = {
            "15m": TimeframeRegime(symbol, "15m", None, 1, Regime.BULLISH),
            "1H": TimeframeRegime(symbol, "1H", None, 1, Regime.BEARISH),
        }

        self.mtf_manager.get_historical_candles.return_value = candles
        self.feature_engine.calculate.return_value = features
        self.regime_engine.classify.return_value = regimes

        start_time = 0
        end_time = 3600000

        analysis = self.pipeline.analyze_symbol(
            symbol=symbol,
            ranking=ranking,
            start_time=start_time,
            end_time=end_time,
        )

        self.assertTrue(analysis.success)
        self.assertIsNone(analysis.error)
        self.assertEqual(analysis.symbol, symbol)
        self.assertEqual(analysis.ranking, ranking)
        self.assertEqual(analysis.candles_by_granularity, candles)
        self.assertEqual(analysis.features_by_granularity, features)
        self.assertEqual(analysis.regimes_by_granularity, regimes)

        self.mtf_manager.get_historical_candles.assert_called_once_with(
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            granularities=("15m", "1H"),
        )
        self.feature_engine.calculate.assert_called_once_with(candles)
        self.regime_engine.classify.assert_called_once_with(features)

    def test_analyze_shortlist_processes_up_to_limit(self):
        markets = [
            make_ranked_market("BTCUSDT", 95.0),
            make_ranked_market("ETHUSDT", 90.0),
            make_ranked_market("SOLUSDT", 85.0),
        ]

        self.mtf_manager.get_historical_candles.return_value = {}
        self.feature_engine.calculate.return_value = {}
        self.regime_engine.classify.return_value = {}

        results = self.pipeline.analyze_shortlist(
            ranked_markets=markets,
            limit=2,
            start_time=0,
            end_time=3600000,
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].symbol, "BTCUSDT")
        self.assertEqual(results[1].symbol, "ETHUSDT")
        self.assertEqual(self.mtf_manager.get_historical_candles.call_count, 2)

    def test_mtf_failure_marks_symbol_failed_and_does_not_abort_others(self):
        markets = [
            make_ranked_market("FAILUSDT", 95.0),
            make_ranked_market("PASSUSDT", 90.0),
        ]

        def mock_fetch(symbol, start_time, end_time, granularities):
            if symbol == "FAILUSDT":
                raise RuntimeError("API timeout fetching candles")
            return {"15m": [make_candle(symbol, "15m")]}

        self.mtf_manager.get_historical_candles.side_effect = mock_fetch
        self.feature_engine.calculate.return_value = {
            "15m": TimeframeFeatures("PASSUSDT", "15m", None, 1, {"last_close": 100.0})
        }
        self.regime_engine.classify.return_value = {
            "15m": TimeframeRegime("PASSUSDT", "15m", None, 1, Regime.BULLISH)
        }

        results = self.pipeline.analyze_shortlist(
            ranked_markets=markets,
            limit=2,
            start_time=0,
            end_time=3600000,
        )

        self.assertEqual(len(results), 2)
        failed_analysis = results[0]
        passed_analysis = results[1]

        self.assertFalse(failed_analysis.success)
        self.assertIn("Historical candles failed", failed_analysis.error)
        self.assertEqual(len(failed_analysis.features_by_granularity), 0)
        self.assertEqual(len(failed_analysis.regimes_by_granularity), 0)

        self.assertTrue(passed_analysis.success)
        self.assertIsNone(passed_analysis.error)
        self.assertEqual(passed_analysis.regimes_by_granularity["15m"].regime, Regime.BULLISH)

    def test_feature_engine_failure_handled_safely(self):
        symbol = "BTCUSDT"
        self.mtf_manager.get_historical_candles.return_value = {"15m": []}
        self.feature_engine.calculate.side_effect = ValueError("Insufficient candle history")

        analysis = self.pipeline.analyze_symbol(
            symbol=symbol,
            start_time=0,
            end_time=3600000,
        )

        self.assertFalse(analysis.success)
        self.assertIn("Feature calculation failed", analysis.error)
        self.assertEqual(len(analysis.features_by_granularity), 0)
        self.assertEqual(len(analysis.regimes_by_granularity), 0)
        self.regime_engine.classify.assert_not_called()

    def test_regime_engine_failure_handled_safely(self):
        symbol = "BTCUSDT"
        self.mtf_manager.get_historical_candles.return_value = {"15m": []}
        self.feature_engine.calculate.return_value = {
            "15m": TimeframeFeatures(symbol, "15m", None, 0, {})
        }
        self.regime_engine.classify.side_effect = ValueError("Classifier failed")

        analysis = self.pipeline.analyze_symbol(
            symbol=symbol,
            start_time=0,
            end_time=3600000,
        )

        self.assertFalse(analysis.success)
        self.assertIn("Regime classification failed", analysis.error)
        self.assertEqual(len(analysis.regimes_by_granularity), 0)
        self.assertEqual(len(analysis.features_by_granularity), 1)

    def test_output_dataclass_is_immutable(self):
        analysis = MarketAnalysis(
            symbol="BTCUSDT",
            ranking=None,
            candles_by_granularity={"15m": []},
            features_by_granularity={},
            regimes_by_granularity={},
            success=True,
        )

        with self.assertRaises(TypeError):
            analysis.candles_by_granularity["15m"] = [make_candle("BTCUSDT", "15m")]

        with self.assertRaises(Exception):
            analysis.symbol = "ETHUSDT"


if __name__ == "__main__":
    unittest.main()
