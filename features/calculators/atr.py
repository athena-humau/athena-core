from features.calculators.base import FeatureCalculator


class ATRCalculator(FeatureCalculator):
    """
    Average True Range (ATR) Calculator.

    Calculates True Range (TR) for each candle:
      For candle 0: TR = high_0 - low_0
      For candle t > 0:
        TR_t = max(high_t - low_t, abs(high_t - close_{t-1}), abs(low_t - close_{t-1}))

    Initial ATR (over the first `period` candles):
      ATR_0 = sum(TR[:period]) / period

    Wilder's smoothing over subsequent candles:
      ATR_t = (ATR_{t-1} * (period - 1) + TR_t) / period

    Output dictionary key: 'atr_<period>'
    """

    def __init__(self, period: int = 14):
        if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
            raise ValueError("ATR period must be a positive integer")

        self.period = period
        self.name = f"atr_{period}"
        self.minimum_candles = period

    def calculate(self, candles):
        if len(candles) < self.minimum_candles:
            raise ValueError(f"Insufficient candles for ATR calculation (requires at least {self.minimum_candles})")

        period = self.period
        true_ranges = []

        for i, candle in enumerate(candles):
            if i == 0:
                tr = candle.high - candle.low
            else:
                prev_close = candles[i - 1].close
                tr = max(
                    candle.high - candle.low,
                    abs(candle.high - prev_close),
                    abs(candle.low - prev_close),
                )
            true_ranges.append(tr)

        # Initial simple average of first `period` true ranges
        atr = sum(true_ranges[:period]) / period

        # Wilder's smoothing for subsequent candles
        for tr in true_ranges[period:]:
            atr = ((atr * (period - 1)) + tr) / period

        return {self.name: float(atr)}
