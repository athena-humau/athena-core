# ATHENA Strategy Ranking & Selection Integration (Sprint 25)

## Overview
Sprint 25 integrates ATHENA's strategy analysis pipeline from `MarketAnalysis` down to `StrategySelection`:
- Evaluates available `StrategyCandidate` instances against market conditions (`StrategyContext`) using `StrategyEvaluator`.
- Ranks candidate evaluations deterministically using `StrategyRanker` based on suitability scores and eligibility.
- Selects the top eligible candidate using `StrategySelector` with transparent, auditable evidence.

```
                      MarketAnalysis
                            │
                            ▼
                     StrategyContext
              (TimeframeFeatures & Regimes)
                            │
                            ▼
                   Strategy Candidates
                            │
                            ▼
                    StrategyEvaluator
             (DeterministicStrategyEvaluator)
                            │
                            ▼
                   StrategyEvaluation[]
                            │
                            ▼
                     StrategyRanker
              (DeterministicStrategyRanker)
                            │
                            ▼
                   Ranked Evaluations
                  (RankedStrategy[])
                            │
                            ▼
                     StrategySelector
             (DeterministicStrategySelector)
                            │
                            ▼
                    StrategySelection
              (selected_strategy_id, reason)
                            │
                            ▼
           Existing Strategy Signal Verification
              (evaluate -> Signal LONG/SHORT/FLAT)
                            │
                  🛑 STOP (Pipeline End)
```

---

## Architecture & Components (`ai/`)

### 1. `RankedStrategy` (`ai/models.py`)
An immutable dataclass (`frozen=True`) representing a candidate strategy positioned in rank order:
- `rank`: 1-indexed rank position (`int`, $\ge 1$).
- `evaluation`: Complete, immutable `StrategyEvaluation` object preserving all evidence.
- Convenience properties:
  - `strategy_id`: Strategy identifier (`str`).
  - `score`: Overall suitability score (`float` in $[0.0, 1.0]$).
  - `eligible`: Boolean eligibility status (`bool`).

### 2. `StrategyRanker` (`ai/ranker.py`)
Abstract Base Class for strategy ranking:
```python
class StrategyRanker(ABC):
    @property
    @abstractmethod
    def ranker_id(self) -> str:
        """Unique identifier for this ranker implementation."""
        pass

    @abstractmethod
    def rank(
        self,
        evaluations: Sequence[StrategyEvaluation],
    ) -> Sequence[RankedStrategy]:
        """Rank evaluations deterministically without mutating inputs."""
        pass
```

### 3. `DeterministicStrategyRanker` (`ai/deterministic_ranker.py`)
Deterministic reference ranker sorting evaluations:
- **Eligible First:** Candidates with `eligible=True` strictly precede `eligible=False`, regardless of numerical score.
- **Descending Score:** Within eligible (and ineligible) subsets, candidates are sorted by `score` descending (highest score first).
- **Deterministic Tie-Breaking:** When scores are identical, tie-breaking sorts by `strategy_id` ascending (alphabetical order).
- **Input Order Independence:** Any permutation of candidate evaluations yields identical ranking order.
- **Input Immutability:** Input collections are never modified in-place; a new tuple of `RankedStrategy` is returned.

### 4. `DeterministicStrategySelector` (`ai/deterministic_selector.py`)
Integrated selector supporting multiple deterministic modes:
- **Evaluation-Ranked Selection (Integrated Mode):**
  When injected with `evaluator` and `ranker`, or when passed pre-ranked evaluations, selects the top eligible candidate based on suitability score. Confidence reflects the deterministic suitability score.
- **Regime-Based Selection (Sprint 23 Baseline Fallback Mode):**
  Preserves 100% backward compatibility with Sprint 23 when no evaluator is injected:
  - `BULLISH` / `BEARISH` $\to$ prefers `ema_trend_following`
  - `NEUTRAL` $\to$ prefers `rsi_mean_reversion`
  - Fallback / `UNKNOWN` $\to$ alphabetical tie-break among eligible candidates
- **Direct Ranked Selection:**
  Exposes `select_from_ranked(ranked_candidates, context)` for direct selection from pre-computed rankings.

---

## End-to-End Analytical Flow
1. **Market Analysis:** `MarketAnalysis` provides multi-timeframe candles, features (EMA 9/21, RSI 14), and classified market regimes.
2. **Context Creation:** Packaged into an immutable `StrategyContext`.
3. **Candidate Generation:** Candidates discovered from `StrategyRegistry` or passed explicitly.
4. **Evaluation:** `DeterministicStrategyEvaluator` calculates suitability score based on:
   - Regime Alignment (40%)
   - Signal Alignment (40%)
   - Data Quality (20%)
5. **Ranking:** `DeterministicStrategyRanker` sorts evaluations into 1-indexed `RankedStrategy` records.
6. **Selection:** `DeterministicStrategySelector` selects the top eligible ranked candidate.
7. **Signal Verification:** The chosen strategy is resolved from `StrategyRegistry` and evaluated against `StrategyContext` to emit a deterministic `Signal`.

---

## Score Semantics & Boundary Disclaimers

### Suitability Metric Only
- The `score` reflects solely how well a candidate's design matches current market regime and indicator setups.
- It is strictly **NOT** a prediction of profit, win probability, expected value, or financial return.
- Confidence values reflect deterministic score alignment, not statistical certainty.

### Absolute Scope Boundaries
The strategy ranking and selection layer is strictly an analysis-only phase:
- **NO** AI or Large Language Models (LLMs)
- **NO** external network or OpenAI API calls
- **NO** Machine Learning or heuristic optimization
- **NO** Judge AI
- **NO** Risk Engine, leverage, or position sizing
- **NO** Execution Engine or order placement
- **NO** Paper Trading or Live Trading
- **NO** Bitget private API access
- **NO** Look-ahead bias or future candle inspection
