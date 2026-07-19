import math
from datetime import datetime, timedelta

from data.candle import Candle
from features.calculators.base import FeatureCalculator
from features.calculators.basic_price import BasicPriceCalculator
from features.models import TimeframeFeatures


class FeatureEngine:

    def __init__(self, calculators=None):

        if calculators is None:
            calculators = (BasicPriceCalculator(),)

        if not isinstance(calculators, (list, tuple)):
            raise ValueError("Calculators must be a list or tuple")

        self.calculators = tuple(calculators)
        self._validate_calculators()

    def calculate(self, candles_by_granularity):

        if not isinstance(candles_by_granularity, dict):
            raise ValueError("Candles must be grouped in a dictionary")

        features_by_granularity = {}

        for granularity in sorted(candles_by_granularity):
            candles = candles_by_granularity[granularity]
            symbol = self._validate_candles(granularity, candles)

            values = self._calculate_values(candles)

            features_by_granularity[granularity] = TimeframeFeatures(
                symbol=symbol,
                granularity=granularity,
                timestamp=candles[-1].timestamp if candles else None,
                candle_count=len(candles),
                values=values,
            )

        return features_by_granularity

    def _validate_calculators(self):

        names = set()

        for calculator in self.calculators:

            if not isinstance(calculator, FeatureCalculator):
                raise ValueError("Calculator must implement FeatureCalculator")

            if not isinstance(calculator.name, str) or not calculator.name:
                raise ValueError("Calculator name must be a non-empty string")

            if calculator.name in names:
                raise ValueError("Duplicate calculator name")

            if (
                not isinstance(calculator.minimum_candles, int)
                or isinstance(calculator.minimum_candles, bool)
                or calculator.minimum_candles < 1
            ):
                raise ValueError("Calculator minimum_candles must be positive")

            names.add(calculator.name)

    def _calculate_values(self, candles):

        if not candles:
            return {}

        values = {}

        for calculator in self.calculators:

            if len(candles) < calculator.minimum_candles:
                raise ValueError(
                    f"Insufficient candles for calculator {calculator.name}"
                )

            calculator_values = calculator.calculate(candles)

            self._validate_calculator_output(calculator_values)

            duplicate_names = set(values).intersection(calculator_values)

            if duplicate_names:
                raise ValueError("Duplicate feature name")

            values.update(calculator_values)

        return values

    @staticmethod
    def _validate_candles(granularity, candles):

        if not isinstance(granularity, str) or not granularity:
            raise ValueError("Candle granularity must be a non-empty string")

        if not isinstance(candles, list):
            raise ValueError("Timeframe candles must be a list")

        if not candles:
            return None

        symbol = None
        previous_timestamp = None

        for candle in candles:

            if not isinstance(candle, Candle):
                raise ValueError("Feature input must contain Candle objects")

            if candle.granularity != granularity:
                raise ValueError("Candle granularity does not match its group")

            if not isinstance(candle.symbol, str) or not candle.symbol:
                raise ValueError("Candle symbol must be a non-empty string")

            if symbol is None:
                symbol = candle.symbol
            elif candle.symbol != symbol:
                raise ValueError("Timeframe candles must use one symbol")

            FeatureEngine._validate_candle(candle)

            if (
                previous_timestamp is not None
                and candle.timestamp <= previous_timestamp
            ):
                raise ValueError(
                    "Timeframe candles must be strictly chronological"
                )

            previous_timestamp = candle.timestamp

        return symbol

    @staticmethod
    def _validate_candle(candle):

        if not isinstance(candle.timestamp, datetime):
            raise ValueError("Candle timestamp must be a datetime")

        if (
            candle.timestamp.tzinfo is None
            or candle.timestamp.utcoffset() != timedelta(0)
        ):
            raise ValueError("Candle timestamp must be UTC")

        numeric_values = (
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
            candle.quote_volume,
        )

        if not all(
            type(value) in (int, float)
            and (
                type(value) is int
                or math.isfinite(value)
            )
            for value in numeric_values
        ):
            raise ValueError("Candle values must be finite numbers")

        if min(candle.open, candle.high, candle.low, candle.close) <= 0:
            raise ValueError("Candle prices must be positive")

        if candle.volume < 0 or candle.quote_volume < 0:
            raise ValueError("Candle volumes cannot be negative")

        if (
            candle.low > min(candle.open, candle.close)
            or candle.high < max(candle.open, candle.close)
        ):
            raise ValueError("Invalid candle OHLC relationship")

    @staticmethod
    def _validate_calculator_output(values):

        if not isinstance(values, dict):
            raise ValueError("Calculator output must be a dictionary")

        for name, value in values.items():

            if not isinstance(name, str) or not name:
                raise ValueError("Feature name must be a non-empty string")

            if type(value) not in (int, float, bool):
                raise ValueError("Feature value must be int, float, or bool")

            if type(value) is float and not math.isfinite(value):
                raise ValueError("Feature value must be finite")
