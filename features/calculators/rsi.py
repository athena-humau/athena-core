from features.calculators.base import FeatureCalculator


class RSICalculator(FeatureCalculator):
    """
    Relative Strength Index (RSI) Calculator.

    Calculates the 14-period (or configurable period) RSI using Wilder's smoothed moving average:
      Change_t = Close_t - Close_{t-1}
      Gain_t = max(Change_t, 0)
      Loss_t = max(-Change_t, 0)

      Initial average gain/loss (over first `period` changes, i.e., from first `period + 1` candles):
        AvgGain_0 = sum(Gains[:period]) / period
        AvgLoss_0 = sum(Losses[:period]) / period

      Wilder smoothing:
        AvgGain_t = (AvgGain_{t-1} * (period - 1) + Gain_t) / period
        AvgLoss_t = (AvgLoss_{t-1} * (period - 1) + Loss_t) / period

      RSI = 100.0 - (100.0 / (1.0 + RS)), where RS = AvgGain / AvgLoss

    Edge Cases:
      - If AvgLoss == 0: RSI = 100.0 (all positive price movement)
      - If AvgGain == 0: RSI = 0.0 (all negative price movement)
      - If both AvgGain == 0 and AvgLoss == 0 (constant price): RSI = 50.0

    Requires at least `period + 1` candles to establish `period` changes.
    Output dictionary key: 'rsi_<period>'
    """

    def __init__(self, period: int = 14):
        if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
            raise ValueError("RSI period must be a positive integer")

        self.period = period
        self.name = f"rsi_{period}"
        self.minimum_candles = period + 1

    def calculate(self, candles):
        if len(candles) < self.minimum_candles:
            raise ValueError(f"Insufficient candles for RSI calculation (requires at least {self.minimum_candles})")

        closes = [c.close for c in candles]
        period = self.period

        # Compute price changes
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        # Seed initial average gain and loss over the first `period` changes
        initial_gains = [max(change, 0.0) for change in changes[:period]]
        initial_losses = [max(-change, 0.0) for change in changes[:period]]

        avg_gain = sum(initial_gains) / period
        avg_loss = sum(initial_losses) / period

        # Wilder's smoothing across remaining changes
        for change in changes[period:]:
            gain = max(change, 0.0)
            loss = max(-change, 0.0)
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period

        # Calculate bounded RSI
        if avg_loss == 0.0:
            if avg_gain == 0.0:
                rsi = 50.0  # Constant price
            else:
                rsi = 100.0
        elif avg_gain == 0.0:
            rsi = 0.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        return {self.name: float(rsi)}
