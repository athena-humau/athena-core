# ATHENA Pipeline Integration (Sprint 19)

## Overview
Sprint 19 connects the analytical components of ATHENA into a continuous deterministic data pipeline:

```
Bitget Public REST API (/api/v2/mix/market/tickers)
            │
            ▼
    MarketData & BitgetParser
            │
            ▼
       MarketCache
            │
            ▼
   MarketEligibilityFilter (Statistical Median Volume & P75 Spread Cutoffs)
            │
            ▼
      RankingEngine (5-Factor Percentile Scoring)
            │
            ▼ Ranked Shortlist
   MarketAnalysisPipeline (market/pipeline.py)
            │
    ┌───────┴────────────────────────┬─────────────────────────┐
    ▼                                ▼                         ▼
MultiTimeframeManager          FeatureEngine          MarketRegimeEngine
(1m, 5m, 15m, 1H, 4H)     (Immutable Features)     (Directional Regimes)
    │                                │                         │
    └───────────────────────┬────────┴─────────────────────────┘
                            ▼
               Structured MarketAnalysis Output
```

## Components

### `MarketAnalysisPipeline` (`market/pipeline.py`)
- Coordinates multi-timeframe historical candle retrieval, feature calculation, and market regime classification.
- Handles partial symbol failure safely: if historical retrieval or feature computation fails for one symbol, the pipeline flags that symbol's analysis with `success=False` and an explicit error message, allowing independent symbols to proceed without state corruption.
- Automatically computes aligned timeframe query windows on 4-hour boundaries (`_calculate_aligned_window`).

### `MarketAnalysis` (`market/analysis_models.py`)
Frozen dataclass encapsulating:
- `symbol`: Market trading pair (e.g. `BTCUSDT`).
- `ranking`: Original `RankedMarket` record containing factor percentile scores.
- `candles_by_granularity`: Mapping of granularity string (`1m`, `5m`, `15m`, `1H`, `4H`) to `list[Candle]`.
- `features_by_granularity`: Mapping of granularity to `TimeframeFeatures`.
- `regimes_by_granularity`: Mapping of granularity to `TimeframeRegime`.
- `success`: Boolean indicating pipeline success.
- `error`: Error message string if processing failed.
- `analyzed_at`: UTC timestamp of the analysis run.

## Failure Modes & Safeguards
1. **Network / Historical Fetch Failures:** Propagated as failed symbol status; no fake candles or mock regimes are created.
2. **Insufficient Candle History:** `FeatureEngine` validation errors are caught and recorded per symbol without aborting other shortlist pairs.
3. **Immutability:** Dataclass mappings are wrapped in `MappingProxyType` to prevent accidental in-place mutation by downstream decision layers.
