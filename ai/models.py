from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from strategy.models import Signal, SignalDirection


@dataclass(frozen=True)
class StrategyCandidate:
    """
    Immutable representation of a strategy candidate available for selection.
    """
    strategy_id: str
    strategy_name: str
    description: str = ""
    eligible: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            raise ValueError("StrategyCandidate strategy_id must be a non-empty string")

        if not isinstance(self.strategy_name, str) or not self.strategy_name:
            raise ValueError("StrategyCandidate strategy_name must be a non-empty string")

        if not isinstance(self.description, str):
            raise ValueError("StrategyCandidate description must be a string")

        if not isinstance(self.eligible, bool):
            raise ValueError("StrategyCandidate eligible must be a boolean")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("StrategyCandidate metadata must be a mapping")

        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )


@dataclass(frozen=True)
class StrategySelection:
    """
    Immutable result describing the selected strategy and reasoning.
    """
    selected_strategy_id: str
    selection_reason: str
    selector_id: str
    confidence: float
    candidate_ids: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.selected_strategy_id, str) or not self.selected_strategy_id:
            raise ValueError("StrategySelection selected_strategy_id must be a non-empty string")

        if not isinstance(self.selection_reason, str) or not self.selection_reason:
            raise ValueError("StrategySelection selection_reason must be a non-empty string")

        if not isinstance(self.selector_id, str) or not self.selector_id:
            raise ValueError("StrategySelection selector_id must be a non-empty string")

        if not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool):
            raise ValueError("StrategySelection confidence must be a float between 0.0 and 1.0")

        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError("StrategySelection confidence must be between 0.0 and 1.0")

        if not isinstance(self.candidate_ids, (list, tuple)):
            raise ValueError("StrategySelection candidate_ids must be a sequence of strings")

        if not all(isinstance(cid, str) and cid for cid in self.candidate_ids):
            raise ValueError("StrategySelection candidate_ids elements must be non-empty strings")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("StrategySelection metadata must be a mapping")

        object.__setattr__(
            self,
            "candidate_ids",
            tuple(self.candidate_ids),
        )
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )


@dataclass(frozen=True)
class StrategyEvaluation:
    """
    Immutable evaluation of a strategy candidate's deterministic suitability.
    The overall score describes how well the strategy fits the supplied market context.
    NOTE: This score is NOT a probability of profit or expected financial return.
    """
    strategy_id: str
    eligible: bool
    score: float
    regime_alignment: float
    signal_alignment: float
    data_quality: float
    evaluation_reason: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            raise ValueError("StrategyEvaluation strategy_id must be a non-empty string")

        if not isinstance(self.eligible, bool):
            raise ValueError("StrategyEvaluation eligible must be a boolean")

        for metric_name, val in [
            ("score", self.score),
            ("regime_alignment", self.regime_alignment),
            ("signal_alignment", self.signal_alignment),
            ("data_quality", self.data_quality),
        ]:
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise ValueError(f"StrategyEvaluation {metric_name} must be a float between 0.0 and 1.0")
            if not (0.0 <= float(val) <= 1.0):
                raise ValueError(f"StrategyEvaluation {metric_name} must be between 0.0 and 1.0")

        if not isinstance(self.evaluation_reason, str) or not self.evaluation_reason:
            raise ValueError("StrategyEvaluation evaluation_reason must be a non-empty string")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("StrategyEvaluation metadata must be a mapping")

        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )


@dataclass(frozen=True)
class RankedStrategy:
    """
    Immutable representation of a ranked candidate strategy.
    Preserves full evaluation evidence alongside rank position.
    """
    rank: int
    evaluation: StrategyEvaluation

    def __post_init__(self):
        if not isinstance(self.rank, int) or isinstance(self.rank, bool):
            raise ValueError("RankedStrategy rank must be an integer")
        if self.rank < 1:
            raise ValueError("RankedStrategy rank must be a positive integer (1-indexed)")
        if not isinstance(self.evaluation, StrategyEvaluation):
            raise ValueError("RankedStrategy evaluation must be a StrategyEvaluation instance")

    @property
    def strategy_id(self) -> str:
        return self.evaluation.strategy_id

    @property
    def score(self) -> float:
        return self.evaluation.score

    @property
    def eligible(self) -> bool:
        return self.evaluation.eligible


class ValidationStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ValidatedCandidate:
    """
    Immutable representation of a validated candidate signal ready for Pre-Judge inspection.
    Encapsulates the original signal, validation outcome, direction, confidence, and audit evidence.
    NOTE: Validation passing confirms structural and contextual validity.
    It does NOT constitute an execution, sizing, or trading decision.
    """
    status: ValidationStatus
    symbol: str
    strategy_id: str
    direction: SignalDirection | None
    confidence: float
    signal: Signal | None = None
    failures: tuple[str, ...] = ()
    validation_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.status, ValidationStatus):
            raise ValueError("ValidatedCandidate status must be an instance of ValidationStatus")

        if not isinstance(self.symbol, str):
            raise ValueError("ValidatedCandidate symbol must be a string")

        if not isinstance(self.strategy_id, str):
            raise ValueError("ValidatedCandidate strategy_id must be a string")

        if self.direction is not None and not isinstance(self.direction, SignalDirection):
            raise ValueError("ValidatedCandidate direction must be an instance of SignalDirection or None")

        if not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool):
            raise ValueError("ValidatedCandidate confidence must be a float between 0.0 and 1.0")

        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError("ValidatedCandidate confidence must be between 0.0 and 1.0")

        if self.signal is not None and not isinstance(self.signal, Signal):
            raise ValueError("ValidatedCandidate signal must be an instance of Signal or None")

        if not isinstance(self.failures, (list, tuple)):
            raise ValueError("ValidatedCandidate failures must be a sequence of strings")

        if not isinstance(self.validation_timestamp, datetime):
            raise ValueError("ValidatedCandidate validation_timestamp must be a datetime instance")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("ValidatedCandidate metadata must be a mapping")

        object.__setattr__(
            self,
            "failures",
            tuple(str(f) for f in self.failures),
        )
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )

    @property
    def is_valid(self) -> bool:
        return self.status == ValidationStatus.PASSED


