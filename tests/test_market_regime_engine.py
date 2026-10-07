from datetime import datetime, timezone
import unittest

from features.models import TimeframeFeatures
from regime.classifiers.base import RegimeClassifier
from regime.market_regime_engine import MarketRegimeEngine
from regime.models import Regime, TimeframeRegime


def make_features(
    granularity="1m",
    last_open=100.0,
    last_close=105.0,
    symbol="BTCUSDT",
    candle_count=10,
):
    return TimeframeFeatures(
        symbol=symbol,
        granularity=granularity,
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        candle_count=candle_count,
        values={
            "last_open": last_open,
            "last_close": last_close,
        },
    )


class NeutralClassifier(RegimeClassifier):

    def classify(self, features):

        return Regime.NEUTRAL


class InvalidClassifier(RegimeClassifier):

    def classify(self, features):

        return "bullish"


class TestMarketRegimeEngine(unittest.TestCase):

    def test_basic_price_classifier_returns_directional_regimes(self):

        engine = MarketRegimeEngine()

        bullish = engine.classify({"1m": make_features()})["1m"]
        bearish = engine.classify(
            {"1m": make_features(last_close=95.0)}
        )["1m"]
        neutral = engine.classify(
            {"1m": make_features(last_close=100.0)}
        )["1m"]

        self.assertEqual(bullish.regime, Regime.BULLISH)
        self.assertEqual(bearish.regime, Regime.BEARISH)
        self.assertEqual(neutral.regime, Regime.NEUTRAL)

    def test_missing_price_features_returns_unknown(self):

        features = make_features()
        features = TimeframeFeatures(
            symbol=features.symbol,
            granularity=features.granularity,
            timestamp=features.timestamp,
            candle_count=features.candle_count,
            values={},
        )

        result = MarketRegimeEngine().classify({"1m": features})

        self.assertEqual(result["1m"].regime, Regime.UNKNOWN)

    def test_empty_features_returns_unknown(self):

        empty_features = TimeframeFeatures(
            symbol=None,
            granularity="1m",
            timestamp=None,
            candle_count=0,
            values={},
        )

        result = MarketRegimeEngine().classify({"1m": empty_features})

        self.assertEqual(result["1m"].regime, Regime.UNKNOWN)

    def test_multiple_timeframes_remain_independent_and_deterministic(self):

        result = MarketRegimeEngine().classify(
            {
                "5m": make_features("5m", last_close=95.0),
                "1m": make_features("1m", last_close=105.0),
            }
        )

        self.assertEqual(list(result), ["1m", "5m"])
        self.assertEqual(result["1m"].regime, Regime.BULLISH)
        self.assertEqual(result["5m"].regime, Regime.BEARISH)

    def test_output_preserves_metadata_and_is_immutable(self):

        features = make_features("5m")

        result = MarketRegimeEngine().classify({"5m": features})["5m"]

        self.assertIsInstance(result, TimeframeRegime)
        self.assertEqual(result.symbol, features.symbol)
        self.assertEqual(result.granularity, features.granularity)
        self.assertEqual(result.timestamp, features.timestamp)
        self.assertEqual(result.candle_count, features.candle_count)

        with self.assertRaises(AttributeError):
            result.regime = Regime.UNKNOWN

    def test_constructor_classifier_injection(self):

        result = MarketRegimeEngine(NeutralClassifier()).classify(
            {"1m": make_features()}
        )

        self.assertEqual(result["1m"].regime, Regime.NEUTRAL)

    def test_invalid_classifier_or_classifier_output_raises_error(self):

        with self.assertRaises(ValueError):
            MarketRegimeEngine(object())

        with self.assertRaises(ValueError):
            MarketRegimeEngine(InvalidClassifier()).classify(
                {"1m": make_features()}
            )

    def test_invalid_feature_groups_raise_errors(self):

        valid = make_features()
        empty = TimeframeFeatures(None, "1m", None, 0, {})
        invalid_groups = [
            {"1m": object()},
            {"5m": valid},
            {"": valid},
            {
                "1m": TimeframeFeatures(
                    "BTCUSDT",
                    "1m",
                    datetime(2024, 1, 1),
                    1,
                    {},
                ),
            },
            {"1m": TimeframeFeatures(None, "1m", None, -1, {})},
            {
                "1m": TimeframeFeatures(
                    None,
                    "1m",
                    datetime(2024, 1, 1, tzinfo=timezone.utc),
                    0,
                    {},
                ),
            },
            {
                "1m": valid,
                "5m": make_features("5m", symbol="ETHUSDT"),
            },
            {"1m": empty, "5m": valid},
        ]

        for feature_group in invalid_groups:
            with self.subTest(feature_group=feature_group):
                with self.assertRaises(ValueError):
                    MarketRegimeEngine().classify(feature_group)

    def test_invalid_top_level_input_raises_error(self):

        with self.assertRaises(ValueError):
            MarketRegimeEngine().classify([])


if __name__ == "__main__":
    unittest.main()
