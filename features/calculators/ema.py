from features.calculators.base import FeatureCalculator


class EMACalculator(FeatureCalculator):
    """
    Exponential Moving Average (EMA) Calculator.

    Calculates the latest EMA value across the chronological close prices.
    Uses the standard formula:
      multiplier = 2.0 / (period + 1.0)
      EMA_initial = simple average of the first `period` close prices
      EMA_t = (Close_t * multiplier) + (EMA_{t-1} * (1.0 - multiplier))

    Output dictionary key: 'ema_<period>'
    """

    def __init__(self, period: int = 14):
        if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
            raise ValueError("EMA period must be a positive integer")

        self.period = period
        self.name = f"ema_{period}"
        self.minimum_candles = period

    def calculate(self, candles):
        if len(candles) < self.minimum_candles:
            raise ValueError(f"Insufficient candles for EMA calculation (requires at least {self.minimum_candles})")

        closes = [c.close for c in candles]
        period = self.period
        multiplier = 2.0 / (period + 1.0)

        # Seed initial EMA with the arithmetic mean of the first `period` closes
        ema = sum(closes[:period]) / period

        # Iteratively update EMA across subsequent candles
        for close in closes[period:]:
            ema = (close * multiplier) + (ema * (1.0 - multiplier))

        return {self.name: float(ema)}
