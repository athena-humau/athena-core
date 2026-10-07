from typing import Any, Sequence

from ai.deterministic_ranker import DeterministicStrategyRanker
from ai.evaluator import StrategyEvaluator
from ai.models import (
    RankedStrategy,
    StrategyCandidate,
    StrategyEvaluation,
    StrategySelection,
)
from ai.ranker import StrategyRanker
from ai.selector import StrategySelector
from regime.models import Regime
from strategy.context import StrategyContext
from strategy.registry import StrategyRegistry


class DeterministicStrategySelector(StrategySelector):
    """
    Deterministic Strategy Selector.

    Selection Modes:
      1. Evaluation-Ranked Selection (Integrated Mode):
         When an evaluator is provided (or when ranked candidates/evaluations are supplied),
         evaluates suitability scores, ranks deterministically (highest eligible score first,
         tie-breaker by strategy_id ascending), and selects the top-ranked eligible strategy.

      2. Regime-Based Selection (Sprint 23 Baseline Fallback Mode):
         When no evaluator is provided and raw candidates are supplied:
           - BULLISH -> prefer 'ema_trend_following'
           - BEARISH -> prefer 'ema_trend_following'
           - NEUTRAL -> prefer 'rsi_mean_reversion'
           - UNKNOWN -> fall back to first eligible candidate (alphabetically)

    Candidate Availability & Safety:
      - Never selects a strategy not supplied in the candidates list.
      - Never selects an ineligible strategy candidate.
      - Validates candidates against StrategyRegistry if provided.
      - Input order independence guaranteed.
    """

    selector_id: str = "deterministic_regime_selector"
    name: str = "Deterministic Strategy Selector"
    description: str = "Selects optimal strategy candidate deterministically using evaluation scores or regime policy"

    def __init__(
        self,
        selector_id: str | None = None,
        registry: StrategyRegistry | None = None,
        reference_timeframe: str = "1H",
        evaluator: StrategyEvaluator | None = None,
        ranker: StrategyRanker | None = None,
    ):
        super().__init__(selector_id or self.selector_id)
        self.registry = registry
        self.reference_timeframe = reference_timeframe
        self.evaluator = evaluator
        self.ranker = ranker

    def select_from_ranked(
        self,
        ranked_candidates: Sequence[RankedStrategy],
        context: StrategyContext | None = None,
    ) -> StrategySelection:
        """
        Select the top eligible candidate directly from a pre-ranked sequence.
        """
        if not isinstance(ranked_candidates, (list, tuple)):
            raise ValueError("ranked_candidates must be a sequence of RankedStrategy instances")

        if not ranked_candidates:
            raise ValueError("Cannot select from an empty candidates list")

        for r in ranked_candidates:
            if not isinstance(r, RankedStrategy):
                raise ValueError("All elements must be RankedStrategy instances")

            if self.registry is not None and not self.registry.has(r.strategy_id):
                raise ValueError(f"Candidate strategy '{r.strategy_id}' is not registered in StrategyRegistry")

        eligible_ranked = [r for r in ranked_candidates if r.eligible]
        if not eligible_ranked:
            raise ValueError("No eligible strategy candidates available for selection")

        # Top candidate by ranking (DeterministicStrategyRanker guarantees eligible first, score descending, tie-break strategy_id)
        top = eligible_ranked[0]
        selected_id = top.strategy_id
        confidence = float(top.score)
        reason = f"Selected {selected_id} with highest suitability score ({top.score:.4f}) at rank {top.rank}: {top.evaluation.evaluation_reason}"

        metadata = {
            "reference_timeframe": self.reference_timeframe,
            "selected_score": top.score,
            "ranking_position": top.rank,
            "evaluated_candidate_count": len(ranked_candidates),
            "eligible_candidate_count": len(eligible_ranked),
            "evaluation_reason": top.evaluation.evaluation_reason,
        }

        if context is not None and isinstance(context, StrategyContext) and context.analysis.success:
            tf_regime = context.analysis.regimes_by_granularity.get(self.reference_timeframe)
            if tf_regime is not None:
                metadata["detected_regime"] = tf_regime.regime.value

        return StrategySelection(
            selected_strategy_id=selected_id,
            selection_reason=reason,
            selector_id=self.selector_id,
            confidence=confidence,
            candidate_ids=tuple(r.strategy_id for r in ranked_candidates),
            metadata=metadata,
        )

    def select(
        self,
        context: StrategyContext,
        candidates: Sequence[Any],
    ) -> StrategySelection:
        if not isinstance(context, StrategyContext):
            raise ValueError("context must be an instance of StrategyContext")

        if not isinstance(candidates, (list, tuple)):
            raise ValueError("candidates must be a sequence of StrategyCandidate instances")

        if not candidates:
            raise ValueError("Cannot select from an empty candidates list")

        # 1. Direct RankedStrategy input
        if all(isinstance(c, RankedStrategy) for c in candidates):
            return self.select_from_ranked(candidates, context=context)

        # 2. Direct StrategyEvaluation input
        if all(isinstance(c, StrategyEvaluation) for c in candidates):
            ranker = self.ranker or DeterministicStrategyRanker()
            ranked = ranker.rank(candidates)
            return self.select_from_ranked(ranked, context=context)

        # 3. StrategyCandidate input
        if not all(isinstance(c, StrategyCandidate) for c in candidates):
            raise ValueError("All candidate elements must be StrategyCandidate, RankedStrategy, or StrategyEvaluation instances")

        # If evaluator is injected, execute full evaluation -> ranking -> selection pipeline
        if self.evaluator is not None:
            # Check candidate registration
            for candidate in candidates:
                if self.registry is not None and not self.registry.has(candidate.strategy_id):
                    raise ValueError(f"Candidate strategy '{candidate.strategy_id}' is not registered in StrategyRegistry")

            # Check eligibility
            if not any(candidate.eligible for candidate in candidates):
                raise ValueError("No eligible strategy candidates available for selection")

            evaluations = [self.evaluator.evaluate(context, c) for c in candidates]
            ranker = self.ranker or DeterministicStrategyRanker()
            ranked = ranker.rank(evaluations)
            return self.select_from_ranked(ranked, context=context)

        # Baseline Sprint 23 regime-based selection when no evaluator is injected
        candidate_ids = []
        eligible_candidates = []

        for candidate in candidates:
            cid = candidate.strategy_id
            if self.registry is not None and not self.registry.has(cid):
                raise ValueError(f"Candidate strategy '{cid}' is not registered in StrategyRegistry")

            candidate_ids.append(cid)
            if candidate.eligible:
                eligible_candidates.append(candidate)

        if not eligible_candidates:
            raise ValueError("No eligible strategy candidates available for selection")

        eligible_by_id = {c.strategy_id: c for c in eligible_candidates}

        regime = Regime.UNKNOWN
        if context.analysis.success:
            tf_regime = context.analysis.regimes_by_granularity.get(self.reference_timeframe)
            if tf_regime is not None:
                regime = tf_regime.regime

        selected_id = None
        reason = ""
        confidence = 0.5

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

        if selected_id is None:
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
