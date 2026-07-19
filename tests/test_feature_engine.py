from datetime import datetime, timedelta, timezone
import unittest

from data.candle import Candle
from features.calculators.base import FeatureCalculator
from features.feature_engine import FeatureEngine
from features.models import TimeframeFeatures


def make_candle(timestamp, granularity="1m", symbol="BTCUSDT"):
    return Candle(
        symbol=symbol,
        timestamp=timestamp,
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=10.0,
        quote_volume=1000.0,
        granularity=granularity,
    )


class StaticCalculator(FeatureCalculator):

    name = "static"
    minimum_candles = 1

    def calculate(self, candles):

        return {"count": len(candles), "ready": True}


class DuplicateFeatureCalculator(FeatureCalculator):

    name = "duplicate"

    def calculate(self, candles):

        return {"count": 1}


class InsufficientCalculator(FeatureCalculator):

    name = "insufficient"
    minimum_candles = 2

    def calculate(self, candles):

        return {"value": 1}


class InvalidOutputCalculator(FeatureCalculator):

    name = "invalid_output"

    def calculate(self, candles):

        return {"value": float("nan")}


class TestFeatureEngine(unittest.TestCase):

    def setUp(self):

        self.timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def test_default_calculator_returns_last_candle_prices(self):

        candle = make_candle(self.timestamp)

        result = FeatureEngine().calculate({"1m": [candle]})

        features = result["1m"]
        self.assertIsInstance(features, TimeframeFeatures)
        self.assertEqual(features.symbol, "BTCUSDT")
        self.assertEqual(features.timestamp, self.timestamp)
        self.assertEqual(features.candle_count, 1)
        self.assertEqual(
            dict(features.values),
            {
                "last_open": 100.0,
                "last_high": 110.0,
                "last_low": 90.0,
                "last_close": 105.0,
                "last_volume": 10.0,
            },
        )

    def test_constructor_calculators_support_int_float_and_bool_outputs(self):

        result = FeatureEngine([StaticCalculator()]).calculate(
            {"1m": [make_candle(self.timestamp)]}
        )

        self.assertEqual(
            dict(result["1m"].values),
            {"count": 1, "ready": True},
        )

    def test_multiple_timeframes_are_deterministic_and_independent(self):

        candles = {
            "5m": [make_candle(self.timestamp, "5m")],
            "1m": [make_candle(self.timestamp, "1m")],
        }
        engine = FeatureEngine([StaticCalculator()])

        first = engine.calculate(candles)
        second = engine.calculate(candles)

        self.assertEqual(list(first), ["1m", "5m"])
        self.assertEqual(first, second)
        self.assertEqual(dict(first["1m"].values), {"count": 1, "ready": True})
        self.assertEqual(dict(first["5m"].values), {"count": 1, "ready": True})

    def test_empty_timeframe_returns_immutable_empty_snapshot(self):

        result = FeatureEngine().calculate({"1m": []})
        features = result["1m"]

        self.assertIsNone(features.symbol)
        self.assertIsNone(features.timestamp)
        self.assertEqual(features.candle_count, 0)
        self.assertEqual(dict(features.values), {})

        with self.assertRaises(TypeError):
            features.values["new"] = 1

    def test_input_candles_are_not_mutated(self):

        candle = make_candle(self.timestamp)
        candles = [candle]

        FeatureEngine().calculate({"1m": candles})

        self.assertEqual(candles, [candle])
        self.assertEqual(candle.close, 105.0)

    def test_duplicate_feature_name_and_insufficient_history_raise_errors(self):

        candle_group = {"1m": [make_candle(self.timestamp)]}

        with self.assertRaises(ValueError):
            FeatureEngine(
                [StaticCalculator(), DuplicateFeatureCalculator()]
            ).calculate(candle_group)

        with self.assertRaises(ValueError):
            FeatureEngine([InsufficientCalculator()]).calculate(candle_group)

    def test_invalid_calculator_configuration_or_output_raises_error(self):

        with self.assertRaises(ValueError):
            FeatureEngine([StaticCalculator(), StaticCalculator()])

        with self.assertRaises(ValueError):
            FeatureEngine([InvalidOutputCalculator()]).calculate(
                {"1m": [make_candle(self.timestamp)]}
            )

        with self.assertRaises(ValueError):
            FeatureEngine([object()])

    def test_invalid_candle_groups_raise_errors(self):

        valid = make_candle(self.timestamp)
        invalid_groups = [
            {"1m": (valid,)},
            {"1m": ["not-a-candle"]},
            {"5m": [valid]},
            {
                "1m": [
                    valid,
                    make_candle(
                        self.timestamp + timedelta(minutes=1),
                        symbol="ETHUSDT",
                    ),
                ],
            },
            {"1m": [valid, make_candle(self.timestamp)]},
            {
                "1m": [
                    make_candle(self.timestamp + timedelta(minutes=1)),
                    valid,
                ],
            },
            {
                "1m": [
                    make_candle(
                        datetime(2024, 1, 1),
                    ),
                ],
            },
        ]

        for candle_group in invalid_groups:
            with self.subTest(candle_group=candle_group):
                with self.assertRaises(ValueError):
                    FeatureEngine().calculate(candle_group)

    def test_invalid_top_level_input_raises_error(self):

        with self.assertRaises(ValueError):
            FeatureEngine().calculate([])


if __name__ == "__main__":
    unittest.main()
