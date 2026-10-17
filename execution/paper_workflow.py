from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from data.candle import Candle
from market.analysis_models import MarketAnalysis
from strategy.context import StrategyContext
from strategy.ema_trend_following import EMATrendFollowingStrategy
from strategy.models import Signal, SignalDirection
from strategy.registry import StrategyRegistry
from strategy.rsi_mean_reversion import RSIMeanReversionStrategy

from ai.deterministic_evaluator import DeterministicStrategyEvaluator
from ai.deterministic_judge import DeterministicJudge
from ai.deterministic_ranker import DeterministicStrategyRanker
from ai.deterministic_selector import DeterministicStrategySelector
from ai.deterministic_validator import DeterministicSignalValidator
from ai.evaluator import StrategyEvaluator
from ai.judge import JudgeEngine
from ai.models import (
    JudgeDecision,
    JudgeVerdict,
    StrategyCandidate,
    StrategyEvaluation,
    StrategySelection,
    ValidatedCandidate,
    ValidationStatus,
)
from ai.ranker import StrategyRanker
from ai.selector import StrategySelector
from ai.signal_validator import SignalValidator

from execution.models import (
    ExecutionRequest,
    ExecutionStatus,
    OrderState,
    OrderType,
)
from execution.paper_engine import PaperTradingEngine
from execution.paper_models import (
    PaperExecutionResult,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    PaperTrade,
    validate_finite_number,
)
from risk.deterministic_engine import DeterministicRiskEngine
from risk.models import (
    RiskAssessment,
    RiskPolicy,
    RiskVerdict,
    TradeProposal,
)


class CandidateProcessingStatus(str, Enum):
    """
    Explicit status code for end-to-end candidate processing in paper trading.
    """
    INELIGIBLE = "INELIGIBLE"
    SELECTION_FAILED = "SELECTION_FAILED"
    NO_ACTIONABLE_SIGNAL = "NO_ACTIONABLE_SIGNAL"
    VALIDATION_REJECTED = "VALIDATION_REJECTED"
    JUDGE_REJECTED = "JUDGE_REJECTED"
    RISK_REJECTED = "RISK_REJECTED"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    PAPER_FILLED = "PAPER_FILLED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class CandidateWorkflowResult:
    """
    Immutable audit container capturing the full step-by-step lifecycle of a market candidate
    through the ATHENA decision, risk, and paper trading pipeline.
    """
    symbol: str
    status: CandidateProcessingStatus
    analysis: MarketAnalysis | None = None
    context: StrategyContext | None = None
    candidates: tuple[StrategyCandidate, ...] = field(default_factory=tuple)
    evaluations: tuple[StrategyEvaluation, ...] = field(default_factory=tuple)
    selection: StrategySelection | None = None
    signal: Signal | None = None
    validated_candidate: ValidatedCandidate | None = None
    judge_decision: JudgeDecision | None = None
    trade_proposal: TradeProposal | None = None
    risk_assessment: RiskAssessment | None = None
    execution_request: ExecutionRequest | None = None
    execution_result: PaperExecutionResult | None = None
    position: PaperPosition | None = None
    order: PaperOrder | None = None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    explanation: str = ""
    processed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_paper: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.symbol, str):
            raise ValueError("CandidateWorkflowResult symbol must be a string")

        if not isinstance(self.status, CandidateProcessingStatus):
            raise ValueError(f"CandidateWorkflowResult status must be CandidateProcessingStatus, got {type(self.status).__name__}")

        if not isinstance(self.candidates, tuple):
            object.__setattr__(self, "candidates", tuple(self.candidates))

        if not isinstance(self.evaluations, tuple):
            object.__setattr__(self, "evaluations", tuple(self.evaluations))

        if not isinstance(self.reason_codes, tuple):
            object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

        if not isinstance(self.explanation, str):
            object.__setattr__(self, "explanation", str(self.explanation))

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("CandidateWorkflowResult metadata must be a mapping")

        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def is_filled(self) -> bool:
        """True if the paper order was successfully executed and filled."""
        return self.status == CandidateProcessingStatus.PAPER_FILLED

    @property
    def is_rejected(self) -> bool:
        """True if the candidate was rejected at any pipeline stage."""
        return self.status in (
            CandidateProcessingStatus.INELIGIBLE,
            CandidateProcessingStatus.SELECTION_FAILED,
            CandidateProcessingStatus.NO_ACTIONABLE_SIGNAL,
            CandidateProcessingStatus.VALIDATION_REJECTED,
            CandidateProcessingStatus.JUDGE_REJECTED,
            CandidateProcessingStatus.RISK_REJECTED,
            CandidateProcessingStatus.EXECUTION_REJECTED,
            CandidateProcessingStatus.CONFLICT,
        )

    @property
    def is_duplicate(self) -> bool:
        """True if the request was an idempotent duplicate."""
        return self.status == CandidateProcessingStatus.DUPLICATE

    @property
    def stage_reached(self) -> str:
        """Human-readable pipeline stage reached by this candidate."""
        if self.execution_result is not None:
            return "FILLED" if self.is_filled else "EXECUTION"
        if self.execution_request is not None:
            return "EXECUTION_REQUEST"
        if self.risk_assessment is not None:
            return "RISK"
        if self.judge_decision is not None:
            return "JUDGE"
        if self.validated_candidate is not None:
            return "VALIDATION"
        if self.signal is not None:
            return "SIGNAL"
        if self.selection is not None:
            return "SELECTION"
        if self.context is not None:
            return "CONTEXT"
        return "ANALYSIS"


