from strategy.base import BaseStrategy
from strategy.context import StrategyContext
from strategy.models import Signal, SignalDirection


class EMATrendFollowingStrategy(BaseStrategy):
    """
    Deterministic EMA Trend Following Strategy.

    Evaluates exponential moving averages (Fast EMA 9, Slow EMA 21) and price:
      - LONG: EMA(9) > EMA(21) and price > EMA(9)
      - SHORT: EMA(9) < EMA(21) and price < EMA(9)
      - FLAT: All other conditions, or missing/invalid features
    """

    strategy_id: str = "ema_trend_following"
    name: str = "EMA Trend Following Strategy"
    description: str = "Trend following strategy using 9 and 21 EMA directional alignment"

    def __init__(
        self,
        strategy_id: str | None = None,
        timeframe: str = "1H",
        fast_period: int = 9,
        slow_period: int = 21,
    ):
        super().__init__(strategy_id or self.strategy_id)
        self.timeframe = timeframe
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.fast_key = f"ema_{fast_period}"
        self.slow_key = f"ema_{slow_period}"

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

        values = tf_features.values
        fast_ema = values.get(self.fast_key)
        slow_ema = values.get(self.slow_key)
        price = values.get("last_close")

        # Fallback to ranking market price if last_close is absent
        if price is None and context.analysis.ranking is not None:
            # We strictly require numeric price
            price = None

        if fast_ema is None or slow_ema is None or price is None:
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.FLAT,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=0.0,
                reason="Missing required EMA or price features (requires ema_9, ema_21, last_close)",
                metadata={
                    "timeframe": self.timeframe,
                    "has_fast_ema": fast_ema is not None,
                    "has_slow_ema": slow_ema is not None,
                    "has_price": price is not None,
                },
            )

        if not isinstance(fast_ema, (int, float)) or not isinstance(slow_ema, (int, float)) or not isinstance(price, (int, float)):
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.FLAT,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=0.0,
                reason="Non-numeric EMA or price feature values encountered",
                metadata={"timeframe": self.timeframe},
            )

        fast_val = float(fast_ema)
        slow_val = float(slow_ema)
        price_val = float(price)

        metadata = {
            "timeframe": self.timeframe,
            "fast_ema": fast_val,
            "slow_ema": slow_val,
            "price": price_val,
        }

        # Long condition: EMA9 > EMA21 and price > EMA9
        if fast_val > slow_val and price_val > fast_val:
            # Deterministic normalized confidence based on relative separation, capped at 1.0
            # Separation = (price - fast_ema) / fast_ema + (fast_ema - slow_ema) / slow_ema
            separation = ((price_val - fast_val) / fast_val) + ((fast_val - slow_val) / slow_val)
            confidence = min(max(round(separation * 10.0, 4), 0.1), 1.0)
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.LONG,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=confidence,
                reason=f"LONG because {self.fast_key} > {self.slow_key} ({fast_val:.4f} > {slow_val:.4f}) and price > {self.fast_key} ({price_val:.4f} > {fast_val:.4f})",
                metadata=metadata,
            )

        # Short condition: EMA9 < EMA21 and price < EMA9
        if fast_val < slow_val and price_val < fast_val:
            separation = ((fast_val - price_val) / fast_val) + ((slow_val - fast_val) / slow_val)
            confidence = min(max(round(separation * 10.0, 4), 0.1), 1.0)
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.SHORT,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=confidence,
                reason=f"SHORT because {self.fast_key} < {self.slow_key} ({fast_val:.4f} < {slow_val:.4f}) and price < {self.fast_key} ({price_val:.4f} < {fast_val:.4f})",
                metadata=metadata,
            )

        # Otherwise FLAT
        if fast_val == slow_val:
            reason = f"FLAT because {self.fast_key} equals {self.slow_key} ({fast_val:.4f} == {slow_val:.4f})"
        elif fast_val > slow_val and price_val <= fast_val:
            reason = f"FLAT because {self.fast_key} > {self.slow_key} but price <= {self.fast_key} ({price_val:.4f} <= {fast_val:.4f})"
        elif fast_val < slow_val and price_val >= fast_val:
            reason = f"FLAT because {self.fast_key} < {self.slow_key} but price >= {self.fast_key} ({price_val:.4f} >= {fast_val:.4f})"
        else:
            reason = "FLAT condition"

        return Signal(
            symbol=context.symbol,
            direction=SignalDirection.FLAT,
            strategy_id=self.strategy_id,
            timestamp=context.timestamp,
            confidence=0.0,
            reason=reason,
            metadata=metadata,
        )
