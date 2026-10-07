from datetime import datetime
from typing import Any, Mapping

from ai.models import StrategySelection, ValidatedCandidate, ValidationStatus
from ai.signal_validator import SignalValidator
from strategy.context import StrategyContext
from strategy.models import Signal, SignalDirection


class DeterministicSignalValidator(SignalValidator):
    """
    Deterministic reference signal validator.

    Validation Rules:
      1. Signal Object: Must be an instance of Signal (not None, not wrong type).
      2. Symbol Integrity: signal.symbol must be a non-empty string.
      3. Strategy ID Integrity: signal.strategy_id must be a non-empty string.
      4. Direction Integrity: signal.direction must be an instance of SignalDirection (LONG, SHORT, FLAT).
         NOTE: FLAT is a valid non-trading signal and is NOT rejected.
      5. Confidence Bounds: signal.confidence must be numeric (int/float) and within [0.0, 1.0].
      6. Timestamp Integrity: signal.timestamp must be a valid datetime instance.
      7. Strategy Consistency: If selected_strategy_id (or StrategySelection) is provided,
         signal.strategy_id must match expected strategy ID.
      8. Context Symbol Consistency: If StrategyContext is provided, signal.symbol must match context.symbol.
      9. Contextual Integrity: If StrategyContext is provided, context must be valid StrategyContext.
      10. Metadata Integrity: signal.metadata must be a mapping.
      11. Immutability & Safety: Never mutates Signal or StrategyContext. No network calls,
          no exchange access, no risk sizing, no order execution.
    """

    validator_id: str = "deterministic_signal_validator"
    name: str = "Deterministic Signal Validator"
    description: str = "Validates signal structure, symbol alignment, and strategy consistency deterministically"

    def validate(
        self,
        signal: Any,
        context: StrategyContext | None = None,
        selected_strategy_id: str | Any | None = None,
    ) -> ValidatedCandidate:
        failures: list[str] = []

        # 1. Resolve expected strategy ID if supplied
        expected_strategy_id: str | None = None
        if isinstance(selected_strategy_id, StrategySelection):
            expected_strategy_id = selected_strategy_id.selected_strategy_id
        elif isinstance(selected_strategy_id, str) and selected_strategy_id:
            expected_strategy_id = selected_strategy_id
        elif selected_strategy_id is not None:
            expected_strategy_id = str(selected_strategy_id)

        # 2. Check for None signal
        if signal is None:
            failures.append("Signal cannot be None")
            return ValidatedCandidate(
                status=ValidationStatus.FAILED,
                symbol="",
                strategy_id="",
                direction=None,
                confidence=0.0,
                signal=None,
                failures=tuple(failures),
                metadata={"expected_strategy_id": expected_strategy_id},
            )

        # 3. Check for correct Signal type
        if not isinstance(signal, Signal):
            failures.append(f"Signal must be an instance of Signal, got {type(signal).__name__}")
            extracted_symbol = getattr(signal, "symbol", "")
            extracted_strategy_id = getattr(signal, "strategy_id", "")
            raw_dir = getattr(signal, "direction", None)
            extracted_dir = raw_dir if isinstance(raw_dir, SignalDirection) else None
            return ValidatedCandidate(
                status=ValidationStatus.FAILED,
                symbol=str(extracted_symbol) if isinstance(extracted_symbol, str) else "",
                strategy_id=str(extracted_strategy_id) if isinstance(extracted_strategy_id, str) else "",
                direction=extracted_dir,
                confidence=0.0,
                signal=None,
                failures=tuple(failures),
                metadata={"expected_strategy_id": expected_strategy_id},
            )

        # 4. Attribute integrity checks on valid Signal object
        # Symbol
        if not isinstance(signal.symbol, str) or not signal.symbol.strip():
            failures.append("Signal symbol must be a non-empty string")

        # Strategy ID
        if not isinstance(signal.strategy_id, str) or not signal.strategy_id.strip():
            failures.append("Signal strategy_id must be a non-empty string")

        # Direction
        if not isinstance(signal.direction, SignalDirection):
            failures.append(f"Signal direction must be an instance of SignalDirection, got {type(signal.direction).__name__}")

        # Confidence
        if not isinstance(signal.confidence, (int, float)) or isinstance(signal.confidence, bool):
            failures.append(f"Signal confidence must be numeric between 0.0 and 1.0, got {type(signal.confidence).__name__}")
        elif signal.confidence < 0.0:
            failures.append(f"Signal confidence {signal.confidence} is below 0.0")
        elif signal.confidence > 1.0:
            failures.append(f"Signal confidence {signal.confidence} is above 1.0")

        # Timestamp
        if not isinstance(signal.timestamp, datetime):
            failures.append("Signal timestamp must be a datetime instance")

        # Metadata
        if not isinstance(signal.metadata, (dict, Mapping)):
            failures.append("Signal metadata must be a mapping")

        # 5. Contextual checks
        if context is not None:
            if not isinstance(context, StrategyContext):
                failures.append(f"Context must be an instance of StrategyContext, got {type(context).__name__}")
            else:
                if isinstance(signal.symbol, str) and signal.symbol and context.symbol:
                    if signal.symbol != context.symbol:
                        failures.append(f"Signal symbol '{signal.symbol}' does not match context symbol '{context.symbol}'")

        # 6. Strategy alignment checks
        if expected_strategy_id is not None:
            if signal.strategy_id != expected_strategy_id:
                failures.append(f"Signal strategy_id '{signal.strategy_id}' does not match selected strategy '{expected_strategy_id}'")

        # 7. Construct result
        status = ValidationStatus.PASSED if not failures else ValidationStatus.FAILED
        symbol_val = signal.symbol if isinstance(signal.symbol, str) else ""
        strategy_id_val = signal.strategy_id if isinstance(signal.strategy_id, str) else ""
        direction_val = signal.direction if isinstance(signal.direction, SignalDirection) else None

        valid_confidence = 0.0
        if isinstance(signal.confidence, (int, float)) and not isinstance(signal.confidence, bool):
            if 0.0 <= float(signal.confidence) <= 1.0:
                valid_confidence = float(signal.confidence)

        meta = {
            "expected_strategy_id": expected_strategy_id,
            "has_context": context is not None,
            "signal_reason": getattr(signal, "reason", ""),
        }

        return ValidatedCandidate(
            status=status,
            symbol=symbol_val,
            strategy_id=strategy_id_val,
            direction=direction_val,
            confidence=valid_confidence,
            signal=signal,
            failures=tuple(failures),
            metadata=meta,
        )