class PaperTradingWorkflow:
    """
    End-to-End Paper Trading Pipeline Orchestrator for ATHENA (Sprint 31).

    Coordinates:
      1. Market Analysis Shortlist Intake (from MarketAnalysisPipeline)
      2. Strategy Context Creation & Strategy Candidate Registration
      3. Deterministic Strategy Evaluation & Scoring
      4. Deterministic Strategy Ranking & Top Candidate Selection
      5. Selected Strategy Signal Generation
      6. Deterministic Signal Validation
      7. Multi-Factor Judge Decision Engine
      8. Account & Position Risk Assessment (Risk Engine)
      9. Execution Request Generation & Idempotency Safeguards
      10. Simulated Paper Trading Execution (PaperTradingEngine)
      11. Portfolio & Position Monitoring, Stop Loss / Take Profit triggers
      12. Clean Position Exits & Realized PnL Accounting

    SAFETY NOTICE:
      This pipeline operates STRICTLY in-memory under SIMULATION mode.
      It does NOT connect to private exchange endpoints, execute live trades,
      or require exchange credentials.
    """

    workflow_id: str = "paper_trading_workflow"
    name: str = "ATHENA End-to-End Paper Trading Workflow"
    description: str = "Deterministic end-to-end integration orchestrating analysis -> strategy -> judge -> risk -> paper execution"

    def __init__(
        self,
        paper_engine: PaperTradingEngine | None = None,
        strategy_registry: StrategyRegistry | None = None,
        evaluator: StrategyEvaluator | None = None,
        ranker: StrategyRanker | None = None,
        selector: StrategySelector | None = None,
        validator: SignalValidator | None = None,
        judge: JudgeEngine | None = None,
        risk_engine: DeterministicRiskEngine | None = None,
        reference_timeframe: str = "1H",
        default_leverage: float = 1.0,
        default_risk_pct: float = 2.0,
        workflow_id: str | None = None,
        db_path: str | None = None,
    ):
        self.workflow_id = workflow_id or self.workflow_id
        self.reference_timeframe = reference_timeframe
        self.default_leverage = validate_finite_number(default_leverage, "default_leverage", strictly_positive=True)
        self.default_risk_pct = validate_finite_number(default_risk_pct, "default_risk_pct", strictly_positive=True)

        # 1. Strategy Registry
        if strategy_registry is not None:
            self.strategy_registry = strategy_registry
        else:
            self.strategy_registry = StrategyRegistry()
            # Register core strategies
            self.strategy_registry.register(EMATrendFollowingStrategy(timeframe=reference_timeframe))
            self.strategy_registry.register(RSIMeanReversionStrategy(timeframe=reference_timeframe))

        # 2. Paper Trading Engine
        self.paper_engine = paper_engine or PaperTradingEngine(db_path=db_path)

        # 3. Strategy Evaluator & Ranker
        self.evaluator = evaluator or DeterministicStrategyEvaluator(
            registry=self.strategy_registry,
            timeframe=reference_timeframe,
        )
        self.ranker = ranker or DeterministicStrategyRanker()

        # 4. Strategy Selector
        self.selector = selector or DeterministicStrategySelector(
            registry=self.strategy_registry,
            evaluator=self.evaluator,
            ranker=self.ranker,
            reference_timeframe=reference_timeframe,
        )

        # 5. Signal Validator
        self.validator = validator or DeterministicSignalValidator()

        # 6. Judge Engine
        self.judge = judge or DeterministicJudge(
            reference_timeframe=reference_timeframe,
        )

        # 7. Risk Engine
        self.risk_engine = risk_engine or DeterministicRiskEngine()

    def process_market_analysis(
        self,
        analysis: MarketAnalysis,
        requested_leverage: float | None = None,
        quantity: float | None = None,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
        entry_price: float | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        candidate_strategies: Sequence[StrategyCandidate] | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> CandidateWorkflowResult:
        """
        Process a single MarketAnalysis candidate through the complete ATHENA decision, risk,
        and paper execution pipeline.

        Returns an immutable CandidateWorkflowResult detailing audit records for all stages.
        """
        now_ts = datetime.now(timezone.utc)

        # 1. Verify Analysis Presence and Validity
        if analysis is None or not isinstance(analysis, MarketAnalysis):
            extracted_symbol = getattr(analysis, "symbol", "") if analysis else ""
            return CandidateWorkflowResult(
                symbol=str(extracted_symbol) if isinstance(extracted_symbol, str) else "",
                status=CandidateProcessingStatus.INELIGIBLE,
                analysis=None,
                reason_codes=("INVALID_MARKET_ANALYSIS",),
                explanation="Input analysis must be a valid MarketAnalysis instance",
                processed_at=now_ts,
            )

        symbol = analysis.symbol
        if not symbol or not isinstance(symbol, str):
            return CandidateWorkflowResult(
                symbol="",
                status=CandidateProcessingStatus.INELIGIBLE,
                analysis=analysis,
                reason_codes=("INVALID_SYMBOL",),
                explanation="MarketAnalysis contains an invalid or empty symbol",
                processed_at=now_ts,
            )

        if not analysis.success:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.INELIGIBLE,
                analysis=analysis,
                reason_codes=("ANALYSIS_UNSUCCESSFUL",),
                explanation=f"MarketAnalysis for '{symbol}' was unsuccessful: {analysis.error or 'unknown error'}",
                processed_at=now_ts,
            )

        # 2. Build StrategyContext
        try:
            context = StrategyContext(
                analysis=analysis,
                parameters=parameters or {},
                timestamp=now_ts,
            )
        except Exception as exc:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.INELIGIBLE,
                analysis=analysis,
                reason_codes=("CONTEXT_CREATION_FAILED",),
                explanation=f"Failed to create StrategyContext: {str(exc)}",
                processed_at=now_ts,
            )

        # 3. Determine Candidate Strategies
        if candidate_strategies is not None:
            candidates = tuple(candidate_strategies)
        else:
            candidates = tuple(
                StrategyCandidate(
                    strategy_id=s_id,
                    strategy_name=self.strategy_registry.get(s_id).name,
                    eligible=True,
                )
                for s_id in self.strategy_registry.list_strategies()
            )

        if not candidates:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.SELECTION_FAILED,
                analysis=analysis,
                context=context,
                candidates=(),
                reason_codes=("NO_CANDIDATES_AVAILABLE",),
                explanation="No strategy candidates provided or registered",
                processed_at=now_ts,
            )

        # 4. Strategy Evaluation, Ranking, and Selection
        try:
            selection = self.selector.select(context=context, candidates=candidates)
        except Exception as exc:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.SELECTION_FAILED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                reason_codes=("SELECTION_ERROR",),
                explanation=f"Strategy selection failed: {str(exc)}",
                processed_at=now_ts,
            )

        if selection is None or not selection.selected_strategy_id:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.SELECTION_FAILED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                reason_codes=("SELECTION_EMPTY",),
                explanation="Strategy selector returned no selected strategy",
                processed_at=now_ts,
            )

        # 5. Retrieve Selected Strategy and Generate Signal
        strategy = self.strategy_registry.get(selection.selected_strategy_id)
        if strategy is None:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.SELECTION_FAILED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                reason_codes=("STRATEGY_NOT_FOUND",),
                explanation=f"Selected strategy '{selection.selected_strategy_id}' not found in registry",
                processed_at=now_ts,
            )

        try:
            if hasattr(strategy, "evaluate"):
                signal = strategy.evaluate(context)
            elif hasattr(strategy, "generate_signal"):
                signal = strategy.generate_signal(context)
            else:
                raise AttributeError(f"Strategy '{selection.selected_strategy_id}' has neither evaluate nor generate_signal method")
        except Exception as exc:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.NO_ACTIONABLE_SIGNAL,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                reason_codes=("SIGNAL_GENERATION_FAILED",),
                explanation=f"Strategy failed to generate signal: {str(exc)}",
                processed_at=now_ts,
            )

        if signal is None:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.NO_ACTIONABLE_SIGNAL,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=None,
                reason_codes=("NULL_SIGNAL",),
                explanation="Strategy returned None signal",
                processed_at=now_ts,
            )

        if signal.direction == SignalDirection.FLAT:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.NO_ACTIONABLE_SIGNAL,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                reason_codes=("FLAT_SIGNAL",),
                explanation=f"Selected strategy generated FLAT signal ({signal.reason})",
                processed_at=now_ts,
            )

        # 6. Signal Validation
        try:
            validated_candidate = self.validator.validate(
                signal=signal,
                context=context,
                selected_strategy_id=selection.selected_strategy_id,
            )
        except Exception as exc:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.VALIDATION_REJECTED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                reason_codes=("VALIDATOR_EXCEPTION",),
                explanation=f"Signal validator encountered exception: {str(exc)}",
                processed_at=now_ts,
            )

        if not validated_candidate.is_valid or validated_candidate.status != ValidationStatus.PASSED:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.VALIDATION_REJECTED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                validated_candidate=validated_candidate,
                reason_codes=validated_candidate.failures,
                explanation=f"Signal validation failed: {'; '.join(validated_candidate.failures)}",
                processed_at=now_ts,
            )

        # 7. Multi-Factor Judge Decision
        try:
            judge_decision = self.judge.judge(
                candidate=validated_candidate,
                context=context,
            )
        except Exception as exc:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.JUDGE_REJECTED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                validated_candidate=validated_candidate,
                reason_codes=("JUDGE_EXCEPTION",),
                explanation=f"Judge engine encountered exception: {str(exc)}",
                processed_at=now_ts,
            )

        if judge_decision.verdict != JudgeVerdict.PASS:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.JUDGE_REJECTED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                validated_candidate=validated_candidate,
                judge_decision=judge_decision,
                reason_codes=judge_decision.reason_codes,
                explanation=f"Judge rejected candidate: {judge_decision.explanation}",
                processed_at=now_ts,
            )

        # 8. Pricing & Protective Levels Extraction
        resolved_entry_price = self._resolve_entry_price(analysis, signal, entry_price)
        if resolved_entry_price is None or resolved_entry_price <= 0.0:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.RISK_REJECTED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                validated_candidate=validated_candidate,
                judge_decision=judge_decision,
                reason_codes=("INVALID_ENTRY_PRICE",),
                explanation=f"Unable to resolve a strictly positive entry price for '{symbol}'",
                processed_at=now_ts,
            )

        resolved_stop_loss = self._resolve_stop_loss_price(
            analysis, signal, resolved_entry_price, stop_loss_price
        )
        if resolved_stop_loss is None or resolved_stop_loss <= 0.0:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.RISK_REJECTED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                validated_candidate=validated_candidate,
                judge_decision=judge_decision,
                reason_codes=("INVALID_STOP_LOSS",),
                explanation=f"Unable to resolve a valid stop loss price for '{symbol}'",
                processed_at=now_ts,
            )

        resolved_take_profit = self._resolve_take_profit_price(
            analysis, signal, resolved_entry_price, resolved_stop_loss, take_profit_price
        )

        # Current portfolio snapshot for sizing and capacity checks
        snapshot = self.paper_engine.get_portfolio_snapshot()
        leverage = float(requested_leverage if requested_leverage is not None else self.default_leverage)

        # Determine quantity
        if quantity is not None:
            resolved_quantity = validate_finite_number(quantity, "quantity", strictly_positive=True)
        else:
            resolved_quantity = self._calculate_position_size(
                equity=snapshot.total_equity,
                cash=snapshot.cash,
                entry_price=resolved_entry_price,
                stop_loss_price=resolved_stop_loss,
                leverage=leverage,
            )

        # 9. Trade Proposal & Risk Assessment
        existing_symbol_exposure = sum(
            p.notional for p in snapshot.open_positions if p.symbol == symbol
        )

        prop_metadata = {}
        if resolved_take_profit is not None:
            prop_metadata["take_profit_price"] = resolved_take_profit

        proposal = TradeProposal(
            judge_decision=judge_decision,
            entry_price=resolved_entry_price,
            stop_loss_price=resolved_stop_loss,
            quantity=resolved_quantity,
            account_equity=snapshot.total_equity,
            available_margin=snapshot.cash,
            existing_exposure=snapshot.open_notional,
            existing_symbol_exposure=existing_symbol_exposure,
            contract_multiplier=self.paper_engine.contract_multiplier,
            requested_leverage=leverage,
            metadata=prop_metadata,
        )

        try:
            risk_assessment = self.risk_engine.assess(proposal)
        except Exception as exc:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.RISK_REJECTED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                validated_candidate=validated_candidate,
                judge_decision=judge_decision,
                trade_proposal=proposal,
                reason_codes=("RISK_ENGINE_EXCEPTION",),
                explanation=f"Risk engine evaluation encountered exception: {str(exc)}",
                processed_at=now_ts,
            )

        if risk_assessment.verdict != RiskVerdict.PASS:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.RISK_REJECTED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                validated_candidate=validated_candidate,
                judge_decision=judge_decision,
                trade_proposal=proposal,
                risk_assessment=risk_assessment,
                reason_codes=risk_assessment.reason_codes,
                explanation=f"Risk engine rejected proposal: {risk_assessment.explanation}",
                processed_at=now_ts,
            )

        # 10. Execution Request Creation
        req_id = request_id or f"wf_req_{symbol}_{int(now_ts.timestamp() * 1000)}"
        idem_key = idempotency_key or f"wf_idem_{symbol}_{signal.timestamp.isoformat()}"

        exec_metadata = {}
        if resolved_take_profit is not None:
            exec_metadata["take_profit_price"] = resolved_take_profit

        try:
            request = ExecutionRequest.from_risk_assessment(
                risk_assessment=risk_assessment,
                request_id=req_id,
                idempotency_key=idem_key,
                order_type=OrderType.MARKET,
                metadata=exec_metadata,
            )
        except Exception as exc:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.EXECUTION_REJECTED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                validated_candidate=validated_candidate,
                judge_decision=judge_decision,
                trade_proposal=proposal,
                risk_assessment=risk_assessment,
                reason_codes=("EXECUTION_REQUEST_CREATION_FAILED",),
                explanation=f"Failed to create ExecutionRequest: {str(exc)}",
                processed_at=now_ts,
            )

        # 11. Simulated Execution via PaperTradingEngine
        try:
            exec_result = self.paper_engine.execute(
                request=request,
                market_price=resolved_entry_price,
            )
        except Exception as exc:
            return CandidateWorkflowResult(
                symbol=symbol,
                status=CandidateProcessingStatus.EXECUTION_REJECTED,
                analysis=analysis,
                context=context,
                candidates=candidates,
                selection=selection,
                signal=signal,
                validated_candidate=validated_candidate,
                judge_decision=judge_decision,
                trade_proposal=proposal,
                risk_assessment=risk_assessment,
                execution_request=request,
                reason_codes=("PAPER_EXECUTION_EXCEPTION",),
                explanation=f"Paper engine execution encountered exception: {str(exc)}",
                processed_at=now_ts,
            )

        # 12. Map Execution Status to Candidate Processing Status
        if exec_result.status == ExecutionStatus.FILLED:
            workflow_status = CandidateProcessingStatus.PAPER_FILLED
        elif exec_result.status == ExecutionStatus.DUPLICATE:
            workflow_status = CandidateProcessingStatus.DUPLICATE
        elif exec_result.status == ExecutionStatus.CONFLICT:
            workflow_status = CandidateProcessingStatus.CONFLICT
        else:
            workflow_status = CandidateProcessingStatus.EXECUTION_REJECTED

        return CandidateWorkflowResult(
            symbol=symbol,
            status=workflow_status,
            analysis=analysis,
            context=context,
            candidates=candidates,
            selection=selection,
            signal=signal,
            validated_candidate=validated_candidate,
            judge_decision=judge_decision,
            trade_proposal=proposal,
            risk_assessment=risk_assessment,
            execution_request=request,
            execution_result=exec_result,
            position=exec_result.position,
            order=exec_result.order,
            reason_codes=exec_result.reason_codes,
            explanation=exec_result.explanation,
            processed_at=now_ts,
            metadata={
                "fill_price": exec_result.order.fill_price if exec_result.order else None,
                "filled_quantity": exec_result.order.quantity if exec_result.order else 0.0,
                "is_paper": True,
            },
        )

    def process_shortlist(
        self,
        analyses: Sequence[MarketAnalysis],
        requested_leverage: float | None = None,
        quantity: float | None = None,
    ) -> list[CandidateWorkflowResult]:
        """
        Process a sequence of MarketAnalysis candidates in deterministic order.
        Guarantees that an error or rejection on one candidate does NOT corrupt
        portfolio state or prevent processing of subsequent candidates.
        """
        results: list[CandidateWorkflowResult] = []
        if not analyses:
            return results

        for analysis in analyses:
            try:
                res = self.process_market_analysis(
                    analysis=analysis,
                    requested_leverage=requested_leverage,
                    quantity=quantity,
                )
                results.append(res)
            except Exception as exc:
                # Top-level fail-safe to isolate per-symbol crashes
                extracted_sym = getattr(analysis, "symbol", "UNKNOWN")
                results.append(
                    CandidateWorkflowResult(
                        symbol=str(extracted_sym),
                        status=CandidateProcessingStatus.INELIGIBLE,
                        analysis=analysis if isinstance(analysis, MarketAnalysis) else None,
                        reason_codes=("UNHANDLED_SYMBOL_EXCEPTION",),
                        explanation=f"Unhandled exception processing symbol '{extracted_sym}': {str(exc)}",
                    )
                )

        return results

    # -------------------------------------------------------------------------
    # Delegated Paper Engine Lifecycle Management Hooks
    # -------------------------------------------------------------------------

    def update_market_price(
        self,
        symbol: str,
        current_price: float,
        timestamp: datetime | None = None,
    ) -> list[PaperTrade]:
        """
        Update market price for a symbol, evaluate stop-loss / take-profit triggers,
        and realize PnL on triggered exits.
        """
        return self.paper_engine.update_market_price(
            symbol=symbol,
            current_price=current_price,
            timestamp=timestamp,
        )

    def process_candle(self, candle: Candle) -> list[PaperTrade]:
        """
        Evaluate an incoming candle against open paper positions under conservative
        ambiguous-candle policy (Stop Loss evaluated before Take Profit if both touched).
        """
        return self.paper_engine.process_candle(candle)

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        quantity: float | None = None,
        exit_reason: str = "MANUAL_CLOSE",
        timestamp: datetime | None = None,
    ) -> PaperTrade:
        """Close an open paper position manually and realize PnL."""
        return self.paper_engine.close_position(
            position_id=position_id,
            exit_price=exit_price,
            quantity=quantity,
            exit_reason=exit_reason,
            timestamp=timestamp,
        )

    def get_portfolio_snapshot(self, timestamp: datetime | None = None) -> PaperPortfolio:
        """Return the current immutable paper portfolio snapshot."""
        return self.paper_engine.get_portfolio_snapshot(timestamp=timestamp)

    def get_open_positions(self) -> tuple[PaperPosition, ...]:
        """Return all active open paper positions."""
        return self.paper_engine.get_open_positions()

    def get_closed_trades(self) -> tuple[PaperTrade, ...]:
        """Return all closed paper trades."""
        return self.paper_engine.get_closed_trades()

    @property
    def storage(self) -> Any:
        """Expose underlying PaperStorage instance if configured."""
        return getattr(self.paper_engine, "storage", None)

    def close(self) -> None:
        """Safely close paper trading engine and persistent resources."""
        if hasattr(self.paper_engine, "close"):
            self.paper_engine.close()

    # -------------------------------------------------------------------------
    # Internal Price Resolution & Sizing Helpers
    # -------------------------------------------------------------------------

    def _resolve_entry_price(
        self,
        analysis: MarketAnalysis,
        signal: Signal,
        explicit_price: float | None,
    ) -> float | None:
        """Resolve current entry price from explicit parameter, signal, candle, or ranking."""
        if explicit_price is not None:
            try:
                return validate_finite_number(explicit_price, "entry_price", strictly_positive=True)
            except ValueError:
                return None

        # From signal metadata
        sig_price = signal.metadata.get("price")
        if isinstance(sig_price, (int, float)) and not isinstance(sig_price, bool) and math.isfinite(sig_price) and sig_price > 0:
            return float(sig_price)

        # From reference timeframe candles
        tf_candles = analysis.candles_by_granularity.get(self.reference_timeframe)
        if tf_candles and len(tf_candles) > 0:
            return float(tf_candles[-1].close)

        # From any available timeframe candles
        for candles in analysis.candles_by_granularity.values():
            if candles and len(candles) > 0:
                return float(candles[-1].close)

        # From ranking
        if analysis.ranking is not None:
            for attr in ("last_price", "price", "close"):
                val = getattr(analysis.ranking, attr, None)
                if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val) and val > 0:
                    return float(val)

        return None

    def _resolve_stop_loss_price(
        self,
        analysis: MarketAnalysis,
        signal: Signal,
        entry_price: float,
        explicit_stop: float | None,
    ) -> float | None:
        """Resolve protective stop loss price."""
        if explicit_stop is not None:
            try:
                return validate_finite_number(explicit_stop, "stop_loss_price", strictly_positive=True)
            except ValueError:
                return None

        sig_stop = signal.metadata.get("stop_loss")
        if isinstance(sig_stop, (int, float)) and not isinstance(sig_stop, bool) and math.isfinite(sig_stop) and sig_stop > 0:
            return float(sig_stop)

        # Attempt ATR-based stop
        tf_features = analysis.features_by_granularity.get(self.reference_timeframe)
        atr_val = None
        if tf_features is not None:
            raw_atr = tf_features.values.get("atr_14")
            if isinstance(raw_atr, (int, float)) and not isinstance(raw_atr, bool) and math.isfinite(raw_atr) and raw_atr > 0:
                atr_val = float(raw_atr)

        if atr_val is not None and atr_val > 0.0:
            buffer = 2.0 * atr_val
        else:
            buffer = entry_price * 0.02  # 2% default fallback

        if signal.direction == SignalDirection.LONG:
            return max(entry_price - buffer, 0.0001)
        elif signal.direction == SignalDirection.SHORT:
            return entry_price + buffer

        return None

    def _resolve_take_profit_price(
        self,
        analysis: MarketAnalysis,
        signal: Signal,
        entry_price: float,
        stop_loss_price: float,
        explicit_tp: float | None,
    ) -> float | None:
        """Resolve take profit price."""
        if explicit_tp is not None:
            try:
                return validate_finite_number(explicit_tp, "take_profit_price", strictly_positive=True)
            except ValueError:
                return None

        sig_tp = signal.metadata.get("take_profit")
        if isinstance(sig_tp, (int, float)) and not isinstance(sig_tp, bool) and math.isfinite(sig_tp) and sig_tp > 0:
            return float(sig_tp)

        stop_dist = abs(entry_price - stop_loss_price)
        if stop_dist <= 0:
            stop_dist = entry_price * 0.02

        reward_dist = 1.5 * stop_dist

        if signal.direction == SignalDirection.LONG:
            return entry_price + reward_dist
        elif signal.direction == SignalDirection.SHORT:
            return max(entry_price - reward_dist, 0.0001)

        return None

    def _calculate_position_size(
        self,
        equity: float,
        cash: float,
        entry_price: float,
        stop_loss_price: float,
        leverage: float,
    ) -> float:
        """Calculate risk-budgeted position size capped by cash, leverage, and policy limits."""
        stop_dist = abs(entry_price - stop_loss_price)
        if stop_dist <= 0:
            stop_dist = entry_price * 0.02

        # Risk budget in dollars
        risk_budget = equity * (self.default_risk_pct / 100.0)
        calc_qty = risk_budget / stop_dist

        # Cap by maximum position notional from risk policy
        max_notional = self.risk_engine.policy.max_position_notional
        if (calc_qty * entry_price) > max_notional:
            calc_qty = (max_notional * 0.999) / entry_price

        # Cap by available cash & leverage including entry fee buffer
        cash_multiplier = (1.0 / leverage) + self.paper_engine.fee_rate
        total_cash_needed = (calc_qty * entry_price) * cash_multiplier
        if total_cash_needed > cash:
            calc_qty = (cash * 0.995) / (entry_price * cash_multiplier)

        # Floor to 4 decimal places to prevent round-up exceeding margin/limits
        qty = math.floor(calc_qty * 10000.0) / 10000.0
        if qty <= 0.0:
            qty = 0.0001
        return qty
