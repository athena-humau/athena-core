from ai.evaluator import StrategyEvaluator
from ai.models import StrategyCandidate, StrategyEvaluation
from regime.models import Regime
from strategy.context import StrategyContext
from strategy.registry import StrategyRegistry


class DeterministicStrategyEvaluator(StrategyEvaluator):
    """
    Deterministic Strategy Evaluator measuring strategy suitability against market context.

    Weighted Scoring Formula:
      overall_score = (regime_alignment * 0.40) + (signal_alignment * 0.40) + (data_quality * 0.20)

    Scoring Semantics:
      The overall score is a bounded suitability metric in [0.0, 1.0].
      It represents how well the candidate strategy fits the currently supplied market context.
      It is strictly NOT a probability of profit or expected financial return.
    """

    evaluator_id: str = "deterministic_strategy_evaluator"
    name: str = "Deterministic Strategy Evaluator"
    description: str = "Evaluates regime alignment, signal alignment, and data quality deterministically"

    WEIGHT_REGIME = 0.40
    WEIGHT_SIGNAL = 0.40
    WEIGHT_DATA = 0.20

    def __init__(
        self,
        evaluator_id: str | None = None,
        registry: StrategyRegistry | None = None,
        timeframe: str = "1H",
    ):
        super().__init__(evaluator_id or self.evaluator_id)
        self.registry = registry
        self.timeframe = timeframe

    def evaluate(
        self,
        context: StrategyContext,
        candidate: StrategyCandidate,
    ) -> StrategyEvaluation:
        if not isinstance(context, StrategyContext):
            raise ValueError("context must be an instance of StrategyContext")

        if not isinstance(candidate, StrategyCandidate):
            raise ValueError("candidate must be an instance of StrategyCandidate")

        # Candidate registry verification if registry is supplied
        if self.registry is not None and not self.registry.has(candidate.strategy_id):
            raise ValueError(f"Candidate strategy '{candidate.strategy_id}' is not registered in StrategyRegistry")

        # Ineligible candidate check
        if not candidate.eligible:
            return StrategyEvaluation(
                strategy_id=candidate.strategy_id,
                eligible=False,
                score=0.0,
                regime_alignment=0.0,
                signal_alignment=0.0,
                data_quality=0.0,
                evaluation_reason=f"Candidate strategy '{candidate.strategy_id}' marked ineligible",
                metadata={"timeframe": self.timeframe},
            )

        # Analysis status check
        if not context.analysis.success:
            return StrategyEvaluation(
                strategy_id=candidate.strategy_id,
                eligible=False,
                score=0.0,
                regime_alignment=0.0,
                signal_alignment=0.0,
                data_quality=0.0,
                evaluation_reason="Market analysis failed",
                metadata={"timeframe": self.timeframe, "error": context.analysis.error or "Analysis unsuccessful"},
            )

        # Delegate evaluation by strategy ID
        if candidate.strategy_id == "ema_trend_following":
            return self._evaluate_ema_trend_following(context, candidate)
        elif candidate.strategy_id == "rsi_mean_reversion":
            return self._evaluate_rsi_mean_reversion(context, candidate)
        else:
            # Unknown strategy fallback
            return StrategyEvaluation(
                strategy_id=candidate.strategy_id,
                eligible=False,
                score=0.0,
                regime_alignment=0.0,
                signal_alignment=0.0,
                data_quality=0.0,
                evaluation_reason=f"Unsupported strategy '{candidate.strategy_id}' for deterministic evaluation",
                metadata={"timeframe": self.timeframe},
            )

    def _evaluate_ema_trend_following(
        self,
        context: StrategyContext,
        candidate: StrategyCandidate,
    ) -> StrategyEvaluation:
        tf_features = context.analysis.features_by_granularity.get(self.timeframe)
        if tf_features is None or not tf_features.values:
            return StrategyEvaluation(
                strategy_id=candidate.strategy_id,
                eligible=False,
                score=0.0,
                regime_alignment=0.0,
                signal_alignment=0.0,
                data_quality=0.0,
                evaluation_reason=f"Missing feature data for timeframe '{self.timeframe}'",
                metadata={"timeframe": self.timeframe},
            )

        values = tf_features.values
        ema9 = values.get("ema_9")
        ema21 = values.get("ema_21")
        price = values.get("last_close")

        # Data quality evaluation (ema_9, ema_21, last_close required and numeric)
        valid_ema9 = isinstance(ema9, (int, float)) and not isinstance(ema9, bool)
        valid_ema21 = isinstance(ema21, (int, float)) and not isinstance(ema21, bool)
        valid_price = isinstance(price, (int, float)) and not isinstance(price, bool)

        if not (valid_ema9 and valid_ema21 and valid_price):
            return StrategyEvaluation(
                strategy_id=candidate.strategy_id,
                eligible=False,
                score=0.0,
                regime_alignment=0.0,
                signal_alignment=0.0,
                data_quality=0.0,
                evaluation_reason="Missing or non-numeric required EMA features (requires ema_9, ema_21, last_close)",
                metadata={"timeframe": self.timeframe},
            )

        data_quality = 1.0
        v_ema9 = float(ema9)
        v_ema21 = float(ema21)
        v_price = float(price)

        # Detect regime
        regime = Regime.UNKNOWN
        tf_regime = context.analysis.regimes_by_granularity.get(self.timeframe)
        if tf_regime is not None:
            regime = tf_regime.regime

        # 1. Regime Alignment
        if regime in (Regime.BULLISH, Regime.BEARISH):
            regime_alignment = 1.0
        elif regime == Regime.NEUTRAL:
            regime_alignment = 0.2
        else:
            regime_alignment = 0.5

        # 2. Signal Alignment based on Sprint 22 EMA rules
        # Long: EMA9 > EMA21 and Price > EMA9
        # Short: EMA9 < EMA21 and Price < EMA9
        is_bullish_setup = (v_ema9 > v_ema21) and (v_price > v_ema9)
        is_bearish_setup = (v_ema9 < v_ema21) and (v_price < v_ema9)

        if is_bullish_setup:
            if regime == Regime.BULLISH:
                signal_alignment = 1.0
                reason_setup = "Bullish trend setup fully aligned with BULLISH regime"
            elif regime == Regime.BEARISH:
                signal_alignment = 0.3
                reason_setup = "Bullish trend setup contradicts BEARISH regime"
            else:
                signal_alignment = 0.7
                reason_setup = "Bullish trend setup in non-trending regime"
        elif is_bearish_setup:
            if regime == Regime.BEARISH:
                signal_alignment = 1.0
                reason_setup = "Bearish trend setup fully aligned with BEARISH regime"
            elif regime == Regime.BULLISH:
                signal_alignment = 0.3
                reason_setup = "Bearish trend setup contradicts BULLISH regime"
            else:
                signal_alignment = 0.7
                reason_setup = "Bearish trend setup in non-trending regime"
        else:
            signal_alignment = 0.0
            reason_setup = "No active EMA trend setup (FLAT)"

        # Weighted score
        score = round(
            (regime_alignment * self.WEIGHT_REGIME)
            + (signal_alignment * self.WEIGHT_SIGNAL)
            + (data_quality * self.WEIGHT_DATA),
            4,
        )

        return StrategyEvaluation(
            strategy_id=candidate.strategy_id,
            eligible=True,
            score=score,
            regime_alignment=regime_alignment,
            signal_alignment=signal_alignment,
            data_quality=data_quality,
            evaluation_reason=f"EMA evaluation ({reason_setup}): regime={regime.value}, score={score}",
            metadata={
                "timeframe": self.timeframe,
                "regime": regime.value,
                "ema_9": v_ema9,
                "ema_21": v_ema21,
                "price": v_price,
            },
        )

    def _evaluate_rsi_mean_reversion(
        self,
        context: StrategyContext,
        candidate: StrategyCandidate,
    ) -> StrategyEvaluation:
        tf_features = context.analysis.features_by_granularity.get(self.timeframe)
        if tf_features is None or not tf_features.values:
            return StrategyEvaluation(
                strategy_id=candidate.strategy_id,
                eligible=False,
                score=0.0,
                regime_alignment=0.0,
                signal_alignment=0.0,
                data_quality=0.0,
                evaluation_reason=f"Missing feature data for timeframe '{self.timeframe}'",
                metadata={"timeframe": self.timeframe},
            )

        rsi = tf_features.values.get("rsi_14")

        # Data quality
        if not (isinstance(rsi, (int, float)) and not isinstance(rsi, bool)):
            return StrategyEvaluation(
                strategy_id=candidate.strategy_id,
                eligible=False,
                score=0.0,
                regime_alignment=0.0,
                signal_alignment=0.0,
                data_quality=0.0,
                evaluation_reason="Missing or non-numeric required feature 'rsi_14'",
                metadata={"timeframe": self.timeframe},
            )

        data_quality = 1.0
        v_rsi = float(rsi)

        # Detect regime
        regime = Regime.UNKNOWN
        tf_regime = context.analysis.regimes_by_granularity.get(self.timeframe)
        if tf_regime is not None:
            regime = tf_regime.regime

        # 1. Regime Alignment
        if regime == Regime.NEUTRAL:
            regime_alignment = 1.0
        elif regime in (Regime.BULLISH, Regime.BEARISH):
            regime_alignment = 0.3
        else:
            regime_alignment = 0.5

        # 2. Signal Alignment based on Sprint 22 RSI rules
        # Oversold: RSI < 30
        # Overbought: RSI > 70
        # Neutral: 30 <= RSI <= 70
        if v_rsi < 30.0:
            signal_alignment = 1.0
            reason_setup = f"RSI oversold setup ({v_rsi:.2f} < 30.0)"
        elif v_rsi > 70.0:
            signal_alignment = 1.0
            reason_setup = f"RSI overbought setup ({v_rsi:.2f} > 70.0)"
        else:
            signal_alignment = 0.0
            reason_setup = f"RSI in neutral range ({v_rsi:.2f})"

        # Weighted score
        score = round(
            (regime_alignment * self.WEIGHT_REGIME)
            + (signal_alignment * self.WEIGHT_SIGNAL)
            + (data_quality * self.WEIGHT_DATA),
            4,
        )

        return StrategyEvaluation(
            strategy_id=candidate.strategy_id,
            eligible=True,
            score=score,
            regime_alignment=regime_alignment,
            signal_alignment=signal_alignment,
            data_quality=data_quality,
            evaluation_reason=f"RSI evaluation ({reason_setup}): regime={regime.value}, score={score}",
            metadata={
                "timeframe": self.timeframe,
                "regime": regime.value,
                "rsi_14": v_rsi,
            },
        )
