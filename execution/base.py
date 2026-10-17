from abc import ABC, abstractmethod
from typing import Any

from execution.models import ExecutionResult


class ExecutionEngine(ABC):
    """
    Abstract Base Class for Execution Engines in ATHENA.
    Evaluates validated ExecutionRequests and produces an immutable ExecutionResult.

    In Sprint 29, execution engines are strictly non-executing and establish safety contracts only.
    Must NOT submit live exchange orders, simulate fills, or connect to private trading APIs.
    """

    engine_id: str = ""
    name: str = ""
    description: str = ""

    def __init__(self, engine_id: str | None = None):
        if engine_id is not None:
            self.engine_id = engine_id

        if not isinstance(self.engine_id, str) or not self.engine_id.strip():
            raise ValueError(f"ExecutionEngine {self.__class__.__name__} must define a non-empty engine_id")

    @abstractmethod
    def execute(self, request: Any) -> ExecutionResult:
        """
        Evaluate an execution request and return an immutable ExecutionResult.
        """
        pass


# Backward-compatible alias
BaseExecutionEngine = ExecutionEngine
