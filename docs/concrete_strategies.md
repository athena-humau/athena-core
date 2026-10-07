# ATHENA Concrete Strategy Implementations (Sprint 22)

## Overview
Sprint 22 implements the first two deterministic concrete trading strategies in ATHENA:
1. **EMA Trend Following Strategy** (`strategy/ema_trend_following.py`)
2. **RSI Mean Reversion Strategy** (`strategy/rsi_mean_reversion.py`)

Both strategies strictly consume the existing `MarketAnalysis` / `StrategyContext` domain abstractions and emit immutable `Signal` objects registered in `StrategyRegistry`.

```
Validated OHLCV Candles
           │
           ▼
     FeatureEngine (Calculators: EMA9, EMA21, RSI14)
           │
           ▼
     MarketAnalysis (TimeframeFeatures)
           │
           ▼
     StrategyContext
     ┌─────┴────────────────────────┐
     ▼                              ▼
EMATrendFollowingStrategy    RSIMeanReversionStrategy
     │                              │
     ▼                              ▼
   Signal (LONG/SHORT/FLAT)       Signal (LONG/SHORT/FLAT)
     │                              │
     └──────────────┬───────────────┘
                    ▼
            StrategyRegistry
```

## Strategy 1: EMA Trend Following (`ema_trend_following`)

### Concept & Parameters
- **Strategy ID:** `ema_trend_following`
- **Default Timeframe:** `1H`
- **Fast EMA Period:** `9` (`ema_9`)
- **Slow EMA Period:** `21` (`ema_21`)
- **Reference Price:** `last_close`

### Explicit Decision Rules
1. **LONG Condition:**
   $$\text{EMA}(9) > \text{EMA}(21) \quad \text{AND} \quad \text{Price} > \text{EMA}(9)$$
   - Emits `SignalDirection.LONG`.
   - Reason: `LONG because ema_9 > ema_21 and price > ema_9`.
2. **SHORT Condition:**
   $$\text{EMA}(9) < \text{EMA}(21) \quad \text{AND} \quad \text{Price} < \text{EMA}(9)$$
   - Emits `SignalDirection.SHORT`.
   - Reason: `SHORT because ema_9 < ema_21 and price < ema_9`.
3. **FLAT Condition:**
   - $\text{EMA}(9) == \text{EMA}(21)$ (no trend slope).
   - $\text{EMA}(9) > \text{EMA}(21)$ but $\text{Price} \le \text{EMA}(9)$ (price pulled back/conflicted).
   - $\text{EMA}(9) < \text{EMA}(21)$ but $\text{Price} \ge \text{EMA}(9)$ (price counter-trend).
   - Missing or non-numeric feature data (fails safely to `FLAT`).

### Deterministic Normalized Confidence
Confidence is derived purely from relative percentage separation between price, fast EMA, and slow EMA:
$$\text{Separation} = \frac{|\text{Price} - \text{EMA}_9|}{\text{EMA}_9} + \frac{|\text{EMA}_9 - \text{EMA}_{21}|}{\text{EMA}_{21}}$$
$$\text{Confidence} = \min(\max(\text{Separation} \times 10.0, 0.1), 1.0)$$
*Note: Strategy confidence measures indicator alignment strength, NOT statistical probability of profit.*

---

## Strategy 2: RSI Mean Reversion (`rsi_mean_reversion`)

### Concept & Parameters
- **Strategy ID:** `rsi_mean_reversion`
- **Default Timeframe:** `1H`
- **RSI Period:** `14` (`rsi_14`)
- **Oversold Threshold:** `30.0`
- **Overbought Threshold:** `70.0`

### Explicit Decision Rules
1. **Oversold $\to$ LONG Condition:**
   $$\text{RSI}(14) < 30.0$$
   - Emits `SignalDirection.LONG`.
   - Reason: `LONG because rsi_14 < 30.0`.
2. **Overbought $\to$ SHORT Condition:**
   $$\text{RSI}(14) > 70.0$$
   - Emits `SignalDirection.SHORT`.
   - Reason: `SHORT because rsi_14 > 70.0`.
3. **Neutral $\to$ FLAT Condition:**
   $$30.0 \le \text{RSI}(14) \le 70.0$$
   - Emits `SignalDirection.FLAT`.
   - Reason: `FLAT because rsi_14 is between 30.0 and 70.0`.
   - Missing or non-numeric feature data fails safely to `FLAT`.

### Deterministic Normalized Confidence
Confidence is derived from distance beyond the respective trigger threshold:
- Oversold: $\text{Confidence} = 0.1 + \left(\frac{30.0 - \text{RSI}}{30.0}\right) \times 0.9$ (bounded in $[0.1, 1.0]$)
- Overbought: $\text{Confidence} = 0.1 + \left(\frac{\text{RSI} - 70.0}{30.0}\right) \times 0.9$ (bounded in $[0.1, 1.0]$)
- Neutral: `0.0`.

---

## Strategy Registry Integration
Both strategies register with `StrategyRegistry`:
```python
registry = StrategyRegistry()
registry.register(EMATrendFollowingStrategy())
registry.register(RSIMeanReversionStrategy())

# Lookup
ema_strat = registry.get("ema_trend_following")
rsi_strat = registry.get("rsi_mean_reversion")
```

---

## Architectural Disclaimers & Non-Goals
- **Reference Strategies Only:** These are deterministic algorithmic reference strategies designed to validate pipeline data flow and signal generation contracts.
- **No Profitability Claims:** These strategies do not guarantee and make no claim to profitability.
- **No Order Execution:** Neither strategy places orders, manages positions, or contacts trading APIs.
- **No Risk Allocation:** Stop-loss, take-profit, and position sizing belong to the future Risk Engine.
- **No AI / ML:** Evaluation is 100% deterministic and pure given input context.
