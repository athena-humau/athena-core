"""
ATHENA Execution Package.

Provides the foundational execution request contracts, order lifecycle models,
idempotency protection, non-executing reference engine, the simulated
Paper Trading Engine (Sprint 30), and the end-to-end Paper Trading Workflow (Sprint 31).

NOTE: All paper trading operations are strictly simulated and in-memory.
They do NOT submit live exchange orders, execute real trades, or access private exchange APIs.
"""

from execution.base import BaseExecutionEngine, ExecutionEngine
from execution.deterministic_engine import (
    DeterministicExecutionEngine,
    FoundationOnlyExecutionEngine,
)
from execution.models import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    IllegalOrderStateTransitionError,
    OrderState,
    OrderType,
    is_valid_transition,
    validate_transition,
)
from execution.paper_engine import PaperTradingEngine
from execution.paper_models import (
    PaperExecutionResult,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    PaperTrade,
)
from execution.paper_persistence import (
    DatabaseCorruptionError,
    IncompatibleSchemaError,
    PaperStorage,
    PersistenceError,
    RecoveredLedgerState,
    TransactionError,
)
from execution.paper_workflow import (
    CandidateProcessingStatus,
    CandidateWorkflowResult,
    PaperTradingWorkflow,
)

__all__ = [
    "BaseExecutionEngine",
    "CandidateProcessingStatus",
    "CandidateWorkflowResult",
    "DatabaseCorruptionError",
    "DeterministicExecutionEngine",
    "ExecutionEngine",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "FoundationOnlyExecutionEngine",
    "IllegalOrderStateTransitionError",
    "IncompatibleSchemaError",
    "LEGAL_TRANSITIONS",
    "OrderState",
    "OrderType",
    "PaperExecutionResult",
    "PaperOrder",
    "PaperPortfolio",
    "PaperPosition",
    "PaperStorage",
    "PaperTrade",
    "PaperTradingEngine",
    "PaperTradingWorkflow",
    "PersistenceError",
    "RecoveredLedgerState",
    "TERMINAL_STATES",
    "TransactionError",
    "is_valid_transition",
    "validate_transition",
]
