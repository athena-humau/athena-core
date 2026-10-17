# ATHENA Execution Engine Foundation, Order Lifecycle & Safety Contracts (Sprint 29)

## 1. Purpose of the Execution Engine Foundation
The Execution Engine represents ATHENA's final boundary before physical exchange interaction. It establishes the strict safety, provenance, idempotency, and lifecycle contracts required to ingest approved `RiskAssessment` records and evaluate execution requests without triggering unauthorized side effects.

In Sprint 29, the engine is **strictly contract-only and non-executing**. It creates the rigorous safety infrastructure required for future execution adapters while ensuring zero live orders, paper trades, or simulated fills can occur.

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
             - Foundation Non-Executing Engine
                          │
                          ▼
              Immutable ExecutionResult
               (NOT_EXECUTED_FOUNDATION)
                          │
                  🛑 STOP (S29 Scope)
                          │
             [ Future Exchange Adapter ]
                          │
             [ Future Paper / Live Ops ]
```

---

## 2. Architectural Boundaries
$$\text{Signal Generation} \neq \text{Signal Validation} \neq \text{Judge Decision} \neq \text{Risk Assessment} \neq \text{Execution Request Accepted} \neq \text{Order Submitted} \neq \text{Order Filled}$$

- **Signal Generation (`strategy/`):** Formulates directional trading intent based on strategy-specific rules.
- **Signal Validation (`ai/signal_validator.py`):** Certifies structural, contextual, and selection integrity.
- **Judge Decision (`ai/judge.py`):** Evaluates multi-factor market evidence to decide `PASS` or `REJECT`.
- **Risk Assessment (`risk/`):** Evaluates account capital, stop-loss placement, position notional, portfolio exposure, and leverage limits.
- **Execution Request Contract (`execution/models.py`):** Strictly verifies risk approval provenance, sizing limits, and idempotency keys.
- **Foundation Execution Engine (`execution/deterministic_engine.py`):** Non-executing reference engine returning structured audit results.
- **Exchange Order Submission (`exchange/` - Future):** Physical dispatch to exchange endpoints.
- **Order Execution / Fills (Exchange / Future):** Physical matching engine fill processing.

> [!CRITICAL]
> **Strict Execution Safety Guarantee:**
> In Sprint 29, the Execution Engine has **no** network access, **no** access to private credentials, **no** simulated fill generation, and **no** order placement capabilities.

---

## 3. Execution Request Contract (`ExecutionRequest`)
An immutable dataclass (`frozen=True`) that binds directly to an approved `RiskAssessment`:

| Field | Type | Description |
|---|---|---|
| `request_id` | `str` | Unique, non-empty identifier for the request |
| `idempotency_key` | `str` | Stable, non-empty key for duplicate/conflict detection |
| `symbol` | `str` | Must exactly match `risk_assessment.symbol` |
| `strategy_id` | `str` | Must exactly match `risk_assessment.strategy_id` |
| `direction` | `SignalDirection` | Must match `risk_assessment.direction` (cannot be `FLAT`) |
| `quantity` | `float` | Strictly positive, cannot exceed `risk_assessment.proposal.quantity` |
| `notional` | `float` | Strictly positive, cannot exceed `risk_assessment.notional` |
| `reference_price` | `float` | Strictly positive entry reference price |
| `risk_assessment` | `RiskAssessment` | Provenance link: must have `verdict == RiskVerdict.PASS` |
| `risk_policy_id` | `str` | Must match `risk_assessment.policy_id` |
| `order_type` | `OrderType` | Enum: `LIMIT` or `MARKET` |
| `requested_leverage`| `float` | Must be $\ge 1.0$ |
| `created_at` | `datetime` | UTC timestamp |
| `metadata` | `Mapping[str, Any]`| Immutable `MappingProxyType` |

### Provenance Enforcement Rules:
1. `risk_assessment` must have `is_passed == True` (`verdict == RiskVerdict.PASS`).
2. `risk_assessment.proposal` must be present and valid.
3. `symbol`, `strategy_id`, and `direction` must match the approved assessment.
4. Direction cannot be `FLAT`.
5. `quantity` and `notional` cannot exceed risk-approved quantities.
6. Inputs cannot be silently clipped, repaired, or invented.

---

## 4. Order Lifecycle Contract

### Lifecycle States (`OrderState` Enum)
- `CREATED`: Initial instantiation of an order intention.
- `VALIDATED`: Request has satisfied all schema, provenance, and limit checks.
- `SUBMITTED`: Order transmitted to exchange (deferred/future).
- `ACKNOWLEDGED`: Order accepted by exchange matching engine (deferred/future).
- `PARTIALLY_FILLED`: Partial quantity executed (deferred/future).
- `FILLED`: Order fully executed (terminal).
- `REJECTED`: Order rejected by validation or exchange (terminal).
- `CANCELLED`: Order cancelled by user or system (terminal).
- `FAILED`: Execution failed due to unexpected error (terminal).
- `UNKNOWN`: State unknown due to timeout or disconnect (requires reconciliation).

### State Transition Matrix (`LEGAL_TRANSITIONS`)

```
   [CREATED]
     ├──> VALIDATED
     ├──> REJECTED (terminal)
     └──> FAILED (terminal)

   [VALIDATED]
     ├──> SUBMITTED (future)
     ├──> REJECTED (terminal)
     └──> FAILED (terminal)

   [SUBMITTED]
     ├──> ACKNOWLEDGED (future)
     ├──> REJECTED (terminal)
     ├──> FAILED (terminal)
     └──> UNKNOWN

   [ACKNOWLEDGED]
     ├──> PARTIALLY_FILLED (future)
     ├──> FILLED (terminal)
     ├──> CANCELLED (terminal)
     ├──> FAILED (terminal)
     └──> UNKNOWN

   [PARTIALLY_FILLED]
     ├──> PARTIALLY_FILLED (future)
     ├──> FILLED (terminal)
     ├──> CANCELLED (terminal)
     ├──> FAILED (terminal)
     └──> UNKNOWN

   [UNKNOWN]
     ├──> FAILED (terminal)
     └──> CANCELLED (terminal)

   [TERMINAL STATES: FILLED, REJECTED, CANCELLED, FAILED]
     └──> (No transitions allowed; terminal states cannot reopen)
