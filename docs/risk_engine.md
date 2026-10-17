# ATHENA Risk Engine Foundation & Trade Risk Assessment Contract (Sprint 28)

## 1. Purpose of the Risk Engine
The Risk Engine serves as ATHENA's account and portfolio risk gateway between multi-factor decision approval (`JudgeDecision`) and downstream order execution. It consumes a passing `JudgeDecision` alongside explicit trade parameters (entry price, stop loss price, quantity, account equity, margin context) to verify whether the proposed trade satisfies configured risk constraints.

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
                  🛑 STOP (S28 Scope)
                          │
                [ Future Order Router ]
                          │
               [ Future Execution Engine ]
```

---

## 2. Architectural Boundaries
$$\text{Signal Generation} \neq \text{Signal Validation} \neq \text{Judge Decision} \neq \text{Risk Assessment} \neq \text{Order Execution}$$

- **Signal Generation (`strategy/`):** Formulates directional trading intent based on strategy-specific rules.
- **Signal Validation (`ai/signal_validator.py`):** Certifies structural, contextual, and selection integrity.
- **Judge Decision (`ai/judge.py`):** Evaluates multi-factor market evidence to decide `PASS` or `REJECT`.
- **Risk Assessment (`risk/`):** Evaluates account capital, stop-loss placement, position notional, portfolio exposure, and leverage limits.
- **Order Execution (`execution/` - Future):** Formulates order payloads and transmits them to exchange endpoints.

> [!IMPORTANT]
> A `PASS` assessment from the Risk Engine confirms solely that risk constraints are satisfied. It does **not** place orders, transmit execution messages, or connect to exchange trading APIs.

---

## 3. Mathematical Risk Models (Linear USDT-Margined Perpetual Contracts)

### Position Notional
$$\text{Notional} = \text{Entry Price} \times \text{Quantity} \times \text{Contract Multiplier}$$

### Stop Loss Placement Rules
- **LONG Trade:**
  $$\text{Stop Loss Price} < \text{Entry Price}$$
  $$\text{Gross Loss} = (\text{Entry Price} - \text{Stop Loss Price}) \times \text{Quantity} \times \text{Contract Multiplier}$$
  *(If $\text{Stop Loss Price} \ge \text{Entry Price}$, reject with `WRONG_SIDE_STOP`)*

- **SHORT Trade:**
  $$\text{Stop Loss Price} > \text{Entry Price}$$
  $$\text{Gross Loss} = (\text{Stop Loss Price} - \text{Entry Price}) \times \text{Quantity} \times \text{Contract Multiplier}$$
  *(If $\text{Stop Loss Price} \le \text{Entry Price}$, reject with `WRONG_SIDE_STOP`)*

### Equity Risk Percentage
$$\text{Risk Percent} = \left(\frac{\text{Gross Loss}}{\text{Account Equity}}\right) \times 100\%$$
*(Must satisfy $\text{Risk Percent} \le \text{max\_risk\_per\_trade\_pct}$)*

### Portfolio Exposure Rules
- **New Aggregate Exposure:**
  $$\text{Aggregate Exposure} = \text{Existing Exposure} + \text{Trade Notional} \le \text{max\_aggregate\_exposure}$$
- **New Symbol Exposure (Optional):**
  $$\text{Symbol Exposure} = \text{Existing Symbol Exposure} + \text{Trade Notional} \le \text{max\_symbol\_exposure}$$

### Leverage & Margin Verification
- **Leverage Upper Bound:**
  $$\text{Requested Leverage} \le \text{max\_leverage}$$
- **Required Margin (when available margin provided):**
  $$\text{Required Margin} = \frac{\text{Notional}}{\text{Requested Leverage}} \le \text{Available Margin}$$

---

## 4. Input & Output Contracts

### Input Contract: `TradeProposal`
- `judge_decision`: An instance of `JudgeDecision` with `verdict == JudgeVerdict.PASS`.
- `entry_price`: Strictly positive float.
- `stop_loss_price`: Strictly positive float.
- `quantity`: Strictly positive float.
- `account_equity`: Strictly positive float.
- `available_margin`: Optional non-negative float.
- `existing_exposure`: Non-negative float (default: 0.0).
- `existing_symbol_exposure`: Non-negative float (default: 0.0).
- `contract_multiplier`: Strictly positive float (default: 1.0).
- `requested_leverage`: Float $\ge 1.0$ (default: 1.0).
- `proposal_id`: Optional string identifier.
- `metadata`: Immutable mapping for custom metadata.

### Configuration Contract: `RiskPolicy`
- `policy_id`: Non-empty string identifier.
- `max_risk_per_trade_pct`: Maximum risk percentage per trade (e.g. 2.0%).
- `max_position_notional`: Maximum notional per trade in quote currency (e.g. 10,000 USDT).
- `max_aggregate_exposure`: Maximum combined portfolio notional exposure (e.g. 25,000 USDT).
- `max_leverage`: Maximum allowable leverage (e.g. 10.0x).
- `max_symbol_exposure`: Optional maximum notional exposure per individual symbol.
- `metadata`: Immutable mapping.

### Output Contract: `RiskAssessment`
- `verdict`: `RiskVerdict.PASS` or `RiskVerdict.REJECT`.
- `symbol`: Symbol of the proposal.
- `strategy_id`: Strategy that produced the underlying candidate.
- `direction`: `SignalDirection.LONG` or `SignalDirection.SHORT`.
- `notional`: Calculated trade notional.
- `estimated_loss`: Calculated dollar loss if stopped out.
- `risk_percent`: Calculated loss as a percentage of account equity.
- `reason_codes`: Tuple of diagnostic reason strings.
- `explanation`: Human-readable summary.
- `check_evidence`: Immutable mapping of check details.
- `proposal`: Original immutable `TradeProposal`.
- `policy_id`: Identifier of the policy used for evaluation.
- `assessment_timestamp`: UTC datetime.
- `is_passed`: Helper property returning `True` if `verdict == RiskVerdict.PASS`.
- `is_rejected`: Helper property returning `True` if `verdict == RiskVerdict.REJECT`.

---

## 5. Reason Codes Catalog

| Reason Code | Category | Meaning |
|---|---|---|
| `RISK_CONSTRAINTS_SATISFIED` | Success | All configured account, price, stop, exposure, and leverage checks passed |
| `MISSING_PROPOSAL` | Integrity | Proposal argument was `None` |
| `INVALID_PROPOSAL_TYPE` | Integrity | Proposal was not an instance of `TradeProposal` |
| `MISSING_JUDGE_DECISION` | Pipeline | Proposal lacked an associated `JudgeDecision` |
| `INVALID_JUDGE_DECISION` | Pipeline | Decision was not an instance of `JudgeDecision` |
| `JUDGE_VERDICT_REJECTED` | Pipeline | Associated `JudgeDecision.verdict != JudgeVerdict.PASS` |
| `FLAT_SIGNAL_NOT_ACTIONABLE` | Pipeline | Associated signal direction is `FLAT` |
| `INVALID_DIRECTION` | Pipeline | Signal direction is undefined or invalid |
| `INVALID_EQUITY` | Sanity | Account equity was non-positive ($\le 0$) |
| `INVALID_PRICE_OR_QUANTITY` | Sanity | Entry price, stop loss, quantity, or multiplier was non-positive |
| `WRONG_SIDE_STOP` | Risk Rule | Stop loss placement on wrong side (LONG stop $\ge$ entry, or SHORT stop $\le$ entry) |
| `EXCEEDS_POSITION_NOTIONAL_LIMIT` | Risk Rule | Trade notional exceeds `policy.max_position_notional` |
| `EXCEEDS_AGGREGATE_EXPOSURE_LIMIT` | Risk Rule | Aggregate portfolio exposure exceeds `policy.max_aggregate_exposure` |
| `EXCEEDS_SYMBOL_EXPOSURE_LIMIT` | Risk Rule | Combined symbol exposure exceeds `policy.max_symbol_exposure` |
| `EXCEEDS_MAX_RISK_PER_TRADE` | Risk Rule | Trade risk percentage exceeds `policy.max_risk_per_trade_pct` |
| `EXCEEDS_MAX_LEVERAGE` | Risk Rule | Requested leverage exceeds `policy.max_leverage` |
| `INVALID_LEVERAGE` | Sanity | Requested leverage is less than 1.0x |
| `INSUFFICIENT_MARGIN` | Risk Rule | Required initial margin exceeds `available_margin` |

---

## 6. Concrete Financial Calculation Example

### Scenario A: Valid LONG Trade
- **Entry Price:** 50,000.00 USDT
- **Stop Loss Price:** 49,000.00 USDT (Distance = 1,000.00 USDT)
- **Quantity:** 0.10 BTC
- **Multiplier:** 1.0
- **Account Equity:** 10,000.00 USDT
- **Requested Leverage:** 2.0x
- **Available Margin:** 5,000.00 USDT

**Evaluated Metrics:**
- $\text{Notional} = 50000 \times 0.10 = 5000.00 \text{ USDT} \le 10000 \text{ (PASS)}$
- $\text{Gross Loss} = (50000 - 49000) \times 0.10 = 100.00 \text{ USDT}$
- $\text{Risk Percent} = (100 / 10000) \times 100 = 1.0\% \le 2.0\% \text{ (PASS)}$
- $\text{Required Margin} = 5000 / 2.0 = 2500.00 \text{ USDT} \le 5000 \text{ (PASS)}$
- **Verdict:** `RiskVerdict.PASS` (`RISK_CONSTRAINTS_SATISFIED`)

### Scenario B: Exceeded Risk Per Trade
- Same entry, but stop loss placed at 47,000.00 USDT (Distance = 3,000.00 USDT)
- $\text{Gross Loss} = 3000 \times 0.10 = 300.00 \text{ USDT}$
- $\text{Risk Percent} = (300 / 10000) \times 100 = 3.0\% > 2.0\%$
- **Verdict:** `RiskVerdict.REJECT` (`EXCEEDS_MAX_RISK_PER_TRADE`)
