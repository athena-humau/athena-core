# ATHENA Paper Trading Engine Foundation & Trade Lifecycle (Sprint 30)

## 1. Purpose & Architectural Overview
The Paper Trading Engine serves as ATHENA's in-memory simulated trade execution and portfolio accounting gateway. It bridges the gap between approved risk/execution contracts and real-world trading by simulating order fills, position tracking, stop-loss / take-profit management, and capital accounting under strict, deterministic, and auditable mathematical rules.

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
                          ▼
                 ★ RISK ENGINE (S28) ★
                          │
                          ▼
               Immutable RiskAssessment
                          │
                          ▼
             ★ EXECUTION ENGINE (S29) ★
             - ExecutionRequest Contract
             - Order Lifecycle Enums
             - Idempotency & Conflict Guard
                          │
                          ▼
           ★ PAPER TRADING ENGINE (S30) ★
           - Simulated Order Fill Model
           - Position Accounting (LONG/SHORT)
           - Margin Reservation & Cash Accounting
           - Stop-Loss & Take-Profit Triggers
           - Conservative Ambiguous-Candle Policy
           - In-Memory Portfolio Ledger
                          │
                          ▼
                Immutable PaperTrade /
               PaperExecutionResult
                          │
                  🛑 STOP (S30 Scope)
                          │
             [ Future Live Exchange Adapter ]
```

> [!CRITICAL]
> **Strict Non-Live Safety Guarantee:**
> All fills, orders, positions, and balances in this engine are **strictly simulated** and held **in-memory**. The engine has **no network capabilities**, **no private Bitget API access**, and **no ability to submit live exchange orders**. Live execution remains strictly disabled and out of scope.

---

## 2. Public Interfaces & Data Flow

### Core Engine Interface (`PaperTradingEngine`)
Inherits from `ExecutionEngine` (`execution/base.py`) and implements:
- `execute(request: ExecutionRequest, market_price: float | None = None) -> PaperExecutionResult`:
  Validates request provenance, computes simulated fill price and fees, checks cash sufficiency, reserves margin, and creates an active `PaperPosition`.
- `close_position(position_id: str, exit_price: float, quantity: float | None = None, exit_reason: str = "MANUAL_CLOSE", timestamp: datetime | None = None) -> PaperTrade`:
  Closes an active position (full or partial), releases reserved margin, calculates gross and net PnL, deducts exit fees, and updates portfolio cash.
- `update_market_price(symbol: str, current_price: float, timestamp: datetime | None = None) -> list[PaperTrade]`:
  Updates mark-to-market unrealized PnL and evaluates protective stops (Stop Loss / Take Profit).
- `process_candle(candle: Candle) -> list[PaperTrade]`:
  Evaluates candle price boundaries against open positions under chronological causality and documented ambiguous-candle rules.
- `get_portfolio_snapshot() -> PaperPortfolio`:
  Returns an immutable snapshot of current cash, reserved margin, unrealized PnL, and total equity.
- `get_open_positions() -> tuple[PaperPosition, ...]`:
  Retrieves active open positions.
- `get_closed_trades() -> tuple[PaperTrade, ...]`:
  Retrieves all historical closed paper trades.

---

## 3. Order Lifecycle & Allowed State Transitions

Paper orders advance through the explicit ATHENA `OrderState` lifecycle:
```
  [CREATED] ──> [VALIDATED] ──> [SUBMITTED] ──> [ACKNOWLEDGED] ──> [FILLED]
     │              │               │
     └──> [REJECTED] └──> [REJECTED] └──> [REJECTED]
