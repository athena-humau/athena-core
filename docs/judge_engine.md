# ATHENA Judge Engine Foundation & Multi-Factor Decision Contract (Sprint 27)

## 1. Purpose of the Judge Engine
The Judge Engine serves as ATHENA's multi-factor decision gateway between signal validation and the downstream risk/execution layers. It evaluates a `ValidatedCandidate` signal against available market analysis context (regime, volatility, confidence, technical confluence) to decide whether the candidate satisfies the configured criteria for progression.

```
                    MarketAnalysis
                          │
                          ▼
                   StrategyContext
                          │
                          ▼
                 Strategy Candidates
                          │
                          ▼
                  StrategyEvaluator
                          │
                          ▼
                 StrategyEvaluation[]
                          │
                          ▼
                   StrategyRanker
                          │
                          ▼
                  RankedStrategy[]
                          │
                          ▼
                  StrategySelector
                          │
                          ▼
                  StrategySelection
                          │
                          ▼
                  Selected Strategy
                          │
                          ▼
                   Strategy Signal
                          │
                          ▼
                ★ SIGNAL VALIDATION ★
                          │
                          ▼
                  ValidatedCandidate
                          │
                          ▼
               ★ JUDGE ENGINE (S27) ★
                          │
                          ▼
                Immutable JudgeDecision
                          │
                  🛑 STOP (S27 Scope)
                          │
                 [ Future Risk Engine ]
                          │
                [ Future Execution Engine ]
```

---

## 2. Architectural Boundaries
$$\text{Signal Generation} \neq \text{Signal Validation} \neq \text{Judge Decision} \neq \text{Risk Assessment} \neq \text{Execution Authorization}$$

- **Signal Generation (`strategy/`):** Formulates directional trading intent based on strategy-specific rules.
- **Signal Validation (`ai/signal_validator.py`):** Certifies structural, contextual, and selection integrity.
- **Judge Decision (`ai/judge.py`):** Evaluates multi-factor market evidence to decide `PASS` or `REJECT`.
- **Risk Assessment (Future):** Determines position size, capital allocation, leverage, and stop-loss/take-profit boundaries.
- **Execution Authorization (Future):** Transmits order payloads to exchange endpoints.

> [!IMPORTANT]
> A `PASS` verdict from the Judge confirms only that the candidate satisfies configured evidence criteria. It does **not** authorize execution, size positions, or guarantee profitability.

---

## 3. Input & Output Contracts

### Input Contract
`JudgeEngine.judge(candidate, context=None)`:
- `candidate`: Must be an instance of `ValidatedCandidate` with `status == ValidationStatus.PASSED`.
- `context`: Optional `StrategyContext` or `MarketAnalysis` containing multi-timeframe features and regimes.

### Output Contract: `JudgeDecision` (`ai/models.py`)
An immutable dataclass (`frozen=True`) containing:
- `verdict`: `JudgeVerdict.PASS` or `JudgeVerdict.REJECT`.
- `symbol`: Target market symbol (e.g. `"BTCUSDT"`).
- `strategy_id`: Strategy identifier (e.g. `"ema_trend_following"`).
- `direction`: Direction of intent (`SignalDirection.LONG`, `SHORT`, or `FLAT`).
- `confidence`: Candidate confidence float in $[0.0, 1.0]$.
- `score`: Overall multi-factor evidence suitability score in $[0.0, 1.0]$.
- `reason_codes`: Tuple of stable uppercase strings (e.g. `("CRITERIA_SATISFIED",)` or `("REGIME_CONTRADICTION",)`).
- `explanation`: Detailed human-readable justification.
- `factor_evidence`: Immutable mapping of factor names to `FactorEvidence` objects.
- `candidate`: Original `ValidatedCandidate` instance.
- `policy_id`: Identifier of the decision policy (e.g. `"deterministic_v1"`).
- `decision_timestamp`: UTC datetime.
- `metadata`: Immutable mapping of audit metadata.

---

## 4. Implemented Factors & Evidence Sources

| Factor Name | Source Evidence | Weight | Interpretation & Criteria |
| :--- | :--- | :--- | :--- |
| **`confidence_threshold`** | `ValidatedCandidate.confidence` | 30% | Candidate confidence must meet or exceed `min_confidence` (default: 0.50). |
| **`regime_alignment`** | `MarketAnalysis.regimes_by_granularity` | 30% | Direction evaluated against prevailing regime (`BULLISH`, `BEARISH`, `NEUTRAL`) with strategy-specific awareness. |
| **`volatility_context`** | `TimeframeFeatures.values['atr_14']` & `last_close` | 20% | Normalized relative ATR (`atr / last_close`) must remain within `[min_relative_atr, max_relative_atr]` (default: 0.01% - 8%). |
| **`feature_confluence`** | `TimeframeFeatures.values` (`rsi_14`, `ema_9`, `ema_21`) | 20% | Detects severe indicator contradictions (e.g. LONG into RSI $\ge 80$, SHORT into RSI $\le 20$, or opposing EMA crosses). |

---

