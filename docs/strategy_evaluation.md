# ATHENA Strategy Evaluation & Scoring Foundation (Sprint 24)

## Overview
Sprint 24 establishes the contract, scoring model, and deterministic reference implementation for ATHENA's Strategy Evaluation & Scoring layer (`ai/evaluator.py`, `ai/deterministic_evaluator.py`, and `ai/models.py`).

The evaluator assesses candidate trading strategies against current multi-timeframe market conditions (`StrategyContext`), producing structured, auditable evaluation metrics with an overall suitability score bounded in $[0.0, 1.0]$.

```
                      StrategyContext
            (TimeframeFeatures & Regimes)
                         │
                         ▼
               StrategyCandidate List
                         │
                         ▼
            DeterministicStrategyEvaluator
       (Implements StrategyEvaluator contract)
                         │
                         ▼
             Tuple[StrategyEvaluation, ...]
    ┌────────────────────┴────────────────────┐
    │                                         │
Regime Alignment (40%)               Signal Alignment (40%)
Data Quality (20%)                   Overall Score in [0.0, 1.0]
    │                                         │
    └────────────────────┬────────────────────┘
                         ▼
        (Future: Strategy Ranking & Selection)
```

## Contracts & Models (`ai/`)

### 1. `StrategyEvaluation` (`ai/models.py`)
An immutable dataclass (`frozen=True`) containing the evaluation result for a single candidate strategy:
- `strategy_id`: Strategy identifier matching a candidate strategy.
- `score`: Overall suitability score strictly bounded in $[0.0, 1.0]$.
- `regime_alignment`: Sub-metric in $[0.0, 1.0]$ measuring alignment with prevailing market regime (weight: 0.40).
- `signal_alignment`: Sub-metric in $[0.0, 1.0]$ measuring alignment with indicator conditions (weight: 0.40).
- `data_quality`: Sub-metric in $[0.0, 1.0]$ measuring data presence and indicator validity (weight: 0.20).
- `eligible`: Boolean flag indicating whether the strategy is eligible under current market conditions.
- `evaluator_id`: Identifier of the evaluator implementation (`deterministic_strategy_evaluator`).
- `reason`: Structured explanation detailing the scoring breakdown and rationale.
- `metadata`: Immutable `MappingProxyType` containing timeframe, regime, and feature snapshot.

### 2. `StrategyEvaluator` (`ai/evaluator.py`)
Abstract Base Class defining the evaluation contract:
```python
class StrategyEvaluator(ABC):
    @property
    @abstractmethod
    def evaluator_id(self) -> str:
        """Unique identifier for this evaluator implementation."""
        pass

    @abstractmethod
    def evaluate(
        self,
        context: StrategyContext,
        candidates: Sequence[StrategyCandidate],
    ) -> Sequence[StrategyEvaluation]:
        """Evaluate candidate strategies against market context."""
        pass
```

### 3. `DeterministicStrategyEvaluator` (`ai/deterministic_evaluator.py`)
Offline, deterministic reference implementation implementing the `StrategyEvaluator` interface.

#### Weighted Scoring Formula
$$\text{Score} = 0.40 \times \text{RegimeAlignment} + 0.40 \times \text{SignalAlignment} + 0.20 \times \text{DataQuality}$$

The weights sum to exactly $1.00$ ($0.40 + 0.40 + 0.20 = 1.00$).

#### Strategy Evaluation Rules

##### A. EMA Trend Following (`ema_trend_following`)
- **Regime Alignment (40%):**
  - Trending regimes (`Regime.BULLISH` or `Regime.BEARISH`): `1.0`
  - Ranging/Neutral regimes (`Regime.NEUTRAL`): `0.2`
  - Unknown/Other regimes (`Regime.UNKNOWN`): `0.5`
- **Signal Alignment (40%):**
  - Strong trend alignment (price & fast EMA aligned with trend, e.g. `close > ema_9 > ema_21` or `close < ema_9 < ema_21`): `1.0`
  - Mild trend alignment (`ema_9 > ema_21` or `ema_9 < ema_21`): `0.6`
  - Flat / No alignment (`ema_9 == ema_21`): `0.2`
- **Data Quality (20%):**
  - Full presence of `ema_9`, `ema_21`, and valid `last_close`: `1.0`
  - Missing required indicator/price: `0.0` (ineligible, score = `0.0`)

##### B. RSI Mean Reversion (`rsi_mean_reversion`)
- **Regime Alignment (40%):**
  - Neutral / Ranging regimes (`Regime.NEUTRAL`): `1.0`
  - Trending regimes (`Regime.BULLISH` or `Regime.BEARISH`): `0.3`
  - Unknown/Other regimes (`Regime.UNKNOWN`): `0.5`
- **Signal Alignment (40%):**
  - Extreme overbought ($RSI \ge 70$) or oversold ($RSI \le 30$): `1.0`
  - Approaching extremes ($30 < RSI \le 40$ or $60 \le RSI < 70$): `0.6`
  - Mid-range ($40 < RSI < 60$): `0.3`
- **Data Quality (20%):**
  - Valid `rsi_14` in range $[0.0, 100.0]$: `1.0`
  - Missing or out-of-range `rsi_14`: `0.0` (ineligible, score = `0.0`)

##### C. Generic / Unknown Strategies
- Evaluates candidate eligibility flag.
- Default regime alignment: `0.5`
- Default signal alignment: `0.5`
- Data quality: `1.0` if features present, else `0.0`
- Score calculated using standard 40/40/20 weights.

#### Safe Failure Handling
- If candidate has `eligible=False`, evaluation returns `eligible=False` and `score=0.0`.
- If required indicator values are missing or `None`, evaluation returns `eligible=False` and `score=0.0`.
- All outputs are validated against boundary constraints ($[0.0, 1.0]$).

## Architectural Boundaries & Disclaimers
1. **Suitability Metric Only:** The `score` reflects purely the structural suitability of a strategy for the detected market regime and indicator setup. It is **NOT** a prediction of profit, win probability, expected value, or financial return.
2. **Offline & Deterministic:** Operates purely offline on local feature memory and regimes. No LLM APIs, external networks, or non-deterministic components are involved.
3. **No Execution or Risk:** The evaluator produces evaluations and scores only. It does not place orders, size positions, manage risk, or execute trades.
4. **Zero Look-Ahead Bias:** Operates strictly on historical closed-candle features provided in the immutable `StrategyContext`.
