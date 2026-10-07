from typing import Sequence

from ai.models import StrategyCandidate, StrategySelection
from ai.selector import StrategySelector
from regime.models import Regime
from strategy.context import StrategyContext
from strategy.registry import StrategyRegistry


class DeterministicStrategySelector(StrategySelector):
    """
    Deterministic reference strategy selector.

    Selection Policy based on market regime (defaulting to 1H timeframe):
      - BULLISH -> prefer 'ema_trend_following'
      - BEARISH -> prefer 'ema_trend_following'
      - NEUTRAL -> prefer 'rsi_mean_reversion'
      - UNKNOWN or other -> fall back to first eligible candidate

    Candidate availability:
      - Never selects a strategy not supplied in the candidates list.
      - If only one candidate is supplied and eligible, selects it.
      - Validates candidates against StrategyRegistry if provided.
    """

    selector_id: str = "deterministic_regime_selector"
    name: str = "Deterministic Regime Strategy Selector"
    description: str = "Selects trend or mean reversion strategy based on detected market regime"

    def __init__(
        self,
        selector_id: str | None = None,
        registry: StrategyRegistry | None = None,
        reference_timeframe: str = "1H",
    ):
        super().__init__(selector_id or self.selector_id)
        self.registry = registry
        self.reference_timeframe = reference_timeframe

    def select(
        self,
        context: StrategyContext,
        candidates: Sequence[StrategyCandidate],
    ) -> StrategySelection:
        if not isinstance(context, StrategyContext):
            raise ValueError("context must be an instance of StrategyContext")

        if not isinstance(candidates, (list, tuple)):
            raise ValueError("candidates must be a sequence of StrategyCandidate instances")

        if not candidates:
            raise ValueError("Cannot select from an empty candidates list")

        # Validate candidate elements and registry membership
        candidate_ids = []
        eligible_candidates = []

        for candidate in candidates:
            if not isinstance(candidate, StrategyCandidate):
                raise ValueError("All candidate elements must be StrategyCandidate instances")

            cid = candidate.strategy_id
            if self.registry is not None and not self.registry.has(cid):
                raise ValueError(f"Candidate strategy '{cid}' is not registered in StrategyRegistry")

            candidate_ids.append(cid)
            if candidate.eligible:
                eligible_candidates.append(candidate)

        if not eligible_candidates:
            raise ValueError("No eligible strategy candidates available for selection")

        # Map candidate IDs for deterministic lookup
        eligible_by_id = {c.strategy_id: c for c in eligible_candidates}

        # Determine reference market regime from context.analysis
        regime = Regime.UNKNOWN
        if context.analysis.success:
            tf_regime = context.analysis.regimes_by_granularity.get(self.reference_timeframe)
            if tf_regime is not None:
                regime = tf_regime.regime

        selected_id = None
        reason = ""
        confidence = 0.5  # Standard deterministic selection confidence

        has_ema = "ema_trend_following" in eligible_by_id
        has_rsi = "rsi_mean_reversion" in eligible_by_id

        if regime in (Regime.BULLISH, Regime.BEARISH):
            if has_ema:
                selected_id = "ema_trend_following"
                reason = f"Selected ema_trend_following because {self.reference_timeframe} regime is {regime.value.upper()}"
                confidence = 0.8
            elif has_rsi:
                selected_id = "rsi_mean_reversion"
                reason = f"Selected rsi_mean_reversion as fallback because {self.reference_timeframe} regime is {regime.value.upper()} but ema_trend_following is unavailable"
                confidence = 0.5
        elif regime == Regime.NEUTRAL:
            if has_rsi:
                selected_id = "rsi_mean_reversion"
                reason = f"Selected rsi_mean_reversion because {self.reference_timeframe} regime is NEUTRAL"
                confidence = 0.8
            elif has_ema:
                selected_id = "ema_trend_following"
                reason = f"Selected ema_trend_following as fallback because {self.reference_timeframe} regime is NEUTRAL but rsi_mean_reversion is unavailable"
                confidence = 0.5

        # Fallback if neither primary preference matched or regime is UNKNOWN
        if selected_id is None:
            # Deterministic tie-break: sort eligible IDs alphabetically for strict order independence
            sorted_eligible_ids = sorted(list(eligible_by_id.keys()))
            selected_id = sorted_eligible_ids[0]
            reason = f"Selected {selected_id} as default eligible strategy for regime {regime.value.upper()}"
            confidence = 0.5

        return StrategySelection(
            selected_strategy_id=selected_id,
            selection_reason=reason,
            selector_id=self.selector_id,
            confidence=confidence,
            candidate_ids=tuple(candidate_ids),
            metadata={
                "reference_timeframe": self.reference_timeframe,
                "detected_regime": regime.value,
                "eligible_candidate_count": len(eligible_candidates),
            },
        )
