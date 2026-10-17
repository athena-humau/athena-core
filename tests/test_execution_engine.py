from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import MappingProxyType
import unittest

from ai.models import (
    JudgeDecision,
    JudgeVerdict,
    ValidatedCandidate,
    ValidationStatus,
)
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
from risk.deterministic_engine import DeterministicRiskEngine
from risk.models import (
    RiskAssessment,
    RiskPolicy,
    RiskVerdict,
    TradeProposal,
)
from strategy.models import Signal, SignalDirection


def make_test_passing_risk_assessment(
    symbol: str = "BTCUSDT",
    strategy_id: str = "ema_trend_following",
    direction: SignalDirection = SignalDirection.LONG,
    entry_price: float = 50000.0,
    stop_loss_price: float = 49000.0,
    quantity: float = 0.1,
    equity: float = 10000.0,
    policy_id: str = "deterministic_risk_v1",
) -> RiskAssessment:
    sig = Signal(
        symbol=symbol,
        direction=direction,
        strategy_id=strategy_id,
        timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
        confidence=0.85,
        reason="Test candidate signal",
    )
    cand = ValidatedCandidate(
        status=ValidationStatus.PASSED,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=0.85,
        signal=sig,
        failures=(),
    )
    judge = JudgeDecision(
        verdict=JudgeVerdict.PASS,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=0.85,
        score=0.80,
        reason_codes=("CRITERIA_SATISFIED",),
        explanation="Judge passed",
        candidate=cand,
    )
    proposal = TradeProposal(
        judge_decision=judge,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        quantity=quantity,
        account_equity=equity,
        available_margin=5000.0,
        requested_leverage=2.0,
    )
    policy = RiskPolicy(
        policy_id=policy_id,
        max_risk_per_trade_pct=2.0,
        max_position_notional=10000.0,
        max_aggregate_exposure=25000.0,
        max_leverage=10.0,
    )
    risk_engine = DeterministicRiskEngine(policy=policy)
    return risk_engine.assess(proposal)


def make_test_rejected_risk_assessment(
    symbol: str = "BTCUSDT",
    strategy_id: str = "ema_trend_following",
    direction: SignalDirection = SignalDirection.LONG,
) -> RiskAssessment:
    sig = Signal(
        symbol=symbol,
        direction=direction,
        strategy_id=strategy_id,
        timestamp=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
        confidence=0.50,
        reason="Test rejected signal",
    )
    cand = ValidatedCandidate(
        status=ValidationStatus.FAILED,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=0.50,
        signal=sig,
        failures=("REGIME_CONTRADICTION",),
    )
    judge = JudgeDecision(
        verdict=JudgeVerdict.REJECT,
        symbol=symbol,
        strategy_id=strategy_id,
        direction=direction,
        confidence=0.50,
        score=0.30,
        reason_codes=("REGIME_CONTRADICTION",),
        explanation="Judge rejected",
        candidate=cand,
    )
    proposal = TradeProposal(
        judge_decision=judge,
        entry_price=50000.0,
        stop_loss_price=49000.0,
        quantity=0.1,
        account_equity=10000.0,
    )
    policy = RiskPolicy(policy_id="deterministic_risk_v1")
    risk_engine = DeterministicRiskEngine(policy=policy)
    return risk_engine.assess(proposal)