```

Illegal transitions (e.g. `FILLED -> CREATED`, `UNKNOWN -> FILLED`, `REJECTED -> FILLED`) raise `IllegalOrderStateTransitionError`.

---

## 5. Idempotency & Conflict Semantics

### Deterministic Content Canonicalization
Idempotency comparisons compute a canonical payload tuple:
$$(\text{symbol}, \text{strategy\_id}, \text{direction}, \text{round}(\text{qty}, 8), \text{round}(\text{notional}, 4), \text{round}(\text{ref\_price}, 4), \text{order\_type}, \text{round}(\text{leverage}, 4), \text{policy\_id})$$

### Semantics:
1. **First Presentation of Key:** Registered in idempotency store; request proceeds to validation.
2. **Repeated Key with Identical Content:** Recognized as duplicate; returns `ExecutionStatus.DUPLICATE` with reason `DUPLICATE_REQUEST_IGNORED`.
3. **Repeated Key with Conflicting Content:** Rejected as conflict; returns `ExecutionStatus.CONFLICT` with reason `IDEMPOTENCY_CONFLICT`.

> [!NOTE]
> **Scope & Storage Limitation:**
> In Sprint 29, the idempotency store is maintained strictly **in-memory** within the reference execution engine process. Durable or distributed multi-instance idempotency requires an external persistent store that will be evaluated in future sprints.

---

## 6. Reference Non-Executing Engine (`FoundationOnlyExecutionEngine`)
The reference engine implements `ExecutionEngine` and enforces all safety rules while guaranteeing zero side effects.

### Meaning of `NOT_EXECUTED_FOUNDATION_ONLY`
- Indicates that the execution request passed all validation, provenance, sizing, and idempotency checks.
- Confirms that the order was **NOT** submitted, acknowledged, or filled.
- `side_effect_occurred` is strictly `False`.
- `execution_attempted` is strictly `False`.
- `exchange_order_id` is strictly `None`.
- `filled_quantity` is strictly `0.0`.
- `execution_price` is strictly `None`.

### Failure & Reason Codes Catalog

| Reason Code | Category | Meaning |
|---|---|---|
| `NOT_EXECUTED_FOUNDATION_ONLY` | Success (Contract) | Request fully validated; execution intentionally deferred |
| `DUPLICATE_REQUEST_IGNORED` | Idempotency | Identical request payload previously seen for key |
| `IDEMPOTENCY_CONFLICT` | Idempotency | Conflicting payload submitted for an existing key |
| `MISSING_EXECUTION_REQUEST` | Integrity | Request argument was `None` |
| `INVALID_REQUEST_TYPE` | Integrity | Request was not an instance of `ExecutionRequest` |
| `MISSING_REQUEST_ID` | Integrity | `request_id` was empty or whitespace |
| `MISSING_IDEMPOTENCY_KEY` | Integrity | `idempotency_key` was empty or whitespace |
| `MISSING_RISK_ASSESSMENT` | Provenance | Request lacked an associated `RiskAssessment` |
| `INVALID_RISK_ASSESSMENT_TYPE` | Provenance | Associated assessment was not a `RiskAssessment` instance |
| `RISK_VERDICT_REJECTED` | Provenance | Associated `RiskAssessment.verdict != RiskVerdict.PASS` |
| `MISSING_RISK_PROPOSAL` | Provenance | Associated `RiskAssessment` lacked an approved `TradeProposal` |
| `SYMBOL_MISMATCH` | Provenance | Request symbol does not match approved risk symbol |
| `STRATEGY_MISMATCH` | Provenance | Request strategy does not match approved risk strategy |
| `DIRECTION_MISMATCH` | Provenance | Request direction does not match approved risk direction |
| `FLAT_DIRECTION_NOT_EXECUTABLE` | Direction | Signal direction is `FLAT` |
| `INVALID_DIRECTION` | Direction | Signal direction is invalid or undefined |
| `INVALID_QUANTITY_OR_PRICE` | Sanity | Quantity, notional, or reference price non-positive |
| `QUANTITY_EXCEEDS_RISK_LIMIT` | Limits | Requested quantity exceeds approved proposal quantity |
| `NOTIONAL_EXCEEDS_RISK_LIMIT` | Limits | Requested notional exceeds approved risk notional |
| `RISK_POLICY_MISMATCH` | Provenance | Request policy ID does not match risk policy ID |

---

## 7. Security and Safeguard Audit
1. **Zero Network Calls:** No sockets, HTTP requests, or websocket clients initialized.
2. **Zero Credential Usage:** `config/secrets.py` and `.env` credentials are not imported, read, or stored.
3. **Immutability:** All request and result structures are frozen dataclasses using immutable mappings.
4. **Safety Locks:** `ExecutionResult` post-init raises `ValueError` if any caller attempts to set `side_effect_occurred=True` or fabricate order IDs/fills.
