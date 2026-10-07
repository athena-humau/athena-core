# ATHENA AI Strategy Selector Foundation (Sprint 23)

## Overview
Sprint 23 implements the contract and architecture foundation for ATHENA's AI Strategy Selector layer. The selector evaluates market context (`StrategyContext` containing multi-timeframe features and regimes) alongside a set of available `StrategyCandidate` records to emit a structured `StrategySelection`.

```
MarketAnalysis (TimeframeFeatures & Regimes)
                 │
                 ▼
          StrategyContext
                 │
                 ▼
       StrategyCandidate List
(ema_trend_following, rsi_mean_reversion)
                 │
                 ▼
   DeterministicStrategySelector
(Implements StrategySelector contract)
                 │
                 ▼
         StrategySelection
 (selected_strategy_id, reason, confidence)
                 │
                 ▼
    (Future: Judge AI → Risk Engine)
```

## Contracts & Models (`ai/`)

### 1. `StrategyCandidate` (`ai/models.py`)
An immutable dataclass (`frozen=True`) representing a strategy candidate submitted for selection:
- `strategy_id`: Unique string ID matching a registered strategy in `StrategyRegistry`.
- `strategy_name`: Human-readable name.
- `description`: Strategy description.
- `eligible`: Boolean flag indicating whether the strategy is eligible for consideration.
- `metadata`: Immutable `MappingProxyType` dictionary containing candidate attributes.

### 2. `StrategySelection` (`ai/models.py`)
An immutable dataclass (`frozen=True`) containing the outcome of the selection process:
- `selected_strategy_id`: The chosen strategy identifier.
- `selection_reason`: Detailed explanation of why the strategy was chosen.
- `selector_id`: Identifier of the selector implementation.
- `confidence`: Bounded float $[0.0, 1.0]$ representing selector confidence.
- `candidate_ids`: Tuple of strings listing all evaluated candidate IDs.
- `metadata`: Contextual metadata including detected regime and timeframe.

### 3. `StrategySelector` (`ai/selector.py`)
Abstract Base Class for all selectors:
```python
class StrategySelector(ABC):
    @abstractmethod
    def select(
        self,
        context: StrategyContext,
        candidates: Sequence[StrategyCandidate],
    ) -> StrategySelection:
        pass
```

### 4. `DeterministicStrategySelector` (`ai/deterministic_selector.py`)
Offline, deterministic reference implementation validating the contract.
- **Selection Policy:**
  - `Regime.BULLISH` $\to$ prefers `ema_trend_following` (confidence 0.8)
  - `Regime.BEARISH` $\to$ prefers `ema_trend_following` (confidence 0.8)
  - `Regime.NEUTRAL` $\to$ prefers `rsi_mean_reversion` (confidence 0.8)
  - Fallback / Unknown $\to$ Alphabetical tie-break among eligible candidates (confidence 0.5)
- **Candidate Restriction:** Never selects a strategy not present in `candidates`.
- **Registry Integration:** Optionally validates candidates against `StrategyRegistry`.
- **Order Independence:** The candidate list order does not affect selection.

## Future AI Boundary
Future LLM or machine learning selectors will inherit directly from `StrategySelector`:
```
            StrategySelector (ABC)
                     ▲
         ┌───────────┴───────────┐
         │                       │
DeterministicStrategySelector   FutureAISelector (Future Sprint)
```
The consuming pipeline interacts strictly with the `StrategySelector` interface, allowing hot-swapping between deterministic rule-based selection and AI evaluation without modifying upstream or downstream layers.

## Architectural Boundaries & Disclaimers
1. **Offline & Deterministic:** No LLMs, OpenAI API, neural networks, or external network calls are used.
2. **Selector Confidence $\ne$ Profitability:** Selection confidence measures the degree of match between the detected market regime and candidate strategy design, NOT probability of profit.
3. **No Execution or Risk:** The selector only identifies the strategy to employ. It does not evaluate strategy signals, size positions, or place orders.