class TestExecutionEngineFoundation(unittest.TestCase):
    def setUp(self):
        self.engine = FoundationOnlyExecutionEngine()
        self.engine.clear_idempotency_cache()
        self.passing_risk = make_test_passing_risk_assessment()

    # --- 1. MODEL INTEGRITY & IMMUTABILITY (V, X, Y) ---

    def test_execution_request_immutability(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_001",
            idempotency_key="idem_001",
            metadata={"source": "signal_pipeline"},
        )
        self.assertEqual(req.request_id, "req_001")
        self.assertEqual(req.idempotency_key, "idem_001")
        self.assertEqual(req.symbol, "BTCUSDT")
        self.assertEqual(req.direction, SignalDirection.LONG)
        self.assertIsInstance(req.metadata, MappingProxyType)

        with self.assertRaises((FrozenInstanceError, AttributeError)):
            req.quantity = 0.5

        with self.assertRaises(TypeError):
            req.metadata["tamper"] = True

    def test_execution_result_immutability_and_safety_locks(self):
        result = ExecutionResult(
            status=ExecutionStatus.NOT_EXECUTED,
            order_state=OrderState.VALIDATED,
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            requested_quantity=0.1,
            requested_notional=5000.0,
            side_effect_occurred=False,
            execution_attempted=False,
            exchange_order_id=None,
            filled_quantity=0.0,
            execution_price=None,
            reason_codes=("NOT_EXECUTED_FOUNDATION_ONLY",),
            explanation="Safety foundation only",
        )
        self.assertFalse(result.is_executed)
        self.assertFalse(result.side_effect_occurred)
        self.assertFalse(result.execution_attempted)
        self.assertIsNone(result.exchange_order_id)
        self.assertEqual(result.filled_quantity, 0.0)
        self.assertIsNone(result.execution_price)

        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result.status = ExecutionStatus.NOT_EXECUTED

        # Safety Lock: Disallows claiming side effect or execution attempted
        with self.assertRaises(ValueError):
            ExecutionResult(
                status=ExecutionStatus.NOT_EXECUTED,
                order_state=OrderState.VALIDATED,
                request_id="req_001",
                idempotency_key="idem_001",
                symbol="BTCUSDT",
                strategy_id="ema_trend_following",
                direction=SignalDirection.LONG,
                requested_quantity=0.1,
                requested_notional=5000.0,
                side_effect_occurred=True,
            )

        # Safety Lock: Disallows fabricating order IDs or fills
        with self.assertRaises(ValueError):
            ExecutionResult(
                status=ExecutionStatus.NOT_EXECUTED,
                order_state=OrderState.VALIDATED,
                request_id="req_001",
                idempotency_key="idem_001",
                symbol="BTCUSDT",
                strategy_id="ema_trend_following",
                direction=SignalDirection.LONG,
                requested_quantity=0.1,
                requested_notional=5000.0,
                exchange_order_id="fake_order_123",
            )

    def test_original_risk_assessment_remains_unchanged(self):
        original_notional = self.passing_risk.notional
        original_verdict = self.passing_risk.verdict
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_001",
            idempotency_key="idem_001",
        )
        res = self.engine.execute(req)
        self.assertEqual(self.passing_risk.notional, original_notional)
        self.assertEqual(self.passing_risk.verdict, original_verdict)
        self.assertIs(req.risk_assessment, self.passing_risk)

    # --- 2. ENGINE CONTRACT & ALIASES ---

    def test_engine_abstract_contract_and_alias(self):
        self.assertIs(BaseExecutionEngine, ExecutionEngine)
        self.assertIs(DeterministicExecutionEngine, FoundationOnlyExecutionEngine)

        with self.assertRaises(TypeError):
            ExecutionEngine()

        class IncompleteEngine(ExecutionEngine):
            pass

        with self.assertRaises(TypeError):
            IncompleteEngine()

    # --- 3. PROVENANCE & INPUT VALIDATION (A, B, C, D, E, F, G, H, I, J, M, N, O) ---

    def test_valid_risk_assessment_pass_produces_validated_request(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_001",
            idempotency_key="idem_001",
        )
        self.assertEqual(req.symbol, "BTCUSDT")
        self.assertEqual(req.strategy_id, "ema_trend_following")
        self.assertEqual(req.direction, SignalDirection.LONG)
        self.assertAlmostEqual(req.quantity, 0.1, places=4)
        self.assertAlmostEqual(req.notional, 5000.0, places=4)

        result = self.engine.execute(req)
        self.assertEqual(result.status, ExecutionStatus.NOT_EXECUTED)
        self.assertEqual(result.order_state, OrderState.VALIDATED)
        self.assertEqual(result.reason_codes, ("NOT_EXECUTED_FOUNDATION_ONLY",))
        self.assertFalse(result.is_executed)
        self.assertFalse(result.side_effect_occurred)

    def test_risk_assessment_reject_cannot_produce_accepted_request(self):
        rejected_risk = make_test_rejected_risk_assessment()
        # Factory raises ValueError
        with self.assertRaises(ValueError):
            ExecutionRequest.from_risk_assessment(
                risk_assessment=rejected_risk,
                request_id="req_001",
                idempotency_key="idem_001",
            )

        # Direct instantiation with rejected risk is rejected by engine
        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=rejected_risk,
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("RISK_VERDICT_REJECTED", result.reason_codes)

    def test_missing_risk_assessment_fails_safely(self):
        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=None,
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("MISSING_RISK_ASSESSMENT", result.reason_codes)

    def test_invalid_risk_assessment_type_fails(self):
        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment="not_a_risk_assessment",
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("INVALID_RISK_ASSESSMENT_TYPE", result.reason_codes)

    def test_symbol_mismatch_is_rejected(self):
        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="ETHUSDT",  # Mismatch with BTCUSDT
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=self.passing_risk,
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("SYMBOL_MISMATCH", result.reason_codes)

    def test_strategy_mismatch_is_rejected(self):
        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="rsi_mean_reversion",  # Mismatch with ema_trend_following
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=self.passing_risk,
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("STRATEGY_MISMATCH", result.reason_codes)

    def test_direction_mismatch_is_rejected(self):
        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.SHORT,  # Mismatch with LONG
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=self.passing_risk,
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("DIRECTION_MISMATCH", result.reason_codes)

    def test_flat_direction_is_rejected_as_non_executable(self):
        # Even if a risk assessment theoretically had FLAT direction
        flat_risk = make_test_passing_risk_assessment(direction=SignalDirection.LONG)
        object.__setattr__(flat_risk, "direction", SignalDirection.FLAT)

        with self.assertRaises(ValueError):
            ExecutionRequest.from_risk_assessment(
                risk_assessment=flat_risk,
                request_id="req_001",
                idempotency_key="idem_001",
            )

        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.FLAT,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=flat_risk,
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("FLAT_DIRECTION_NOT_EXECUTABLE", result.reason_codes)

    def test_missing_request_id_and_idempotency_key_fail(self):
        with self.assertRaises(ValueError):
            ExecutionRequest(
                request_id="",
                idempotency_key="idem_001",
                symbol="BTCUSDT",
                strategy_id="ema_trend_following",
                direction=SignalDirection.LONG,
                quantity=0.1,
                notional=5000.0,
                reference_price=50000.0,
                risk_assessment=self.passing_risk,
            )

        with self.assertRaises(ValueError):
            ExecutionRequest(
                request_id="req_001",
                idempotency_key="",
                symbol="BTCUSDT",
                strategy_id="ema_trend_following",
                direction=SignalDirection.LONG,
                quantity=0.1,
                notional=5000.0,
                reference_price=50000.0,
                risk_assessment=self.passing_risk,
            )

    def test_quantity_exceeding_risk_approved_quantity_rejected(self):
        # Approved qty = 0.1
        with self.assertRaises(ValueError):
            ExecutionRequest.from_risk_assessment(
                risk_assessment=self.passing_risk,
                request_id="req_001",
                idempotency_key="idem_001",
                quantity=0.15,
            )

        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.15,  # Exceeds 0.1
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=self.passing_risk,
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("QUANTITY_EXCEEDS_RISK_LIMIT", result.reason_codes)

    def test_notional_exceeding_risk_approved_notional_rejected(self):
        # Approved notional = 5000.0
        with self.assertRaises(ValueError):
            ExecutionRequest.from_risk_assessment(
                risk_assessment=self.passing_risk,
                request_id="req_001",
                idempotency_key="idem_001",
                notional=6000.0,
            )

        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=6000.0,  # Exceeds 5000.0
            reference_price=50000.0,
            risk_assessment=self.passing_risk,
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("NOTIONAL_EXCEEDS_RISK_LIMIT", result.reason_codes)

    def test_missing_or_negative_quantity_notional_fails(self):
        invalid_values = [0.0, -1.0]
        for val in invalid_values:
            with self.assertRaises(ValueError):
                ExecutionRequest.from_risk_assessment(
                    risk_assessment=self.passing_risk,
                    request_id="req_001",
                    idempotency_key="idem_001",
                    quantity=val,
                )

        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.0,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=self.passing_risk,
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("INVALID_QUANTITY_OR_PRICE", result.reason_codes)

    def test_risk_policy_mismatch_fails(self):
        raw_req = ExecutionRequest(
            request_id="req_001",
            idempotency_key="idem_001",
            symbol="BTCUSDT",
            strategy_id="ema_trend_following",
            direction=SignalDirection.LONG,
            quantity=0.1,
            notional=5000.0,
            reference_price=50000.0,
            risk_assessment=self.passing_risk,
            risk_policy_id="rogue_policy_v99",  # Mismatch with deterministic_risk_v1
        )
        result = self.engine.execute(raw_req)
        self.assertTrue(result.is_rejected)
        self.assertIn("RISK_POLICY_MISMATCH", result.reason_codes)

    # --- 4. IDEMPOTENCY & DUPLICATE PROTECTION (J, K, L, AE) ---

    def test_identical_repeated_idempotency_key_recognized_as_duplicate(self):
        req1 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_001",
            idempotency_key="idem_unique_key_001",
        )
        req2 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_002",  # Different request_id but same idempotency_key & content
            idempotency_key="idem_unique_key_001",
        )

        res1 = self.engine.execute(req1)
        self.assertEqual(res1.status, ExecutionStatus.NOT_EXECUTED)

        res2 = self.engine.execute(req2)
        self.assertEqual(res2.status, ExecutionStatus.DUPLICATE)
        self.assertTrue(res2.is_duplicate)
        self.assertIn("DUPLICATE_REQUEST_IGNORED", res2.reason_codes)

    def test_conflicting_request_with_same_key_rejected(self):
        req1 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_001",
            idempotency_key="idem_conflict_key",
            quantity=0.1,
        )
        # Same key but different quantity (0.05) -> conflicting content!
        req2 = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_002",
            idempotency_key="idem_conflict_key",
            quantity=0.05,
        )

        res1 = self.engine.execute(req1)
        self.assertEqual(res1.status, ExecutionStatus.NOT_EXECUTED)

        res2 = self.engine.execute(req2)
        self.assertEqual(res2.status, ExecutionStatus.CONFLICT)
        self.assertTrue(res2.is_rejected)
        self.assertIn("IDEMPOTENCY_CONFLICT", res2.reason_codes)

    # --- 5. ORDER LIFECYCLE & STATE TRANSITIONS (P, Q, R, S) ---

    def test_valid_lifecycle_transitions(self):
        valid_transitions = [
            (OrderState.CREATED, OrderState.VALIDATED),
            (OrderState.CREATED, OrderState.REJECTED),
            (OrderState.CREATED, OrderState.FAILED),
            (OrderState.VALIDATED, OrderState.SUBMITTED),
            (OrderState.SUBMITTED, OrderState.ACKNOWLEDGED),
            (OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED),
            (OrderState.ACKNOWLEDGED, OrderState.FILLED),
            (OrderState.ACKNOWLEDGED, OrderState.CANCELLED),
            (OrderState.PARTIALLY_FILLED, OrderState.FILLED),
            (OrderState.UNKNOWN, OrderState.FAILED),
        ]
        for from_s, to_s in valid_transitions:
            self.assertTrue(is_valid_transition(from_s, to_s))
            validate_transition(from_s, to_s)  # Should not raise

    def test_illegal_lifecycle_transitions_fail(self):
        illegal_transitions = [
            (OrderState.FILLED, OrderState.CREATED),
            (OrderState.FILLED, OrderState.VALIDATED),
            (OrderState.REJECTED, OrderState.FILLED),
            (OrderState.CANCELLED, OrderState.SUBMITTED),
            (OrderState.FAILED, OrderState.ACKNOWLEDGED),
            (OrderState.UNKNOWN, OrderState.FILLED),
            (OrderState.UNKNOWN, OrderState.ACKNOWLEDGED),
            (OrderState.CREATED, OrderState.FILLED),
            (OrderState.VALIDATED, OrderState.FILLED),
        ]
        for from_s, to_s in illegal_transitions:
            self.assertFalse(is_valid_transition(from_s, to_s))
            with self.assertRaises(IllegalOrderStateTransitionError):
                validate_transition(from_s, to_s)

    def test_terminal_states_cannot_reopen(self):
        for term_state in TERMINAL_STATES:
            self.assertEqual(len(LEGAL_TRANSITIONS[term_state]), 0)
            for any_state in OrderState:
                self.assertFalse(is_valid_transition(term_state, any_state))
                with self.assertRaises(IllegalOrderStateTransitionError):
                    validate_transition(term_state, any_state)

    def test_unknown_never_treated_as_successful_execution(self):
        self.assertFalse(is_valid_transition(OrderState.UNKNOWN, OrderState.FILLED))
        self.assertNotIn(OrderState.FILLED, LEGAL_TRANSITIONS[OrderState.UNKNOWN])

    # --- 6. SAFETY, DETERMINISM & PIPELINE (T, U, W, Z, AA, AB, AC, AD, AF) ---

    def test_foundation_only_engine_reports_not_executed(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_safety_001",
            idempotency_key="idem_safety_001",
        )
        res = self.engine.execute(req)
        self.assertEqual(res.status, ExecutionStatus.NOT_EXECUTED)
        self.assertEqual(res.order_state, OrderState.VALIDATED)
        self.assertIn("NOT_EXECUTED_FOUNDATION_ONLY", res.reason_codes)

    def test_foundation_only_generates_no_exchange_order_ids_or_fills(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_clean_001",
            idempotency_key="idem_clean_001",
        )
        res = self.engine.execute(req)
        self.assertIsNone(res.exchange_order_id)
        self.assertEqual(res.filled_quantity, 0.0)
        self.assertIsNone(res.execution_price)
        self.assertFalse(res.side_effect_occurred)
        self.assertFalse(res.execution_attempted)

    def test_pure_determinism_across_multiple_runs(self):
        req = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="req_det_001",
            idempotency_key="idem_det_001",
        )
        engine_a = FoundationOnlyExecutionEngine()
        engine_b = FoundationOnlyExecutionEngine()

        res_a = engine_a.execute(req)
        res_b = engine_b.execute(req)

        self.assertEqual(res_a.status, res_b.status)
        self.assertEqual(res_a.order_state, res_b.order_state)
        self.assertEqual(res_a.requested_quantity, res_b.requested_quantity)
        self.assertEqual(res_a.requested_notional, res_b.requested_notional)
        self.assertEqual(res_a.reason_codes, res_b.reason_codes)
        self.assertEqual(res_a.side_effect_occurred, res_b.side_effect_occurred)

    def test_end_to_end_pipeline_integration(self):
        # 1. Start with full RiskAssessment
        self.assertTrue(self.passing_risk.is_passed)

        # 2. Construct ExecutionRequest
        exec_request = ExecutionRequest.from_risk_assessment(
            risk_assessment=self.passing_risk,
            request_id="e2e_req_001",
            idempotency_key="e2e_idem_001",
        )
        self.assertEqual(exec_request.symbol, "BTCUSDT")

        # 3. Evaluate via Foundation Execution Engine
        exec_result = self.engine.execute(exec_request)

        # 4. Assert structured result
        self.assertEqual(exec_result.status, ExecutionStatus.NOT_EXECUTED)
        self.assertEqual(exec_result.order_state, OrderState.VALIDATED)
        self.assertFalse(exec_result.is_executed)
        self.assertFalse(exec_result.side_effect_occurred)
        self.assertFalse(exec_result.execution_attempted)
        self.assertIn("NOT_EXECUTED_FOUNDATION_ONLY", exec_result.reason_codes)
        self.assertEqual(exec_result.audit_evidence["safety_lock"]["status"], "LOCKED")


if __name__ == "__main__":
    unittest.main()
