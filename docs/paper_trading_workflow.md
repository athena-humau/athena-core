# ATHENA End-to-End Paper Trading Integration (Sprint 31)

## Overview

Sprint 31 integrates the complete decision, risk, and execution stack of ATHENA into a deterministic, testable **End-to-End Paper Trading Pipeline** (`PaperTradingWorkflow`).

```
Public REST API / Historical Candles
                │
                ▼
      MarketCache & Ranking
                │
                ▼
     MarketAnalysisPipeline
                │
                ▼
         MarketAnalysis
                │
                ▼
   PaperTradingWorkflow.process_market_analysis()
                │
       ┌────────┴───────────────────────────────┐
       ▼                                        ▼
StrategyContext & Candidates            Ineligible Analysis
       │                               (status: INELIGIBLE)
       ▼
DeterministicStrategyEvaluator & Ranker
       │
       ▼
DeterministicStrategySelector
       │
       ▼
Strategy.evaluate(context)
       │
       ├─────────────────────────────────┐
       ▼                                 ▼
   Signal (LONG / SHORT)            Signal (FLAT)
       │                       (status: NO_ACTIONABLE_SIGNAL)
       ▼
DeterministicSignalValidator
       │
       ├─────────────────────────────────┐
       ▼                                 ▼
ValidatedCandidate (PASS)      Validation Failure
       │                       (status: VALIDATION_REJECTED)
       ▼
DeterministicJudge
       │
       ├─────────────────────────────────┐
       ▼                                 ▼
JudgeDecision (PASS)             Judge Verdict REJECT
       │                         (status: JUDGE_REJECTED)
       ▼
DeterministicRiskEngine (TradeProposal)
       │
       ├─────────────────────────────────┐
       ▼                                 ▼
RiskAssessment (PASS)             Risk Verdict REJECT
       │                          (status: RISK_REJECTED)
       ▼
ExecutionRequest (Idempotency Key & Request ID)
       │
       ▼
PaperTradingEngine.execute()
       │
       ├──────────────────────┬──────────────────────┬──────────────────────┐
       ▼                      ▼                      ▼                      ▼
  FILLED                  DUPLICATE               CONFLICT               REJECTED
(status: PAPER_FILLED)   (status: DUPLICATE)    (status: CONFLICT)     (status: EXECUTION_REJECTED)
       │
       ▼
Position Opened in PaperPortfolio
       │
       ▼
Continuous Market Monitoring & Exit Lifecycle
   - Price Updates (update_market_price)
   - Candle Boundaries (process_candle)
   - Protective Triggers (Stop-Loss / Take-Profit)
   - Realized PnL & Margin Release
```

---

## Candidate Processing Lifecycle & Status Codes

Every market candidate analyzed by the workflow returns an immutable `CandidateWorkflowResult` capturing full audit evidence at every pipeline stage:

| Status Code | Description | Next Steps / Impact |
|:---|:---|:---|
| `INELIGIBLE` | Market analysis failed, symbol invalid, or data missing. | No trade proposed; portfolio untouched. |
| `SELECTION_FAILED` | Candidate evaluation or ranking returned no eligible strategy. | No signal generated; portfolio untouched. |
| `NO_ACTIONABLE_SIGNAL` | Selected strategy produced a `FLAT` neutral signal or failed to evaluate. | Non-actionable; no execution attempted. |
| `VALIDATION_REJECTED` | Signal failed contract validation rules (e.g., symbol mismatch, bounds). | Signal dropped; logged with audit failure reasons. |
| `JUDGE_REJECTED` | Multi-factor evidence check failed (e.g., regime conflict, excessive ATR). | Candidate rejected with explicit factor evidence. |
| `RISK_REJECTED` | Account, sizing, exposure, or stop-loss rules violated. | Risk rejected with policy check evidence. |
| `EXECUTION_REJECTED` | Paper execution validation failed (e.g., insufficient cash). | Order rejected; portfolio unchanged. |
| `PAPER_FILLED` | Simulated paper order accepted and filled. | Paper position opened; margin reserved; fees deducted. |
| `DUPLICATE` | Repeated execution request with identical idempotency key. | Existing paper order returned; no double margin or fee. |
| `CONFLICT` | Execution request with reused idempotency key but conflicting payload. | Rejected as an idempotency violation. |

---

## Protective Exits & Conservative Candle Policy

The paper trading workflow delegates market price and candle updates to `PaperTradingEngine`:

1. **Market Price Tick Evaluation (`update_market_price`)**:
   - Compares the latest market price against open positions.
   - For `LONG`: `price <= stop_loss_price` triggers `STOP_LOSS_TRIGGERED`; `price >= take_profit_price` triggers `TAKE_PROFIT_TRIGGERED`.
   - For `SHORT`: `price >= stop_loss_price` triggers `STOP_LOSS_TRIGGERED`; `price <= take_profit_price` triggers `TAKE_PROFIT_TRIGGERED`.

2. **Conservative Ambiguous-Candle Policy (`process_candle`)**:
   - If an incoming candle's high and low touch *both* the stop-loss and take-profit levels within the same bar, the conservative rule triggers **Stop-Loss first** (`STOP_LOSS_CONSERVATIVE_AMBIGUOUS_CANDLE`).
   - Ensures simulated performance never over-reports profits under ambiguous intraday market volatility.

---

## Safety & Simulation Boundaries

- **STRICTLY SIMULATION-ONLY**: All operations run completely in-memory.
- **Zero Network / API Key Dependencies**: Operates independently of private Bitget credentials or exchange endpoints.
- **Portfolio Isolation**: All positions, orders, trades, and portfolio snapshots carry explicit `is_paper=True` flags and distinct prefixes (`paper_ord_`, `paper_pos_`, `paper_trd_`).
- **Fail-Safe Partial Isolation**: Shortlist processing guarantees that errors or rejections on one symbol never corrupt subsequent symbol evaluations or alter account balances.