class JudgeVerdict(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"


@dataclass(frozen=True)
class FactorEvidence:
    """
    Immutable representation of an individual factor evaluated by the Judge.
    """
    factor_name: str
    status: str
    score: float
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.factor_name, str) or not self.factor_name:
            raise ValueError("FactorEvidence factor_name must be a non-empty string")

        if not isinstance(self.status, str) or not self.status:
            raise ValueError("FactorEvidence status must be a non-empty string")

        if not isinstance(self.score, (int, float)) or isinstance(self.score, bool):
            raise ValueError("FactorEvidence score must be a float between 0.0 and 1.0")

        if not (0.0 <= float(self.score) <= 1.0):
            raise ValueError("FactorEvidence score must be between 0.0 and 1.0")

        if not isinstance(self.reason, str):
            raise ValueError("FactorEvidence reason must be a string")

        if not isinstance(self.evidence, (dict, Mapping)):
            raise ValueError("FactorEvidence evidence must be a mapping")

        object.__setattr__(
            self,
            "evidence",
            MappingProxyType(dict(self.evidence)),
        )


@dataclass(frozen=True)
class JudgeDecision:
    """
    Immutable structured decision output from the Judge Engine.
    NOTE: A PASS decision confirms that multi-factor evidence satisfies configured criteria.
    It does NOT authorize order execution, calculate leverage, or predict profit.
    """
    verdict: JudgeVerdict
    symbol: str
    strategy_id: str
    direction: SignalDirection | None
    confidence: float
    score: float
    reason_codes: tuple[str, ...]
    explanation: str
    factor_evidence: Mapping[str, FactorEvidence] = field(default_factory=dict)
    candidate: ValidatedCandidate | None = None
    policy_id: str = "deterministic_v1"
    decision_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.verdict, JudgeVerdict):
            raise ValueError("JudgeDecision verdict must be an instance of JudgeVerdict")

        if not isinstance(self.symbol, str):
            raise ValueError("JudgeDecision symbol must be a string")

        if not isinstance(self.strategy_id, str):
            raise ValueError("JudgeDecision strategy_id must be a string")

        if self.direction is not None and not isinstance(self.direction, SignalDirection):
            raise ValueError("JudgeDecision direction must be an instance of SignalDirection or None")

        if not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool):
            raise ValueError("JudgeDecision confidence must be a float between 0.0 and 1.0")

        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError("JudgeDecision confidence must be between 0.0 and 1.0")

        if not isinstance(self.score, (int, float)) or isinstance(self.score, bool):
            raise ValueError("JudgeDecision score must be a float between 0.0 and 1.0")

        if not (0.0 <= float(self.score) <= 1.0):
            raise ValueError("JudgeDecision score must be between 0.0 and 1.0")

        if not isinstance(self.reason_codes, (list, tuple)):
            raise ValueError("JudgeDecision reason_codes must be a sequence of strings")

        if not all(isinstance(rc, str) and rc for rc in self.reason_codes):
            raise ValueError("JudgeDecision reason_codes elements must be non-empty strings")

        if not isinstance(self.explanation, str):
            raise ValueError("JudgeDecision explanation must be a string")

        if not isinstance(self.factor_evidence, (dict, Mapping)):
            raise ValueError("JudgeDecision factor_evidence must be a mapping")

        if self.candidate is not None and not isinstance(self.candidate, ValidatedCandidate):
            raise ValueError("JudgeDecision candidate must be a ValidatedCandidate or None")

        if self.verdict == JudgeVerdict.PASS:
            if self.candidate is None or not self.candidate.is_valid:
                raise ValueError("Cannot produce a passing JudgeDecision for an invalid or missing candidate")

        if not isinstance(self.policy_id, str) or not self.policy_id:
            raise ValueError("JudgeDecision policy_id must be a non-empty string")

        if not isinstance(self.decision_timestamp, datetime):
            raise ValueError("JudgeDecision decision_timestamp must be a datetime instance")

        if not isinstance(self.metadata, (dict, Mapping)):
            raise ValueError("JudgeDecision metadata must be a mapping")

        object.__setattr__(
            self,
            "reason_codes",
            tuple(str(rc) for rc in self.reason_codes),
        )
        object.__setattr__(
            self,
            "factor_evidence",
            MappingProxyType(dict(self.factor_evidence)),
        )
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )

    @property
    def is_passed(self) -> bool:
        return self.verdict == JudgeVerdict.PASS

    @property
    def is_rejected(self) -> bool:
        return self.verdict == JudgeVerdict.REJECT
