"""
ATHENA Execution Package.

Provides the foundational execution request contracts, order lifecycle models,
idempotency protection, non-executing reference engine, and the simulated
Paper Trading Engine (Sprint 30).

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

__all__ = [
    "BaseExecutionEngine",
    "DeterministicExecutionEngine",
    "ExecutionEngine",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "FoundationOnlyExecutionEngine",
    "IllegalOrderStateTransitionError",
    "LEGAL_TRANSITIONS",
    "OrderState",
    "OrderType",
    "PaperExecutionResult",
    "PaperOrder",
    "PaperPortfolio",
    "PaperPosition",
    "PaperTrade",
    "PaperTradingEngine",
    "TERMINAL_STATES",
    "is_valid_transition",
    "validate_transition",
]
