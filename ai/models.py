from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


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