## 5. Strategy-Specific Regime Handling
- **Trend-Following (`ema_trend_following`):**
  - `LONG` in `BULLISH`: Aligned (`PASS`, factor score 1.0).
  - `SHORT` in `BEARISH`: Aligned (`PASS`, factor score 1.0).
  - `LONG` in `BEARISH` / `SHORT` in `BULLISH`: Direct contradiction (`REJECT`, factor score 0.0, reason: `REGIME_CONTRADICTION`).
  - `NEUTRAL` Regime: Ranging conditions lack trend momentum; rejected when `require_regime_alignment=True`.
- **Mean-Reversion (`rsi_mean_reversion`):**
  - `NEUTRAL` Regime: Mean reversion thrives in ranging markets (`PASS`, factor score 1.0).
  - Trending Regimes (`BULLISH` / `BEARISH`): Dip-buying in bull market or rally-selling in bear market is aligned; counter-trend extremes carry caution (score 0.5 - 0.9).
- **Unknown / Unavailable Regime:**
  - Factor recorded as `"UNAVAILABLE"`, neutral score 0.5 assigned without guessing.

---

## 6. Volatility & Confluence Handling
- **Relative ATR Normalization:**
  $$\text{Relative ATR} = \frac{\text{ATR}_{14}}{\text{Last Close}}$$
  Using unitless percentage thresholds avoids cross-symbol price magnitude distortions.
- **Missing Volatility:** If ATR is absent from feature memory, the factor is recorded as `"UNAVAILABLE"` without synthesizing fake volatility data.
- **Confluence Contradiction:** Detects momentum exhaustion:
  - `LONG` signal when $RSI_{14} \ge 80.0$ $\to$ `CONFLUENCE_CONTRADICTION`.
  - `SHORT` signal when $RSI_{14} \le 20.0$ $\to$ `CONFLUENCE_CONTRADICTION`.

---

## 7. Decision Rules & Stable Reason Codes

### Decision Policy
1. Rejects immediately if candidate validation failed or input is malformed (`CANDIDATE_VALIDATION_FAILED`, `INVALID_CANDIDATE`).
2. Rejects immediately if signal is `FLAT` (`FLAT_SIGNAL_NOT_ACTIONABLE`).
3. Rejects immediately on context or symbol mismatch (`SYMBOL_MISMATCH`, `INVALID_CONTEXT`).
4. Rejects if any fatal factor flag is raised (`LOW_CONFIDENCE`, `REGIME_CONTRADICTION`, `EXCESSIVE_VOLATILITY`, `INSUFFICIENT_VOLATILITY`, `CONFLUENCE_CONTRADICTION`).
5. Computes overall weighted evidence suitability score:
   $$\text{Score} = 0.30 \times \text{Conf} + 0.30 \times \text{Regime} + 0.20 \times \text{Vol} + 0.20 \times \text{Confluence}$$
6. Passes candidate if all critical factors pass and $\text{Score} \ge \text{threshold}$ (default: 0.60); otherwise rejects with `INSUFFICIENT_EVIDENCE_SCORE`.

### Reason Codes
- `CRITERIA_SATISFIED`: Multi-factor evidence meets or exceeds configured suitability standards.
- `CANDIDATE_VALIDATION_FAILED`: Upstream signal validator failed.
- `INVALID_CANDIDATE`: Candidate object missing or wrong type.
- `FLAT_SIGNAL_NOT_ACTIONABLE`: FLAT direction indicates market neutrality (non-trading).
- `SYMBOL_MISMATCH`: Candidate symbol does not match market analysis symbol.
- `LOW_CONFIDENCE`: Candidate confidence below minimum requirement.
- `REGIME_CONTRADICTION`: Prevailing regime directly conflicts with directional intent.
- `EXCESSIVE_VOLATILITY`: Market volatility exceeds safe operational boundary.
- `INSUFFICIENT_VOLATILITY`: Market is inactive or relative volatility is near zero.
- `CONFLUENCE_CONTRADICTION`: Extreme technical indicators contradict the trade setup.
- `MARKET_ANALYSIS_FAILED`: Upstream market pipeline reported failure.
- `INSUFFICIENT_EVIDENCE_SCORE`: Evidence score fell below threshold.

---

## 8. FLAT Semantics
- `SignalDirection.FLAT` represents an active, valid determination by a strategy to remain neutral.
- The Judge returns `REJECT` with `reason_codes = ("FLAT_SIGNAL_NOT_ACTIONABLE",)`.
- It does **not** convert `FLAT` to `LONG` or `SHORT`, nor does it misrepresent `FLAT` as a malformed signal.

---

## 9. Immutability, Determinism & Safety
- **Frozen Models:** `JudgeDecision` and `FactorEvidence` are immutable.
- **Safety Invariant:** A failed or missing candidate can **never** produce a passing decision by construction (`__post_init__` enforces this).
- **Pure Determinism:** Same candidate and context produce identical decisions, scores, and reason codes across unlimited repeated calls.
- **Offline Isolation:** Zero network calls, zero exchange interactions, zero order placement, and zero risk engine capabilities.

---

## 10. Known Limitations & Deferred Capabilities
- **No Order Sizing or Leverage:** The Judge does not compute lot sizes, leverage, or margin requirements (reserved for Risk Engine).
- **No Stop-Loss or Take-Profit:** Stop-loss distances are not determined here.
- **No External LLM / AI:** Implemented as a deterministic rule-and-evidence engine; probabilistic/LLM judges remain separate future plugins.
- **No Cross-Exchange Arbitrage:** Evaluates individual symbol analysis only.
