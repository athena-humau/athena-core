from features.calculators.base import FeatureCalculator


class BasicPriceCalculator(FeatureCalculator):

    name = "basic_price"
    minimum_candles = 1

    def calculate(self, candles):

        candle = candles[-1]

        return {
            "last_open": candle.open,
            "last_high": candle.high,
            "last_low": candle.low,
            "last_close": candle.close,
            "last_volume": candle.volume,
        }