```
- Orders generated by the paper engine carry the explicit prefix `paper_ord_` (e.g. `paper_ord_000001`) and flag `is_paper = True`.
- They are distinctly separated from real exchange order IDs.
- Terminal states (`FILLED`, `REJECTED`, `CANCELLED`, `FAILED`) cannot be reopened or mutated.

---

## 4. Simulated Fill-Price Methodology & Slippage Model

Fill prices are calculated directly from reference prices without relying on live exchange connectivity:
- For **LONG** orders:
  $$\text{Fill Price} = \text{Reference Price} \times (1.0 + \text{slippage\_pct})$$
- For **SHORT** orders:
  $$\text{Fill Price} = \text{Reference Price} \times (1.0 - \text{slippage\_pct})$$
- Default `slippage_pct` is `0.0` (configurable for sensitivity analysis).
- Missing, non-finite (NaN, $\pm\infty$), zero, or negative prices are strictly rejected.

---

## 5. Portfolio Accounting & Fee Policies

### Linear USDT-Margined Perpetual Formulas
- $\text{Trade Notional} = \text{Fill Price} \times \text{Quantity} \times \text{Contract Multiplier}$
- $\text{Required Margin} = \frac{\text{Trade Notional}}{\text{Leverage}}$
- $\text{Entry Fee} = \text{Trade Notional} \times \text{Fee Rate}$ (default simulation assumption: $0.06\%$)
- $\text{Exit Fee} = \text{Exit Notional} \times \text{Fee Rate}$

### Fundamental Accounting Identity
$$\text{Total Equity} = \text{Cash} + \text{Reserved Margin} + \text{Unrealized PnL}$$

### Balance Transitions:
1. **On Position Entry:**
   - $\text{Total Cash Required} = \text{Required Margin} + \text{Entry Fee}$
   - If $\text{Total Cash Required} > \text{Available Cash}$, the order is rejected with `INSUFFICIENT_PAPER_CASH` without mutating state.
   - $\text{Cash} \leftarrow \text{Cash} - \text{Total Cash Required}$
   - $\text{Reserved Margin} \leftarrow \text{Reserved Margin} + \text{Required Margin}$
   - $\text{Fees Paid} \leftarrow \text{Fees Paid} + \text{Entry Fee}$
2. **Mark-to-Market (While Position Open):**
   - For **LONG**: $\text{Unrealized PnL} = (\text{Current Price} - \text{Entry Price}) \times \text{Quantity} \times \text{Multiplier}$
   - For **SHORT**: $\text{Unrealized PnL} = (\text{Entry Price} - \text{Current Price}) \times \text{Quantity} \times \text{Multiplier}$
3. **On Position Exit (Full or Partial):**
   - $\text{Ratio} = \frac{\text{Closed Quantity}}{\text{Position Quantity}}$
   - $\text{Released Margin} = \text{Position Margin} \times \text{Ratio}$
   - $\text{Gross PnL (LONG)} = (\text{Exit Price} - \text{Entry Price}) \times \text{Closed Quantity} \times \text{Multiplier}$
   - $\text{Gross PnL (SHORT)} = (\text{Entry Price} - \text{Exit Price}) \times \text{Closed Quantity} \times \text{Multiplier}$
   - $\text{Net PnL} = \text{Gross PnL} - (\text{Entry Fee} \times \text{Ratio}) - \text{Exit Fee}$
   - $\text{Cash} \leftarrow \text{Cash} + \text{Released Margin} + \text{Gross PnL} - \text{Exit Fee}$
   - $\text{Reserved Margin} \leftarrow \text{Reserved Margin} - \text{Released Margin}$
   - $\text{Realized PnL} \leftarrow \text{Realized PnL} + \text{Gross PnL}$
   - $\text{Fees Paid} \leftarrow \text{Fees Paid} + \text{Exit Fee}$

Zero leakage or double-counting occurs across any sequence of trades.

---

## 6. Stop-Loss & Take-Profit Simulation

### Directional Validation:
- **LONG Positions:** Enforces $\text{Stop Loss} < \text{Fill Price}$ and (if present) $\text{Take Profit} > \text{Fill Price}$.
- **SHORT Positions:** Enforces $\text{Stop Loss} > \text{Fill Price}$ and (if present) $\text{Take Profit} < \text{Fill Price}$.

### Ambiguous-Candle Conservative Policy:
If a single candle's high and low touch **both** the stop-loss and take-profit prices (such that the true intraday sequence is unknown):
- ATHENA enforces a **documented conservative policy**:
  The Stop Loss is assumed to have triggered first.
- Exit is executed at the exact stop-loss price with reason `"STOP_LOSS_CONSERVATIVE_AMBIGUOUS_CANDLE"`.
- This strictly avoids overestimating simulated performance or cherry-picking favorable outcomes.

---

## 7. Idempotency & State Integrity
- Requests supply a stable `idempotency_key`.
- An identical repeated submission returns the existing order result with `DUPLICATE_REQUEST_IGNORED` without creating duplicate positions or deducting fees again.
- A conflicting request with the same key returns `IDEMPOTENCY_CONFLICT` with status `CONFLICT` and leaves portfolio state unchanged.
- All operations validate inputs *before* committing state mutations.

---

## 8. Verification & Running Tests

Run the dedicated paper trading engine test suite:
```powershell
python -m unittest tests/test_paper_trading_engine.py -v
```

Run the full project regression test suite:
```powershell
python -m unittest discover -s tests -v
```

Byte-compile all project files:
```powershell
python -m compileall .
```
