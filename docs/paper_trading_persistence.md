# ATHENA Paper Trading Persistence & Restart Recovery

**Sprint:** 33  
**Status:** IMPLEMENTED & VERIFIED  
**Storage Engine:** SQLite 3 (Standard Library) with Write-Ahead Logging (WAL)  
**Safety Mode:** SIMULATION / PAPER TRADING ONLY  
**Default Database Path:** `data/paper_trading.db`  

---

## 1. Overview

Sprint 33 introduces durable, transactional SQLite persistence and restart recovery for ATHENA's paper trading engine. Previously (Sprint 30–31), the paper trading ledger operated strictly in transient Python process memory, meaning any process termination, system reboot, or unhandled exit completely wiped the open positions, trade history, accumulated PnL, and idempotency store, while resetting cash to initial equity.

With Sprint 33, the paper portfolio survives process restarts with complete mathematical fidelity, atomic ACID transactions, and deterministic idempotency deduplication across restarts.

---

## 2. Persisted State & Schema Version

Database schema versioning is explicitly enforced via the `schema_version` table. The current schema version is **`1`**.

### Schema Tables

1. **`schema_version`**
   - Tracks applied migrations and current schema version.
   - Fail-closed safety: If a database with a version higher than supported is encountered, `IncompatibleSchemaError` is raised.

2. **`paper_portfolio_state`**
   - Single-row table (`id = 1`) tracking aggregate portfolio accounting state.
   - Fields:
     - `starting_equity` (REAL): Initial account allocation (e.g. 10,000.00 USDT).
     - `cash` (REAL): Available cash balance (debited for required margin and entry fees; credited on exit).
     - `fee_rate` (REAL): Simulated fee rate (default: 0.0006 / 0.06%).
     - `slippage_pct` (REAL): Simulated slippage percentage.
     - `contract_multiplier` (REAL): Linear contract multiplier (1.0).
     - `realized_pnl` (REAL): Cumulative gross realized PnL across closed trades.
     - `fees_paid` (REAL): Cumulative transaction fees paid.
     - `order_counter` (INTEGER), `position_counter` (INTEGER), `trade_counter` (INTEGER): Deterministic sequence counters.
     - `updated_at` (TEXT): ISO 8601 UTC timestamp of last state mutation.

3. **`paper_orders`**
   - Stores all simulated order requests and fill details.
   - Fields: `order_id` (PRIMARY KEY), `request_id`, `idempotency_key`, `symbol`, `direction`, `order_type`, `quantity`, `requested_price`, `fill_price`, `state`, `fee`, `created_at`, `filled_at`, `is_paper`, `metadata` (JSON).

4. **`paper_positions`**
   - Stores all open and closed position tickets.
   - Fields: `position_id` (PRIMARY KEY), `order_id` (FOREIGN KEY), `symbol`, `direction`, `entry_price`, `quantity`, `contract_multiplier`, `notional`, `required_margin`, `leverage`, `entry_time`, `stop_loss_price`, `take_profit_price`, `entry_fee`, `unrealized_pnl`, `is_closed` (0 for open, 1 for closed), `is_paper`, `metadata` (JSON).

5. **`paper_trades`**
   - Immutable audit trail of closed trades (both full and partial closures).
   - Fields: `trade_id` (PRIMARY KEY), `position_id` (FOREIGN KEY), `order_id` (FOREIGN KEY), `symbol`, `direction`, `strategy_id`, `entry_price`, `exit_price`, `quantity`, `contract_multiplier`, `entry_time`, `exit_time`, `gross_pnl`, `entry_fee`, `exit_fee`, `net_pnl`, `exit_reason`, `is_paper`, `metadata` (JSON).

6. **`idempotency_records`**
   - Stores processed idempotency keys, canonical payload tuples, and linked order/position IDs.
   - Fields: `idempotency_key` (PRIMARY KEY), `canonical_payload` (JSON list), `order_id` (FOREIGN KEY), `position_id` (FOREIGN KEY), `created_at`.

---

## 3. SQLite Configuration & Safety Pragmas

Every SQLite connection configured by `PaperStorage` enforces:
```sql
PRAGMA journal_mode=WAL;       -- Write-Ahead Logging for high concurrency and crash durability
PRAGMA busy_timeout=5000;      -- 5-second lock timeout to avoid immediate lock contention
PRAGMA foreign_keys=ON;        -- Referential integrity enforcement
PRAGMA synchronous=NORMAL;     -- Optimal durability balance with WAL mode
```

---

## 4. Transactional Integrity & Rollback Design

