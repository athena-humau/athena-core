from typing import Sequence

from ai.models import RankedStrategy, StrategyEvaluation
from ai.ranker import StrategyRanker


class DeterministicStrategyRanker(StrategyRanker):
    """
    Deterministic Strategy Ranker.

    Ranking Policy:
      1. Eligible candidates (eligible=True) rank before ineligible candidates (eligible=False).
      2. Within eligible candidates, sort by score descending (highest suitability score first).
      3. Tie-breaker: strategy_id ascending (alphabetical order).
      4. Within ineligible candidates, sort by score descending, tie-breaker strategy_id ascending.
      5. Ranks are 1-indexed (1, 2, 3, ...).
      6. Input sequence is never mutated.
      7. StrategyEvaluation objects are never mutated.
    """

    ranker_id: str = "deterministic_strategy_ranker"
    name: str = "Deterministic Strategy Ranker"
    description: str = "Ranks strategy evaluations by suitability score descending with deterministic tie-breaking"

    def rank(
        self,
        evaluations: Sequence[StrategyEvaluation],
    ) -> tuple[RankedStrategy, ...]:
        if not isinstance(evaluations, (list, tuple)):
            raise ValueError("evaluations must be a sequence of StrategyEvaluation instances")

        if not evaluations:
            return ()

        for ev in evaluations:
            if not isinstance(ev, StrategyEvaluation):
                raise ValueError("All elements of evaluations must be StrategyEvaluation instances")

        # Deterministic sorting key:
        # 1. Eligible first (0 for True, 1 for False)
        # 2. Score descending (-score)
        # 3. Strategy ID ascending (alphabetical tie-breaker)
        sorted_evals = sorted(
            evaluations,
            key=lambda ev: (0 if ev.eligible else 1, -ev.score, ev.strategy_id),
        )

        return tuple(
            RankedStrategy(rank=idx + 1, evaluation=ev)
            for idx, ev in enumerate(sorted_evals)
        )
