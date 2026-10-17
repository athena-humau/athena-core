from datetime import datetime, timezone
from typing import Any, Mapping

from execution.base import ExecutionEngine
from execution.models import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    OrderState,
)
from risk.models import RiskAssessment, RiskVerdict
from strategy.models import SignalDirection


class FoundationOnlyExecutionEngine(ExecutionEngine):
    """
    Reference non-executing Execution Engine implementation for Sprint 29.

    Responsibilities:
      1. Validates ExecutionRequest structure and data types.
      2. Enforces Risk Assessment provenance (PASS verdict, matching symbol/strategy/direction/limits).
      3. Rejects non-executable directions (FLAT).
      4. Enforces in-memory idempotency (duplicate detection and conflict rejection).
      5. Strictly reports NOT_EXECUTED status without attempting execution or producing side effects.
    """

    engine_id: str = "foundation_only_execution_engine"
    name: str = "Foundation Only Execution Engine"
    description: str = "Contract-only non-executing reference implementation for ATHENA Sprint 29"

    def __init__(self, engine_id: str | None = None):
        super().__init__(engine_id=engine_id or self.engine_id)
        self._idempotency_store: dict[str, tuple[Any, ...]] = {}

    def clear_idempotency_cache(self) -> None:
        """Clear in-memory idempotency registry (primarily for isolated test fixtures)."""
        self._idempotency_store.clear()

    def execute(self, request: Any) -> ExecutionResult:
        """
        Evaluate an execution request against lifecycle, provenance, and idempotency rules.
        Strictly contract-only: does not transmit orders or communicate with exchanges.
        """
        # 1. Request presence check
        if request is None:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id="",
                idempotency_key="",
                symbol="",
                strategy_id="",
                direction=None,
                requested_quantity=0.0,
                requested_notional=0.0,
                reason_codes=("MISSING_EXECUTION_REQUEST",),
                explanation="Execution request is None",
                audit_evidence={"request": {"status": "FAIL", "reason": "Request is None"}},
                request=None,
            )

        if not isinstance(request, ExecutionRequest):
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id="",
                idempotency_key="",
                symbol="",
                strategy_id="",
                direction=None,
                requested_quantity=0.0,
                requested_notional=0.0,
                reason_codes=("INVALID_REQUEST_TYPE",),
                explanation=f"Expected ExecutionRequest, got {type(request).__name__}",
                audit_evidence={"request": {"status": "FAIL", "reason": f"Expected ExecutionRequest, got {type(request).__name__}"}},
                request=None,
            )

        # 2. Identifiers check
        if not request.request_id or not request.request_id.strip():
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id="",
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("MISSING_REQUEST_ID",),
                explanation="ExecutionRequest request_id is empty or missing",
                audit_evidence={"identifiers": {"status": "FAIL", "reason": "Empty request_id"}},
                request=request,
            )

        if not request.idempotency_key or not request.idempotency_key.strip():
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key="",
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("MISSING_IDEMPOTENCY_KEY",),
                explanation="ExecutionRequest idempotency_key is empty or missing",
                audit_evidence={"identifiers": {"status": "FAIL", "reason": "Empty idempotency_key"}},
                request=request,
            )

        # 3. Risk assessment presence and validity check
        risk_assessment = request.risk_assessment
        if risk_assessment is None:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("MISSING_RISK_ASSESSMENT",),
                explanation="Execution request lacks an associated RiskAssessment",
                audit_evidence={"risk_provenance": {"status": "FAIL", "reason": "RiskAssessment is None"}},
                request=request,
            )

        if not isinstance(risk_assessment, RiskAssessment):
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("INVALID_RISK_ASSESSMENT_TYPE",),
                explanation=f"Expected RiskAssessment, got {type(risk_assessment).__name__}",
                audit_evidence={"risk_provenance": {"status": "FAIL", "reason": f"Expected RiskAssessment, got {type(risk_assessment).__name__}"}},
                request=request,
            )

        if risk_assessment.verdict != RiskVerdict.PASS:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("RISK_VERDICT_REJECTED",),
                explanation=f"Associated RiskAssessment verdict was {risk_assessment.verdict.value}, not PASS",
                audit_evidence={
                    "risk_provenance": {
                        "status": "FAIL",
                        "verdict": risk_assessment.verdict.value,
                        "risk_reasons": list(risk_assessment.reason_codes),
                    }
                },
                request=request,
            )

        if risk_assessment.proposal is None:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("MISSING_RISK_PROPOSAL",),
                explanation="Associated RiskAssessment does not contain an approved TradeProposal",
                audit_evidence={"risk_provenance": {"status": "FAIL", "reason": "TradeProposal is None"}},
                request=request,
            )

        # 4. Provenance consistency checks
        if request.symbol != risk_assessment.symbol:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("SYMBOL_MISMATCH",),
                explanation=f"Request symbol '{request.symbol}' does not match RiskAssessment symbol '{risk_assessment.symbol}'",
                audit_evidence={"provenance": {"status": "FAIL", "request_symbol": request.symbol, "risk_symbol": risk_assessment.symbol}},
                request=request,
            )

        if request.strategy_id != risk_assessment.strategy_id:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("STRATEGY_MISMATCH",),
                explanation=f"Request strategy '{request.strategy_id}' does not match RiskAssessment strategy '{risk_assessment.strategy_id}'",
                audit_evidence={"provenance": {"status": "FAIL", "request_strategy": request.strategy_id, "risk_strategy": risk_assessment.strategy_id}},
                request=request,
            )

        if request.direction != risk_assessment.direction:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("DIRECTION_MISMATCH",),
                explanation=f"Request direction '{request.direction}' does not match RiskAssessment direction '{risk_assessment.direction}'",
                audit_evidence={"provenance": {"status": "FAIL", "request_direction": str(request.direction), "risk_direction": str(risk_assessment.direction)}},
                request=request,
            )

        if request.direction == SignalDirection.FLAT:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("FLAT_DIRECTION_NOT_EXECUTABLE",),
                explanation="FLAT signals are non-executable and cannot produce order execution",
                audit_evidence={"direction": {"status": "FAIL", "direction": "FLAT"}},
                request=request,
            )

        if request.direction not in (SignalDirection.LONG, SignalDirection.SHORT):
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("INVALID_DIRECTION",),
                explanation=f"Direction {request.direction} is invalid",
                audit_evidence={"direction": {"status": "FAIL", "direction": str(request.direction)}},
                request=request,
            )

        # 5. Price & Quantity validity check
        if request.quantity <= 0.0 or request.notional <= 0.0 or request.reference_price <= 0.0:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("INVALID_QUANTITY_OR_PRICE",),
                explanation="Requested quantity, notional, and reference price must be strictly positive",
                audit_evidence={
                    "sizing": {
                        "status": "FAIL",
                        "quantity": request.quantity,
                        "notional": request.notional,
                        "reference_price": request.reference_price,
                    }
                },
                request=request,
            )

        # 6. Upper bounds against risk-approved proposal
        if request.quantity > risk_assessment.proposal.quantity + 1e-9:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("QUANTITY_EXCEEDS_RISK_LIMIT",),
                explanation=(
                    f"Requested quantity ({request.quantity}) exceeds risk-approved "
                    f"proposal quantity ({risk_assessment.proposal.quantity})"
                ),
                audit_evidence={
                    "limits": {
                        "status": "FAIL",
                        "requested_quantity": request.quantity,
                        "approved_quantity": risk_assessment.proposal.quantity,
                    }
                },
                request=request,
            )

        if request.notional > risk_assessment.notional + 1e-9:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("NOTIONAL_EXCEEDS_RISK_LIMIT",),
                explanation=(
                    f"Requested notional ({request.notional}) exceeds risk-approved "
                    f"notional ({risk_assessment.notional})"
                ),
                audit_evidence={
                    "limits": {
                        "status": "FAIL",
                        "requested_notional": request.notional,
                        "approved_notional": risk_assessment.notional,
                    }
                },
                request=request,
            )

        if request.risk_policy_id != risk_assessment.policy_id:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                order_state=OrderState.REJECTED,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                strategy_id=request.strategy_id,
                direction=request.direction,
                requested_quantity=request.quantity,
                requested_notional=request.notional,
                reason_codes=("RISK_POLICY_MISMATCH",),
                explanation=f"Request risk policy '{request.risk_policy_id}' does not match RiskAssessment policy '{risk_assessment.policy_id}'",
                audit_evidence={"policy": {"status": "FAIL", "request_policy": request.risk_policy_id, "assessment_policy": risk_assessment.policy_id}},
                request=request,
            )

        # 7. Idempotency evaluation
        canonical = request.canonical_payload()
        key = request.idempotency_key

        if key in self._idempotency_store:
            stored_canonical = self._idempotency_store[key]
            if stored_canonical == canonical:
                return ExecutionResult(
                    status=ExecutionStatus.DUPLICATE,
                    order_state=OrderState.VALIDATED,
                    request_id=request.request_id,
                    idempotency_key=key,
                    symbol=request.symbol,
                    strategy_id=request.strategy_id,
                    direction=request.direction,
                    requested_quantity=request.quantity,
                    requested_notional=request.notional,
                    side_effect_occurred=False,
                    execution_attempted=False,
                    exchange_order_id=None,
                    filled_quantity=0.0,
                    execution_price=None,
                    reason_codes=("DUPLICATE_REQUEST_IGNORED",),
                    explanation=f"Duplicate execution request with idempotency key '{key}' recognized and ignored",
                    audit_evidence={
                        "idempotency": {
                            "status": "DUPLICATE",
                            "key": key,
                            "match": True,
                        }
                    },
                    request=request,
                )
            else:
                return ExecutionResult(
                    status=ExecutionStatus.CONFLICT,
                    order_state=OrderState.REJECTED,
                    request_id=request.request_id,
                    idempotency_key=key,
                    symbol=request.symbol,
                    strategy_id=request.strategy_id,
                    direction=request.direction,
                    requested_quantity=request.quantity,
                    requested_notional=request.notional,
                    side_effect_occurred=False,
                    execution_attempted=False,
                    exchange_order_id=None,
                    filled_quantity=0.0,
                    execution_price=None,
                    reason_codes=("IDEMPOTENCY_CONFLICT",),
                    explanation=f"Conflicting request content submitted with existing idempotency key '{key}'",
                    audit_evidence={
                        "idempotency": {
                            "status": "CONFLICT",
                            "key": key,
                            "match": False,
                        }
                    },
                    request=request,
                )

        # Register key in in-memory store
        self._idempotency_store[key] = canonical

        # 8. Return successful validation result (non-executing reference foundation)
        return ExecutionResult(
            status=ExecutionStatus.NOT_EXECUTED,
            order_state=OrderState.VALIDATED,
            request_id=request.request_id,
            idempotency_key=key,
            symbol=request.symbol,
            strategy_id=request.strategy_id,
            direction=request.direction,
            requested_quantity=request.quantity,
            requested_notional=request.notional,
            side_effect_occurred=False,
            execution_attempted=False,
            exchange_order_id=None,
            filled_quantity=0.0,
            execution_price=None,
            reason_codes=("NOT_EXECUTED_FOUNDATION_ONLY",),
            explanation="Execution request validated successfully. Order execution is deferred (Sprint 29 foundation only).",
            audit_evidence={
                "provenance": {"status": "PASS", "risk_policy_id": request.risk_policy_id},
                "limits": {"status": "PASS", "quantity": request.quantity, "notional": request.notional},
                "idempotency": {"status": "REGISTERED", "key": key},
                "safety_lock": {"status": "LOCKED", "side_effect_occurred": False, "execution_attempted": False},
            },
            request=request,
        )


# Backward-compatible alias
DeterministicExecutionEngine = FoundationOnlyExecutionEngine