All state mutations use atomic database transactions (`with conn:`):

### Order Fill Atomicity
When an order is filled, the following operations occur inside a single atomic transaction:
1. Insert `PaperOrder` into `paper_orders`.
2. Insert `PaperPosition` into `paper_positions`.
3. Insert idempotency key and canonical payload into `idempotency_records`.
4. Update `paper_portfolio_state` with new `cash`, `fees_paid`, `order_counter`, `position_counter`.

**Ordering Guarantee:**
- The database write and commit occur **before** memory state is modified.
- If a database failure occurs (disk full, lock error), the transaction is completely rolled back, memory state remains untouched, and `PaperExecutionResult` returns `ExecutionStatus.REJECTED` with reason code `PERSISTENCE_TRANSACTION_FAILED`.
- Zero divergence between in-memory ledger and disk storage is guaranteed.

### Position Close Atomicity
When closing a position (full or partial):
1. Insert `PaperTrade` into `paper_trades`.
2. Update `paper_positions` (set `is_closed = 1` if full close; update remaining quantity and margin if partial close).
3. Update `paper_portfolio_state` (`cash`, `realized_pnl`, `fees_paid`, `trade_counter`).
4. Apply in-memory mutations only after durable commit succeeds.

---

## 5. Startup & Restart Recovery

### First Startup (Fresh Database)
If the database file does not exist:
- Schema tables are created.
- Initial portfolio row (`id = 1`) is created with initial cash equal to `starting_equity` ($10,000.00).

### Subsequent Startup (Existing Database)
When `PaperTradingEngine` connects to an existing database:
1. Schema is verified.
2. `paper_portfolio_state` is loaded (`cash`, `fees_paid`, `realized_pnl`, counters).
3. Open positions are recovered from `SELECT * FROM paper_positions WHERE is_closed = 0`.
4. Closed trade history is recovered from `paper_trades`.
5. Orders are recovered from `paper_orders`.
6. Idempotency store is reconstructed from `idempotency_records`.
7. `reserved_margin` is recalculated as the sum of `required_margin` across all recovered open positions.
8. **Cash and portfolio equity do NOT reset to initial starting equity.**

### Fail-Closed Behavior
- **Corrupted Database:** If SQLite header is invalid or unreadable, `DatabaseCorruptionError` is raised. ATHENA will fail closed rather than silently overwriting the corrupted database with a fresh portfolio.
- **Incompatible Schema:** If a database with schema version higher than supported is encountered, `IncompatibleSchemaError` is raised.
- **Reset Protection:** Calling `engine.reset()` on an engine attached to persistent storage raises `RuntimeError` to prevent accidental wiping of portfolio history.

---

## 6. Idempotency Across Restarts

`idempotency_records` stores the canonical tuple of every processed execution request:
`('BTCUSDT', 'ema_trend_following', 'LONG', quantity, notional, ref_price, 'MARKET', leverage, 'deterministic_risk_v1')`.

Across process restarts:
- Replaying an **identical request** with the same idempotency key returns `ExecutionStatus.DUPLICATE` with the existing order and position. No new order, position, or fee deduction is created.
- Replaying the same key with a **materially conflicting request** (e.g. different quantity or symbol) returns `ExecutionStatus.CONFLICT` and rejects execution.

---

## 7. Accounting Invariants

The fundamental paper accounting identity holds across restarts:
$$\text{Total Equity} = \text{Cash} + \text{Reserved Margin} + \text{Unrealized PnL}$$

- **Mark-to-Market Note:** Open position quantities, entry prices, leverage, and required margins are preserved across restart. Unrealized PnL is mark-to-market and dynamically evaluates as live prices are ingested post-restart.

---

## 8. Verification & Test Commands

Run the persistence test suite:
```powershell
python -m unittest tests/test_paper_trading_persistence.py -v
```

Run full regression test suite:
```powershell
python -m unittest discover -s tests -v
```

Run compilation checks:
```powershell
python -m compileall .
```

---

## 9. Known Limitations & Scope Boundary

1. **Persistence is Not a Continuous Loop:**
   Implementing SQLite persistence solves the state-loss problem across restarts. It does **not** implement a continuous runtime loop or background market scanner in `core/app.py`. `Athena.run()` retains its single-pass CLI batch execution behavior.
2. **Active Position Monitoring is a Future Sprint:**
   Periodic price polling and automated SL/TP monitoring while running continuously will be implemented in **Sprint 34**.
3. **No Soak Executed in Sprint 33:**
   In accordance with the staged paper soak roadmap, no soak was executed or authorized during Sprint 33.
