# ATHENA Technical Indicator Calculators (Sprint 21)

## Overview
Sprint 21 implements a quantitative foundation of standard technical indicator calculators inside `features/calculators/`:
- **Exponential Moving Average (EMA)** (`features/calculators/ema.py`)
- **Relative Strength Index (RSI)** (`features/calculators/rsi.py`)
- **Average True Range (ATR)** (`features/calculators/atr.py`)

All calculators implement the standard `FeatureCalculator` abstract interface from `features/calculators/base.py` and are designed to be injected into `FeatureEngine`.

```
Validated Chronological Candles
              │
              ▼
        FeatureEngine
  (Injection: Base + EMA + RSI + ATR)
              │
              ▼
   TimeframeFeatures (Immutable values map:
   {'last_close': ..., 'ema_14': ..., 'rsi_14': ..., 'atr_14': ...})
```

## Calculation Formulas & Conventions

### 1. Exponential Moving Average (EMA)
- **Period ($N$):** Configurable positive integer (default 14).
- **Minimum Candles:** $N$.
- **Multiplier:**
  $$\alpha = \frac{2}{N + 1}$$
- **Seed Value:** Arithmetic mean of the first $N$ close prices:
  $$\text{EMA}_0 = \frac{1}{N} \sum_{i=0}^{N-1} \text{Close}_i$$
- **Recursive Update:**
  $$\text{EMA}_t = (\text{Close}_t \times \alpha) + (\text{EMA}_{t-1} \times (1 - \alpha))$$
- **Feature Name:** `ema_<period>` (e.g. `ema_14`).

### 2. Relative Strength Index (RSI)
- **Period ($N$):** Configurable positive integer (default 14).
- **Minimum Candles:** $N + 1$ (to provide $N$ price change intervals).
- **Price Changes:**
  $$\Delta_t = \text{Close}_t - \text{Close}_{t-1}$$
  $$\text{Gain}_t = \max(\Delta_t, 0), \quad \text{Loss}_t = \max(-\Delta_t, 0)$$
- **Initial Averages (over first $N$ changes):**
  $$\text{AvgGain}_0 = \frac{1}{N} \sum_{i=1}^N \text{Gain}_i, \quad \text{AvgLoss}_0 = \frac{1}{N} \sum_{i=1}^N \text{Loss}_i$$
- **Wilder's Smoothing:**
  $$\text{AvgGain}_t = \frac{\text{AvgGain}_{t-1} \times (N - 1) + \text{Gain}_t}{N}$$
  $$\text{AvgLoss}_t = \frac{\text{AvgLoss}_{t-1} \times (N - 1) + \text{Loss}_t}{N}$$
- **Relative Strength & Index:**
  $$\text{RS} = \frac{\text{AvgGain}}{\text{AvgLoss}}, \quad \text{RSI} = 100 - \frac{100}{1 + \text{RS}}$$
- **Edge Cases:**
  - $\text{AvgLoss} = 0 \text{ and } \text{AvgGain} > 0 \implies \text{RSI} = 100.0$ (monotonically rising)
  - $\text{AvgGain} = 0 \text{ and } \text{AvgLoss} > 0 \implies \text{RSI} = 0.0$ (monotonically falling)
  - $\text{AvgLoss} = 0 \text{ and } \text{AvgGain} = 0 \implies \text{RSI} = 50.0$ (constant price)
- **Feature Name:** `rsi_<period>` (e.g. `rsi_14`).

### 3. Average True Range (ATR)
- **Period ($N$):** Configurable positive integer (default 14).
- **Minimum Candles:** $N$.
- **True Range ($\text{TR}$):**
  - For $t = 0$: $\text{TR}_0 = \text{High}_0 - \text{Low}_0$
  - For $t > 0$:
    $$\text{TR}_t = \max(\text{High}_t - \text{Low}_t, |\text{High}_t - \text{Close}_{t-1}|, |\text{Low}_t - \text{Close}_{t-1}|)$$
- **Initial ATR:**
  $$\text{ATR}_0 = \frac{1}{N} \sum_{i=0}^{N-1} \text{TR}_i$$
- **Wilder's Smoothing:**
  $$\text{ATR}_t = \frac{\text{ATR}_{t-1} \times (N - 1) + \text{TR}_t}{N}$$
- **Feature Name:** `atr_<period>` (e.g. `atr_14`).

## Architectural Guarantees & Boundaries
1. **No Trading Rules:** These calculators compute purely quantitative metric measurements. They contain no directional interpretations, buy/sell rules, or threshold logic.
2. **No Look-Ahead:** All calculations process strictly chronological candles from past to present.
3. **Multi-Timeframe Isolation:** Calculators operate independently within each granularity slice passed by `FeatureEngine`.
4. **Finite Values:** All outputs are strictly validated by `FeatureEngine` to ensure numerical finiteness (`math.isfinite`).
