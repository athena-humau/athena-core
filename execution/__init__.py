"""
ATHENA Execution Package.

Provides the foundational execution request contracts, order lifecycle models,
idempotency protection, and non-executing reference execution engine.

NOTE: Sprint 29 establishes safety contracts and non-executing foundation only.
It does NOT submit live exchange orders, execute paper trades, or access private exchange APIs.
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
    "TERMINAL_STATES",
    "is_valid_transition",
    "validate_transition",
]
