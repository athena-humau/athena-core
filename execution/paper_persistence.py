from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from execution.models import OrderState, OrderType
from execution.paper_models import (
    PaperOrder,
    PaperPosition,
    PaperTrade,
    validate_finite_number,
)
from strategy.models import SignalDirection

SCHEMA_VERSION = 1


class PersistenceError(Exception):
    """Base exception for all paper trading persistence errors."""
    pass


class DatabaseCorruptionError(PersistenceError):
    """Raised when the database schema or data is corrupted, invalid, or unreadable."""
    pass


class IncompatibleSchemaError(PersistenceError):
    """Raised when the database schema version is incompatible with current code."""
    pass


class TransactionError(PersistenceError):
    """Raised when an atomic database transaction fails."""
    pass


@dataclass(frozen=True)
class RecoveredLedgerState:
    """
    Immutable container capturing recovered paper ledger state from durable SQLite storage.
    """
    starting_equity: float
    cash: float
    fee_rate: float
    slippage_pct: float
    contract_multiplier: float
    realized_pnl: float
    fees_paid: float
    order_counter: int
    position_counter: int
    trade_counter: int
    open_positions: dict[str, PaperPosition] = field(default_factory=dict)
    closed_trades: list[PaperTrade] = field(default_factory=list)
    orders: dict[str, PaperOrder] = field(default_factory=dict)
    idempotency_store: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    idempotency_order_map: dict[str, str] = field(default_factory=dict)
    idempotency_pos_map: dict[str, str] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PaperStorage:
    """
    Durable SQLite persistence and restart recovery engine for ATHENA Paper Trading.

    Guarantees:
      1. ACID compliance with explicit transaction boundaries (BEGIN IMMEDIATE).
      2. Write-Ahead Logging (WAL) mode with busy timeout for concurrent safety.
      3. Foreign key integrity enforcement.
      4. Explicit schema versioning and fail-closed corruption detection.
      5. Full recovery of portfolio cash, open positions, closed trades, orders, and idempotency store.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._is_memory = self.db_path == ":memory:"

        if not self._is_memory:
            abs_path = os.path.abspath(self.db_path)
            parent_dir = os.path.dirname(abs_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            self.db_path = abs_path

        self._conn: sqlite3.Connection | None = None
        self._init_connection()

    def _init_connection(self) -> None:
        """Establish SQLite connection and configure safety pragmas."""
        try:
            # For :memory:, keep single connection open; for files, connect with timeout
            if self._conn is None:
                self._conn = sqlite3.connect(
                    self.db_path,
                    timeout=10.0,
                    check_same_thread=False,
                )
                self._conn.row_factory = sqlite3.Row

            cursor = self._conn.cursor()

            # Configure WAL mode (if file-based; memory databases will return 'memory')
            cursor.execute("PRAGMA journal_mode=WAL;")
            mode = cursor.fetchone()[0]

            cursor.execute("PRAGMA busy_timeout=5000;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA synchronous=NORMAL;")

            if not self._is_memory and mode.lower() != "wal":
                raise PersistenceError(f"Failed to enable SQLite WAL mode on '{self.db_path}', current mode: {mode}")

        except sqlite3.DatabaseError as err:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            raise DatabaseCorruptionError(f"Failed to open SQLite database at '{self.db_path}': {err}") from err
        except Exception as err:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            if not isinstance(err, PersistenceError):
                raise PersistenceError(f"Initialization error for database '{self.db_path}': {err}") from err
            raise

    def get_connection(self) -> sqlite3.Connection:
        """Return the active SQLite connection."""
        if self._conn is None:
            self._init_connection()
        return self._conn

    def close(self) -> None:
        """Safely close active database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def initialize_schema(self) -> int:
        """
        Verify or initialize database schema to SCHEMA_VERSION.
        Fails closed on corrupt or newer incompatible schema.
        """
        conn = self.get_connection()
        try:
            with conn:
                # 1. Check if schema_version table exists
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version';"
                )
                exists = cursor.fetchone()

                if not exists:
                    # Check if there are other existing tables indicating untracked/corrupted DB
                    cursor.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('paper_portfolio_state', 'paper_orders', 'paper_positions');"
                    )
                    other_tables = cursor.fetchall()
                    if other_tables:
                        raise DatabaseCorruptionError(
                            f"Database '{self.db_path}' contains existing paper tables without schema_version tracking"
                        )

                    # Fresh initialization: create schema_version table
                    cursor.execute(
                        """
                        CREATE TABLE schema_version (
                            version INTEGER PRIMARY KEY,
                            applied_at TEXT NOT NULL,
                            description TEXT NOT NULL
                        );
                        """
                    )
                    cursor.execute(
                        """
                        CREATE TABLE paper_portfolio_state (
                            id INTEGER PRIMARY KEY CHECK (id = 1),
                            starting_equity REAL NOT NULL,
                            cash REAL NOT NULL,
                            fee_rate REAL NOT NULL,
                            slippage_pct REAL NOT NULL,
                            contract_multiplier REAL NOT NULL,
                            realized_pnl REAL NOT NULL,
                            fees_paid REAL NOT NULL,
                            order_counter INTEGER NOT NULL,
                            position_counter INTEGER NOT NULL,
                            trade_counter INTEGER NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        """
                    )
                    cursor.execute(
                        """
                        CREATE TABLE paper_orders (
                            order_id TEXT PRIMARY KEY,
                            request_id TEXT NOT NULL,
                            idempotency_key TEXT NOT NULL,
                            symbol TEXT NOT NULL,
                            direction TEXT NOT NULL,
                            order_type TEXT NOT NULL,
                            quantity REAL NOT NULL,
                            requested_price REAL NOT NULL,
                            fill_price REAL NOT NULL,
                            state TEXT NOT NULL,
                            fee REAL NOT NULL,
                            created_at TEXT NOT NULL,
                            filled_at TEXT,
                            is_paper INTEGER NOT NULL DEFAULT 1,
                            metadata TEXT NOT NULL
                        );
                        """
                    )
                    cursor.execute(
                        """
                        CREATE TABLE paper_positions (
                            position_id TEXT PRIMARY KEY,
                            order_id TEXT NOT NULL,
                            symbol TEXT NOT NULL,
                            direction TEXT NOT NULL,
                            entry_price REAL NOT NULL,
                            quantity REAL NOT NULL,
                            contract_multiplier REAL NOT NULL,
                            notional REAL NOT NULL,
                            required_margin REAL NOT NULL,
                            leverage REAL NOT NULL,
                            entry_time TEXT NOT NULL,
                            stop_loss_price REAL NOT NULL,
                            take_profit_price REAL,
                            entry_fee REAL NOT NULL,
                            unrealized_pnl REAL NOT NULL,
                            is_closed INTEGER NOT NULL DEFAULT 0,
                            is_paper INTEGER NOT NULL DEFAULT 1,
                            metadata TEXT NOT NULL,
                            FOREIGN KEY(order_id) REFERENCES paper_orders(order_id)
                        );
                        """
                    )
                    cursor.execute(
                        """
                        CREATE TABLE paper_trades (
                            trade_id TEXT PRIMARY KEY,
                            position_id TEXT NOT NULL,
                            order_id TEXT NOT NULL,
                            symbol TEXT NOT NULL,
                            direction TEXT NOT NULL,
                            strategy_id TEXT NOT NULL,
                            entry_price REAL NOT NULL,
                            exit_price REAL NOT NULL,
                            quantity REAL NOT NULL,
                            contract_multiplier REAL NOT NULL,
                            entry_time TEXT NOT NULL,
                            exit_time TEXT NOT NULL,
                            gross_pnl REAL NOT NULL,
                            entry_fee REAL NOT NULL,
                            exit_fee REAL NOT NULL,
                            net_pnl REAL NOT NULL,
                            exit_reason TEXT NOT NULL,
                            is_paper INTEGER NOT NULL DEFAULT 1,
                            metadata TEXT NOT NULL,
                            FOREIGN KEY(position_id) REFERENCES paper_positions(position_id),
                            FOREIGN KEY(order_id) REFERENCES paper_orders(order_id)
                        );
                        """
                    )
                    cursor.execute(
                        """
                        CREATE TABLE idempotency_records (
                            idempotency_key TEXT PRIMARY KEY,
                            canonical_payload TEXT NOT NULL,
                            order_id TEXT,
                            position_id TEXT,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY(order_id) REFERENCES paper_orders(order_id),
                            FOREIGN KEY(position_id) REFERENCES paper_positions(position_id)
                        );
                        """
                    )

                    now_iso = datetime.now(timezone.utc).isoformat()
                    cursor.execute(
                        "INSERT INTO schema_version (version, applied_at, description) VALUES (?, ?, ?);",
                        (SCHEMA_VERSION, now_iso, "Initial ATHENA paper trading persistence schema"),
                    )
                    return SCHEMA_VERSION

                # Schema table exists; check version
                cursor.execute("SELECT MAX(version) FROM schema_version;")
                row = cursor.fetchone()
                current_ver = row[0] if row and row[0] is not None else 0

                if current_ver > SCHEMA_VERSION:
                    raise IncompatibleSchemaError(
                        f"Database schema version {current_ver} is newer than supported version {SCHEMA_VERSION}"
                    )
                if current_ver < 1:
                    raise DatabaseCorruptionError(f"Invalid schema version {current_ver} in '{self.db_path}'")

                return current_ver

        except (IncompatibleSchemaError, DatabaseCorruptionError):
            raise
        except sqlite3.DatabaseError as err:
            raise DatabaseCorruptionError(f"Database corruption encountered in '{self.db_path}': {err}") from err
        except Exception as err:
            raise PersistenceError(f"Unexpected error initializing schema in '{self.db_path}': {err}") from err

    def save_portfolio_initialization(
        self,
        starting_equity: float,
        cash: float,
        fee_rate: float,
        slippage_pct: float,
        contract_multiplier: float,
    ) -> None:
        """
        Record initial portfolio state if row id=1 does not already exist.
        Safe against repeated calls: NEVER overwrites an already-existing ledger state.
        """
        conn = self.get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM paper_portfolio_state WHERE id = 1;")
                if cursor.fetchone() is not None:
                    # Ledger state already exists: do not overwrite
                    return

                now_iso = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    """
                    INSERT INTO paper_portfolio_state (
                        id, starting_equity, cash, fee_rate, slippage_pct,
                        contract_multiplier, realized_pnl, fees_paid,
                        order_counter, position_counter, trade_counter, updated_at
                    ) VALUES (1, ?, ?, ?, ?, ?, 0.0, 0.0, 0, 0, 0, ?);
                    """,
                    (
                        float(starting_equity),
                        float(cash),
                        float(fee_rate),
                        float(slippage_pct),
                        float(contract_multiplier),
                        now_iso,
                    ),
                )
        except sqlite3.DatabaseError as err:
            raise TransactionError(f"Failed to record initial portfolio state: {err}") from err

    def record_order_fill(
        self,
        order: PaperOrder,
        position: PaperPosition,
        key: str,
        canonical_payload: tuple[Any, ...],
        cash: float,
        fees_paid: float,
        order_counter: int,
        position_counter: int,
    ) -> None:
        """
        Atomically persist:
          1. New PaperOrder
          2. New PaperPosition
          3. Idempotency record with canonical payload
          4. Updated paper_portfolio_state (cash, fees, counters)
        Rolls back completely if any insert or update fails.
        """
        conn = self.get_connection()
        try:
            with conn:
                cursor = conn.cursor()

                # 1. Insert PaperOrder
                order_meta = json.dumps(dict(order.metadata))
                created_iso = order.created_at.isoformat()
                filled_iso = order.filled_at.isoformat() if order.filled_at else None

                cursor.execute(
                    """
                    INSERT INTO paper_orders (
                        order_id, request_id, idempotency_key, symbol, direction,
                        order_type, quantity, requested_price, fill_price, state,
                        fee, created_at, filled_at, is_paper, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        order.order_id,
                        order.request_id,
                        order.idempotency_key,
                        order.symbol,
                        order.direction.value,
                        order.order_type.value,
                        order.quantity,
                        order.requested_price,
                        order.fill_price,
                        order.state.value,
                        order.fee,
                        created_iso,
                        filled_iso,
                        1 if order.is_paper else 0,
                        order_meta,
                    ),
                )

                # 2. Insert PaperPosition
                pos_meta = json.dumps(dict(position.metadata))
                entry_iso = position.entry_time.isoformat()

                cursor.execute(
                    """
                    INSERT INTO paper_positions (
                        position_id, order_id, symbol, direction, entry_price,
                        quantity, contract_multiplier, notional, required_margin,
                        leverage, entry_time, stop_loss_price, take_profit_price,
                        entry_fee, unrealized_pnl, is_closed, is_paper, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?);
                    """,
                    (
                        position.position_id,
                        position.order_id,
                        position.symbol,
                        position.direction.value,
                        position.entry_price,
                        position.quantity,
                        position.contract_multiplier,
                        position.notional,
                        position.required_margin,
                        position.leverage,
                        entry_iso,
                        position.stop_loss_price,
                        position.take_profit_price,
                        position.entry_fee,
                        position.unrealized_pnl,
                        1 if position.is_paper else 0,
                        pos_meta,
                    ),
                )

                # 3. Insert Idempotency Record
                canon_json = json.dumps(list(canonical_payload))
                now_iso = datetime.now(timezone.utc).isoformat()

                cursor.execute(
                    """
                    INSERT INTO idempotency_records (
                        idempotency_key, canonical_payload, order_id, position_id, created_at
                    ) VALUES (?, ?, ?, ?, ?);
                    """,
                    (
                        key,
                        canon_json,
                        order.order_id,
                        position.position_id,
                        now_iso,
                    ),
                )

                # 4. Update Portfolio State
                cursor.execute(
                    """
                    UPDATE paper_portfolio_state
                    SET cash = ?,
                        fees_paid = ?,
                        order_counter = ?,
                        position_counter = ?,
                        updated_at = ?
                    WHERE id = 1;
                    """,
                    (
                        float(cash),
                        float(fees_paid),
                        int(order_counter),
                        int(position_counter),
                        now_iso,
                    ),
                )

        except sqlite3.DatabaseError as err:
            raise TransactionError(f"Atomic record_order_fill failed; rolled back: {err}") from err

    def record_position_close(
        self,
        trade: PaperTrade,
        position: PaperPosition,
        is_full_close: bool,
        cash: float,
        realized_pnl: float,
        fees_paid: float,
        trade_counter: int,
        updated_position: PaperPosition | None = None,
    ) -> None:
        """
        Atomically persist:
          1. New PaperTrade
          2. Updated or closed PaperPosition
          3. Updated paper_portfolio_state (cash, realized_pnl, fees, trade_counter)
        Rolls back completely if any step fails.
        """
        conn = self.get_connection()
        try:
            with conn:
                cursor = conn.cursor()

                # 1. Insert PaperTrade
                trade_meta = json.dumps(dict(trade.metadata))
                entry_iso = trade.entry_time.isoformat()
                exit_iso = trade.exit_time.isoformat()

                cursor.execute(
                    """
                    INSERT INTO paper_trades (
                        trade_id, position_id, order_id, symbol, direction,
                        strategy_id, entry_price, exit_price, quantity, contract_multiplier,
                        entry_time, exit_time, gross_pnl, entry_fee, exit_fee,
                        net_pnl, exit_reason, is_paper, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        trade.trade_id,
                        trade.position_id,
                        trade.order_id,
                        trade.symbol,
                        trade.direction.value,
                        trade.strategy_id,
                        trade.entry_price,
                        trade.exit_price,
                        trade.quantity,
                        trade.contract_multiplier,
                        entry_iso,
                        exit_iso,
                        trade.gross_pnl,
                        trade.entry_fee,
                        trade.exit_fee,
                        trade.net_pnl,
                        trade.exit_reason,
                        1 if trade.is_paper else 0,
                        trade_meta,
                    ),
                )

                # 2. Update Position
                if is_full_close:
                    cursor.execute(
                        """
                        UPDATE paper_positions
                        SET is_closed = 1,
                            quantity = 0.0,
                            notional = 0.0,
                            required_margin = 0.0,
                            unrealized_pnl = 0.0
                        WHERE position_id = ?;
                        """,
                        (position.position_id,),
                    )
                else:
                    if updated_position is None:
                        raise ValueError("updated_position must be provided for partial close")

                    cursor.execute(
                        """
                        UPDATE paper_positions
                        SET quantity = ?,
                            notional = ?,
                            required_margin = ?,
                            entry_fee = ?,
                            unrealized_pnl = ?
                        WHERE position_id = ?;
                        """,
                        (
                            updated_position.quantity,
                            updated_position.notional,
                            updated_position.required_margin,
                            updated_position.entry_fee,
                            updated_position.unrealized_pnl,
                            position.position_id,
                        ),
                    )

                # 3. Update Portfolio State
                now_iso = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    """
                    UPDATE paper_portfolio_state
                    SET cash = ?,
                        realized_pnl = ?,
                        fees_paid = ?,
                        trade_counter = ?,
                        updated_at = ?
                    WHERE id = 1;
                    """,
                    (
                        float(cash),
                        float(realized_pnl),
                        float(fees_paid),
                        int(trade_counter),
                        now_iso,
                    ),
                )

        except sqlite3.DatabaseError as err:
            raise TransactionError(f"Atomic record_position_close failed; rolled back: {err}") from err

    def load_ledger_state(self) -> RecoveredLedgerState | None:
        """
        Reconstruct the logical paper trading ledger from persistent storage.
        Returns None if no initialized portfolio state exists.
        Fails closed on corrupt or unreadable data.
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()

            # 1. Load Portfolio State
            cursor.execute("SELECT * FROM paper_portfolio_state WHERE id = 1;")
            state_row = cursor.fetchone()
            if state_row is None:
                return None

            starting_equity = float(state_row["starting_equity"])
            cash = float(state_row["cash"])
            fee_rate = float(state_row["fee_rate"])
            slippage_pct = float(state_row["slippage_pct"])
            contract_multiplier = float(state_row["contract_multiplier"])
            realized_pnl = float(state_row["realized_pnl"])
            fees_paid = float(state_row["fees_paid"])
            order_counter = int(state_row["order_counter"])
            position_counter = int(state_row["position_counter"])
            trade_counter = int(state_row["trade_counter"])
            updated_at = datetime.fromisoformat(state_row["updated_at"])

            # 2. Load Orders
            cursor.execute("SELECT * FROM paper_orders ORDER BY created_at ASC;")
            order_rows = cursor.fetchall()
            orders: dict[str, PaperOrder] = {}
            for row in order_rows:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
                filled_at = datetime.fromisoformat(row["filled_at"]) if row["filled_at"] else None
                order = PaperOrder(
                    order_id=row["order_id"],
                    request_id=row["request_id"],
                    idempotency_key=row["idempotency_key"],
                    symbol=row["symbol"],
                    direction=SignalDirection(row["direction"]),
                    order_type=OrderType(row["order_type"]),
                    quantity=float(row["quantity"]),
                    requested_price=float(row["requested_price"]),
                    fill_price=float(row["fill_price"]),
                    state=OrderState(row["state"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    filled_at=filled_at,
                    fee=float(row["fee"]),
                    is_paper=bool(row["is_paper"]),
                    metadata=meta,
                )
                orders[order.order_id] = order

            # 3. Load Active (Open) Positions only
            cursor.execute("SELECT * FROM paper_positions WHERE is_closed = 0 ORDER BY entry_time ASC;")
            pos_rows = cursor.fetchall()
            open_positions: dict[str, PaperPosition] = {}
            for row in pos_rows:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
                tp = float(row["take_profit_price"]) if row["take_profit_price"] is not None else None
                pos = PaperPosition(
                    position_id=row["position_id"],
                    order_id=row["order_id"],
                    symbol=row["symbol"],
                    direction=SignalDirection(row["direction"]),
                    entry_price=float(row["entry_price"]),
                    quantity=float(row["quantity"]),
                    contract_multiplier=float(row["contract_multiplier"]),
                    notional=float(row["notional"]),
                    required_margin=float(row["required_margin"]),
                    leverage=float(row["leverage"]),
                    entry_time=datetime.fromisoformat(row["entry_time"]),
                    stop_loss_price=float(row["stop_loss_price"]),
                    take_profit_price=tp,
                    entry_fee=float(row["entry_fee"]),
                    unrealized_pnl=float(row["unrealized_pnl"]),
                    is_closed=False,
                    is_paper=bool(row["is_paper"]),
                    metadata=meta,
                )
                open_positions[pos.position_id] = pos

            # 4. Load Closed Trades
            cursor.execute("SELECT * FROM paper_trades ORDER BY exit_time ASC;")
            trade_rows = cursor.fetchall()
            closed_trades: list[PaperTrade] = []
            for row in trade_rows:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
                trade = PaperTrade(
                    trade_id=row["trade_id"],
                    position_id=row["position_id"],
                    order_id=row["order_id"],
                    symbol=row["symbol"],
                    direction=SignalDirection(row["direction"]),
                    strategy_id=row["strategy_id"],
                    entry_price=float(row["entry_price"]),
                    exit_price=float(row["exit_price"]),
                    quantity=float(row["quantity"]),
                    contract_multiplier=float(row["contract_multiplier"]),
                    entry_time=datetime.fromisoformat(row["entry_time"]),
                    exit_time=datetime.fromisoformat(row["exit_time"]),
                    gross_pnl=float(row["gross_pnl"]),
                    entry_fee=float(row["entry_fee"]),
                    exit_fee=float(row["exit_fee"]),
                    net_pnl=float(row["net_pnl"]),
                    exit_reason=row["exit_reason"],
                    is_paper=bool(row["is_paper"]),
                    metadata=meta,
                )
                closed_trades.append(trade)

            # 5. Load Idempotency Records
            cursor.execute("SELECT * FROM idempotency_records;")
            idem_rows = cursor.fetchall()
            idempotency_store: dict[str, tuple[Any, ...]] = {}
            idempotency_order_map: dict[str, str] = {}
            idempotency_pos_map: dict[str, str] = {}

            for row in idem_rows:
                k = row["idempotency_key"]
                raw_payload = json.loads(row["canonical_payload"])
                idempotency_store[k] = tuple(raw_payload)
                if row["order_id"]:
                    idempotency_order_map[k] = row["order_id"]
                if row["position_id"]:
                    idempotency_pos_map[k] = row["position_id"]

            return RecoveredLedgerState(
                starting_equity=starting_equity,
                cash=cash,
                fee_rate=fee_rate,
                slippage_pct=slippage_pct,
                contract_multiplier=contract_multiplier,
                realized_pnl=realized_pnl,
                fees_paid=fees_paid,
                order_counter=order_counter,
                position_counter=position_counter,
                trade_counter=trade_counter,
                open_positions=open_positions,
                closed_trades=closed_trades,
                orders=orders,
                idempotency_store=idempotency_store,
                idempotency_order_map=idempotency_order_map,
                idempotency_pos_map=idempotency_pos_map,
                updated_at=updated_at,
            )

        except (IncompatibleSchemaError, DatabaseCorruptionError):
            raise
        except sqlite3.DatabaseError as err:
            raise DatabaseCorruptionError(f"Corrupted or invalid database records in '{self.db_path}': {err}") from err
        except Exception as err:
            raise PersistenceError(f"Failed to load ledger state from '{self.db_path}': {err}") from err
