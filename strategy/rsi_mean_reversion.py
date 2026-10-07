from strategy.base import BaseStrategy
from strategy.context import StrategyContext
from strategy.models import Signal, SignalDirection


class RSIMeanReversionStrategy(BaseStrategy):
    """
    Deterministic RSI Mean Reversion Strategy.

    Evaluates 14-period RSI:
      - Oversold -> LONG: RSI(14) < 30
      - Overbought -> SHORT: RSI(14) > 70
      - Neutral -> FLAT: 30 <= RSI(14) <= 70
    """

    strategy_id: str = "rsi_mean_reversion"
    name: str = "RSI Mean Reversion Strategy"
    description: str = "Mean reversion strategy identifying oversold and overbought RSI conditions"

    def __init__(
        self,
        strategy_id: str | None = None,
        timeframe: str = "1H",
        period: int = 14,
        oversold_threshold: float = 30.0,
        overbought_threshold: float = 70.0,
    ):
        super().__init__(strategy_id or self.strategy_id)
        self.timeframe = timeframe
        self.period = period
        self.oversold_threshold = oversold_threshold
        self.overbought_threshold = overbought_threshold
        self.rsi_key = f"rsi_{period}"

    def evaluate(self, context: StrategyContext) -> Signal:
        if not context.analysis.success:
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.FLAT,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=0.0,
                reason="Market analysis failed",
                metadata={"error": context.analysis.error or "Analysis unsuccessful"},
            )

        features_by_granularity = context.analysis.features_by_granularity
        tf_features = features_by_granularity.get(self.timeframe)

        if tf_features is None:
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.FLAT,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=0.0,
                reason=f"Missing feature data for timeframe '{self.timeframe}'",
                metadata={"timeframe": self.timeframe},
            )

        rsi_val = tf_features.values.get(self.rsi_key)

        if rsi_val is None:
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.FLAT,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=0.0,
                reason=f"Missing required feature '{self.rsi_key}'",
                metadata={"timeframe": self.timeframe, "rsi_key": self.rsi_key},
            )

        if not isinstance(rsi_val, (int, float)):
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.FLAT,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=0.0,
                reason="Non-numeric RSI feature value encountered",
                metadata={"timeframe": self.timeframe},
            )

        rsi = float(rsi_val)

        metadata = {
            "timeframe": self.timeframe,
            "rsi": rsi,
            "oversold_threshold": self.oversold_threshold,
            "overbought_threshold": self.overbought_threshold,
        }

        # Oversold -> LONG
        if rsi < self.oversold_threshold:
            # Deterministic normalized confidence based on distance below oversold threshold:
            # Distance in [0, 30] normalized to [0.1, 1.0]
            distance = self.oversold_threshold - rsi
            confidence = min(max(round(0.1 + (distance / self.oversold_threshold) * 0.9, 4), 0.1), 1.0)
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.LONG,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=confidence,
                reason=f"LONG because {self.rsi_key} < {self.oversold_threshold} ({rsi:.2f} < {self.oversold_threshold})",
                metadata=metadata,
            )

        # Overbought -> SHORT
        if rsi > self.overbought_threshold:
            # Distance in [0, 30] (from 70 to 100) normalized to [0.1, 1.0]
            max_overbought_span = 100.0 - self.overbought_threshold
            distance = rsi - self.overbought_threshold
            confidence = min(max(round(0.1 + (distance / max_overbought_span) * 0.9, 4), 0.1), 1.0)
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.SHORT,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=confidence,
                reason=f"SHORT because {self.rsi_key} > {self.overbought_threshold} ({rsi:.2f} > {self.overbought_threshold})",
                metadata=metadata,
            )

        # Otherwise FLAT (30 <= rsi <= 70)
        return Signal(
            symbol=context.symbol,
            direction=SignalDirection.FLAT,
            strategy_id=self.strategy_id,
            timestamp=context.timestamp,
            confidence=0.0,
            reason=f"FLAT because {self.rsi_key} is between {self.oversold_threshold} and {self.overbought_threshold} ({rsi:.2f})",
            metadata=metadata,
        )
