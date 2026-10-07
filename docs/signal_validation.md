# ATHENA Signal Validation Contract & Pre-Judge Boundary (Sprint 26)

## 1. Why Signal Validation Exists
In ATHENA's modular trading system, strategies generate directional intent signals (`Signal`), but raw strategy output must never be passed directly to the trade decision layer (Judge) or execution engine without rigorous verification.

Signal validation establishes an immutable, deterministic quality and security gateway that guarantees:
- Signals are structurally sound (valid types, bounded confidence, intact timestamps).
- Signals are contextually consistent with the underlying market data and analysis context (symbol match, context validity).
- Signals align with the upstream strategy selection decision (strategy ID match).
- Malformed, corrupt, or contradictory signals are rejected before any decision-making occurs.

```
                    Strategy Selection
                  (StrategySelection)
                           │
                           ▼
                   Selected Strategy
             (resolve from StrategyRegistry)
                           │
                           ▼
                    Strategy Signal
                   (strategy.evaluate)
                           │
                           ▼
               ★ SIGNAL VALIDATION ★
           (DeterministicSignalValidator)
                           │
                           ▼
                  Validated Candidate
                 (ValidatedCandidate)
                           │
                  🛑 STOP (Sprint 26 Boundary)
                           │
                   [ FUTURE JUDGE ]
                           │
                    [ FUTURE RISK ]
                           │
                  [ FUTURE EXECUTION ]
```

---

## 2. Difference Between Signal and Validated Candidate

| Dimension | `Signal` (`strategy/models.py`) | `ValidatedCandidate` (`ai/models.py`) |
| :--- | :--- | :--- |
| **Origin** | Emitted by concrete strategies (`evaluate()`) | Produced exclusively by `SignalValidator` (`validate()`) |
| **Scope** | Expresses raw strategy intent (`direction`, `confidence`) | Certifies structural, contextual, and selection validity |
| **Validation Status** | Not validated against context or selection | Explicit status (`PASSED` or `FAILED`) with audit failure log |
| **Downstream Role** | Strategy-level output | Pre-Judge decision contract input |
| **Mutability** | Frozen dataclass | Frozen dataclass with immutable failure log & metadata |

---

## 3. What the Validator Checks
`DeterministicSignalValidator` enforces 10 strict checks:
1. **Signal Presence & Type:** Validates that `signal` is not `None` and is an instance of `Signal`.
2. **Symbol Integrity:** Confirms `symbol` is a non-empty, stripped string.
3. **Strategy ID Integrity:** Confirms `strategy_id` is a non-empty, stripped string.
4. **Direction Integrity:** Validates that `direction` is a valid member of `SignalDirection` (`LONG`, `SHORT`, or `FLAT`).
5. **Confidence Boundary:** Enforces that `confidence` is numeric (int/float, not bool) and strictly within $[0.0, 1.0]$.
6. **Timestamp Integrity:** Validates that `timestamp` is a valid `datetime` instance.
7. **Context Symbol Consistency:** When `StrategyContext` is provided, ensures `signal.symbol == context.symbol`.
8. **Context Structural Validity:** Confirms `context` is a valid `StrategyContext` instance.
9. **Strategy Selection Consistency:** When `selected_strategy_id` (or `StrategySelection`) is provided, verifies `signal.strategy_id == expected_strategy_id`.
10. **Metadata Integrity:** Validates that `metadata` is a structural mapping.

---

## 4. What the Validator Deliberately Does NOT Check
The validator is strictly an analytical and structural gateway:
- **NO Profitability Check:** Does not assess win-rate, profit expectancy, or return.
- **NO Risk Management:** Does not calculate leverage, position sizing, stop-loss, or take-profit.
- **NO Trade Decision:** Validating a signal does **not** mean placing a trade.
- **NO Exchange Access:** Does not connect to exchanges, order books, or private APIs.
- **NO Execution:** Has zero authority or methods to place, modify, or cancel orders.
- **NO Lookahead:** Does not inspect future candles or non-historical data.

---

## 5. Failure Semantics
Validation fails safely and explicitly:
- **No Silent Repairs:** Malformed values (e.g. `confidence = 1.4`) are **never** clipped or repaired; they are rejected with `status = ValidationStatus.FAILED`.
- **Comprehensive Failure Tracking:** All detected failures are aggregated in the `failures: tuple[str, ...]` attribute for full auditability.
- **Safe Fallback Fields:** When validation fails, `is_valid` returns `False`, and confidence defaults safely without fabricating synthetic values.

---

## 6. FLAT Semantics
- `SignalDirection.FLAT` represents an active decision by a strategy to remain out of the market (neutrality, lack of trend alignment, or insufficient momentum).
- `FLAT` is **structurally valid** and is **never** treated as an error or failed state.
- If all structural and contextual requirements are satisfied, a `FLAT` signal receives `ValidationStatus.PASSED`.
- The validator never alters `FLAT` into `LONG` or `SHORT`.

---

## 7. Relationship to Future Judge
```
Validated Candidate (is_valid=True) ──► Future Judge ──► Risk Engine ──► Execution
Validated Candidate (is_valid=False) ──► Discarded / Audited (Never sent to Execution)
```
The future Judge will receive `ValidatedCandidate` objects. It is guaranteed that any candidate with `status == ValidationStatus.PASSED` has passed all structural, contextual, and selection integrity tests, allowing the Judge to focus purely on strategic multi-factor evaluation and risk alignment.

---

## 8. Architectural Boundaries
$$\text{Signal Validation} \neq \text{Judge} \neq \text{Risk} \neq \text{Execution}$$

1. **Signal Validation:** "Is this signal structurally sound, contextually consistent, and emitted by the selected strategy?"
2. **Judge (Future):** "Given market conditions and validation evidence, should ATHENA pursue this setup?"
3. **Risk (Future):** "If pursuing this setup, what is the allowable position size, leverage, and stop-loss boundary?"
4. **Execution (Future):** "How should the order be safely submitted and filled on the exchange?"
