# ATHENA Strategy Library Foundation (Sprint 20)

## Overview
Sprint 20 establishes the contract and interface foundation of the ATHENA Strategy Library. The layer consumes structured `MarketAnalysis` records produced by the upstream pipeline and emits structured, immutable `Signal` objects without placing orders, performing risk sizing, or communicating with external exchanges.

```
Upstream Pipeline:
Market Scan → Eligibility Filter → Ranking → MTF Candles → Features → Regimes
                                  │
                                  ▼
                            MarketAnalysis
                                  │
                                  ▼
                           StrategyContext
                                  │
                                  ▼
                      BaseStrategy.evaluate()
                                  │
                                  ▼
                               Signal
                                  │
                                  ▼
                 (Future: AI Selector → Judge AI → Risk Engine)
```

## Contracts & Models

### `SignalDirection` (`strategy/models.py`)
Enum defining possible signal directions:
- `LONG`: Bullish signal opinion.
- `SHORT`: Bearish signal opinion.
- `FLAT`: Neutral or no-action opinion.

### `Signal` (`strategy/models.py`)
An immutable dataclass (`frozen=True`) representing a strategy opinion:
- `symbol`: Market trading pair (e.g. `BTCUSDT`).
- `direction`: `SignalDirection` enum value.
- `strategy_id`: Unique identifier of the emitting strategy.
- `timestamp`: UTC datetime of signal generation.
- `confidence`: Bounded float `[0.0, 1.0]` representing conviction.
- `reason`: Descriptive explanation for auditing and diagnostics.
- `metadata`: Immutable `MappingProxyType` dictionary containing supporting indicator/regime values.

### `StrategyContext` (`strategy/context.py`)
An immutable dataclass (`frozen=True`) providing domain context to strategies:
- `analysis`: `MarketAnalysis` instance containing candles, features, and regimes across granularities.
- `parameters`: Immutable parameter mapping for strategy configuration.
- `timestamp`: Evaluation timestamp.

### `BaseStrategy` (`strategy/base.py`)
Abstract Base Class for all strategies:
- `strategy_id`: Unique non-empty string identifier.
- `name`: Human-readable strategy name.
- `description`: Strategy description.
- `evaluate(context: StrategyContext) -> Signal`: Core abstract evaluation method.

### `StrategyRegistry` (`strategy/registry.py`)
Registry providing thread-safe strategy storage, lookup, duplicate prevention, and enumeration:
- `register(strategy: BaseStrategy)`: Registers a strategy instance.
- `get(strategy_id: str) -> BaseStrategy`: Retrieves strategy by ID.
- `has(strategy_id: str) -> bool`: Checks existence.
- `list_strategies() -> list[str]`: Enumerates registered strategy IDs.
- `unregister(strategy_id: str)`: Removes strategy from registry.
- `clear()`: Empties the registry.

## Architectural Boundaries
1. **No Order Execution:** Strategies never execute trades, place orders, or interface with exchange private APIs.
2. **No Risk Management:** Position sizing, leverage, stop-loss calculations, and capital allocation remain strictly in the upcoming Risk Engine.
3. **No Profitable Trading Claims:** Signal generation is a deterministic analytical contract; no profitability assumptions are made.
4. **Independent from AI:** Strategy classes are completely decoupled from AI and LLMs. The future AI Strategy Selector and Judge AI consume strategy signals rather than the reverse.
