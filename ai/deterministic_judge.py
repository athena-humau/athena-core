from datetime import datetime, timezone
from typing import Any, Mapping

from ai.judge import JudgeEngine
from ai.models import (
    FactorEvidence,
    JudgeDecision,
    JudgeVerdict,
    ValidatedCandidate,
    ValidationStatus,
)
from market.analysis_models import MarketAnalysis
from regime.models import Regime
from strategy.context import StrategyContext
from strategy.models import SignalDirection


class DeterministicJudge(JudgeEngine):
    """
    Deterministic reference implementation of the ATHENA Judge Engine.

    Evaluates validated signal candidates against multi-factor market evidence:
      1. Candidate Validation Integrity: Rejects any failed, malformed, or invalid candidate.
      2. Signal Actionability: FLAT signals are non-actionable and rejected with explicit reason.
      3. Confidence Threshold: Rejects signals with confidence below configured minimum.
      4. Context & Symbol Consistency: Verifies candidate symbol matches analysis context.
      5. Regime Alignment: Evaluates direction against market regime with strategy-specific awareness.
      6. Volatility Context: Evaluates normalized relative ATR boundaries when ATR is available.
      7. Feature Confluence: Detects severe indicator contradictions (e.g. extreme counter-RSI/EMA).

    Scoring Semantics:
      The overall score is a bounded evidence suitability metric in [0.0, 1.0].
      It represents how well the available evidence satisfies configured criteria.
      It is strictly NOT a probability of profit or expected financial return.
    """

    judge_id: str = "deterministic_judge"
    policy_id: str = "deterministic_v1"
    name: str = "Deterministic Multi-Factor Judge"
    description: str = "Evaluates validated candidates against regime, volatility, confidence, and confluence evidence"

    # Multi-factor weights
    WEIGHT_CONFIDENCE = 0.30
    WEIGHT_REGIME = 0.30
    WEIGHT_VOLATILITY = 0.20
    WEIGHT_CONFLUENCE = 0.20

    def __init__(
        self,
        judge_id: str | None = None,
        policy_id: str | None = None,
        min_confidence: float = 0.50,
        reference_timeframe: str = "1H",
        max_relative_atr: float | None = 0.08,
        min_relative_atr: float | None = 0.0001,
        require_regime_alignment: bool = True,
        decision_score_threshold: float = 0.60,
    ):
        super().__init__(judge_id or self.judge_id, policy_id or self.policy_id)

        if not isinstance(min_confidence, (int, float)) or isinstance(min_confidence, bool):
            raise ValueError("min_confidence must be a float between 0.0 and 1.0")
        if not (0.0 <= float(min_confidence) <= 1.0):
            raise ValueError("min_confidence must be between 0.0 and 1.0")

        if not isinstance(reference_timeframe, str) or not reference_timeframe.strip():
            raise ValueError("reference_timeframe must be a non-empty string")

        if max_relative_atr is not None:
            if not isinstance(max_relative_atr, (int, float)) or isinstance(max_relative_atr, bool):
                raise ValueError("max_relative_atr must be a positive float or None")
            if float(max_relative_atr) <= 0:
                raise ValueError("max_relative_atr must be greater than 0")

        if min_relative_atr is not None:
            if not isinstance(min_relative_atr, (int, float)) or isinstance(min_relative_atr, bool):
                raise ValueError("min_relative_atr must be a non-negative float or None")
            if float(min_relative_atr) < 0:
                raise ValueError("min_relative_atr must be greater than or equal to 0")

        if not isinstance(require_regime_alignment, bool):
            raise ValueError("require_regime_alignment must be a boolean")

        if not isinstance(decision_score_threshold, (int, float)) or isinstance(decision_score_threshold, bool):
            raise ValueError("decision_score_threshold must be a float between 0.0 and 1.0")
        if not (0.0 <= float(decision_score_threshold) <= 1.0):
            raise ValueError("decision_score_threshold must be between 0.0 and 1.0")

        self.min_confidence = float(min_confidence)
        self.reference_timeframe = reference_timeframe.strip()
        self.max_relative_atr = float(max_relative_atr) if max_relative_atr is not None else None
        self.min_relative_atr = float(min_relative_atr) if min_relative_atr is not None else None
        self.require_regime_alignment = require_regime_alignment
        self.decision_score_threshold = float(decision_score_threshold)

    def judge(
        self,
        candidate: Any,
        context: StrategyContext | MarketAnalysis | None = None,
    ) -> JudgeDecision:
        # 1. Candidate Presence & Type Validation
        if candidate is None:
            return JudgeDecision(
                verdict=JudgeVerdict.REJECT,
                symbol="",
                strategy_id="",
                direction=None,
                confidence=0.0,
                score=0.0,
                reason_codes=("INVALID_CANDIDATE",),
                explanation="Candidate cannot be None",
                factor_evidence={},
                candidate=None,
                policy_id=self.policy_id,
            )

        if not isinstance(candidate, ValidatedCandidate):
            extracted_symbol = str(getattr(candidate, "symbol", ""))
            extracted_strategy_id = str(getattr(candidate, "strategy_id", ""))
            raw_dir = getattr(candidate, "direction", None)
            extracted_dir = raw_dir if isinstance(raw_dir, SignalDirection) else None
            return JudgeDecision(
                verdict=JudgeVerdict.REJECT,
                symbol=extracted_symbol,
                strategy_id=extracted_strategy_id,
                direction=extracted_dir,
                confidence=0.0,
                score=0.0,
                reason_codes=("INVALID_CANDIDATE",),
                explanation=f"Candidate must be an instance of ValidatedCandidate, got {type(candidate).__name__}",
                factor_evidence={},
                candidate=None,
                policy_id=self.policy_id,
            )

        # 2. Check Candidate Validation Status
        if not candidate.is_valid or candidate.status != ValidationStatus.PASSED:
            failures_str = "; ".join(candidate.failures) if candidate.failures else "Unknown validation failure"
            return JudgeDecision(
                verdict=JudgeVerdict.REJECT,
                symbol=candidate.symbol,
                strategy_id=candidate.strategy_id,
                direction=candidate.direction,
                confidence=candidate.confidence,
                score=0.0,
                reason_codes=("CANDIDATE_VALIDATION_FAILED",),
                explanation=f"Validated candidate failed signal validation: {failures_str}",
                factor_evidence={
                    "candidate_validity": FactorEvidence(
                        factor_name="candidate_validity",
                        status="FAILED",
                        score=0.0,
                        reason=f"Candidate validation failed: {failures_str}",
                        evidence={"failures": candidate.failures},
                    )
                },
                candidate=candidate,
                policy_id=self.policy_id,
            )

        # 3. Direction Actionability: FLAT is non-actionable
        if candidate.direction == SignalDirection.FLAT:
            return JudgeDecision(
                verdict=JudgeVerdict.REJECT,
                symbol=candidate.symbol,
                strategy_id=candidate.strategy_id,
                direction=SignalDirection.FLAT,
                confidence=candidate.confidence,
                score=0.0,
                reason_codes=("FLAT_SIGNAL_NOT_ACTIONABLE",),
                explanation="FLAT signal indicates market neutrality and is non-actionable for trading execution",
                factor_evidence={
                    "direction_actionability": FactorEvidence(
                        factor_name="direction_actionability",
                        status="FAILED",
                        score=0.0,
                        reason="FLAT signal is non-actionable for trade entry",
                        evidence={"direction": "FLAT"},
                    )
                },
                candidate=candidate,
                policy_id=self.policy_id,
            )

        if candidate.direction not in (SignalDirection.LONG, SignalDirection.SHORT):
            return JudgeDecision(
                verdict=JudgeVerdict.REJECT,
                symbol=candidate.symbol,
                strategy_id=candidate.strategy_id,
                direction=candidate.direction,
                confidence=candidate.confidence,
                score=0.0,
                reason_codes=("UNSUPPORTED_DIRECTION",),
                explanation=f"Signal direction '{candidate.direction}' is unsupported for trade decisions",
                factor_evidence={},
                candidate=candidate,
                policy_id=self.policy_id,
            )

        # 4. Context Extraction and Consistency
        analysis: MarketAnalysis | None = None
        context_symbol: str | None = None

        if isinstance(context, StrategyContext):
            analysis = context.analysis
            context_symbol = context.symbol
        elif isinstance(context, MarketAnalysis):
            analysis = context
            context_symbol = context.symbol
        elif context is not None:
            return JudgeDecision(
                verdict=JudgeVerdict.REJECT,
                symbol=candidate.symbol,
                strategy_id=candidate.strategy_id,
                direction=candidate.direction,
                confidence=candidate.confidence,
                score=0.0,
                reason_codes=("INVALID_CONTEXT",),
                explanation=f"Context must be StrategyContext or MarketAnalysis, got {type(context).__name__}",
                factor_evidence={},
                candidate=candidate,
                policy_id=self.policy_id,
            )

        if context_symbol is not None and candidate.symbol != context_symbol:
            return JudgeDecision(
                verdict=JudgeVerdict.REJECT,
                symbol=candidate.symbol,
                strategy_id=candidate.strategy_id,
                direction=candidate.direction,
                confidence=candidate.confidence,
                score=0.0,
                reason_codes=("SYMBOL_MISMATCH",),
                explanation=f"Candidate symbol '{candidate.symbol}' does not match context symbol '{context_symbol}'",
                factor_evidence={
                    "symbol_consistency": FactorEvidence(
                        factor_name="symbol_consistency",
                        status="FAILED",
                        score=0.0,
                        reason="Candidate and context symbols do not match",
                        evidence={"candidate_symbol": candidate.symbol, "context_symbol": context_symbol},
                    )
                },
                candidate=candidate,
                policy_id=self.policy_id,
            )

        if analysis is not None and not analysis.success:
            err_msg = analysis.error or "Analysis unsuccessful"
            return JudgeDecision(
                verdict=JudgeVerdict.REJECT,
                symbol=candidate.symbol,
                strategy_id=candidate.strategy_id,
                direction=candidate.direction,
                confidence=candidate.confidence,
                score=0.0,
                reason_codes=("MARKET_ANALYSIS_FAILED",),
                explanation=f"Market analysis failed: {err_msg}",
                factor_evidence={
                    "market_analysis": FactorEvidence(
                        factor_name="market_analysis",
                        status="FAILED",
                        score=0.0,
                        reason=err_msg,
                        evidence={"analysis_error": err_msg},
                    )
                },
                candidate=candidate,
                policy_id=self.policy_id,
            )

        # 5. Multi-Factor Evidence Evaluation
        factor_evidence_map: dict[str, FactorEvidence] = {}
        fatal_reason_codes: list[str] = []

        # --- Factor A: Confidence Threshold ---
        c_conf = candidate.confidence
        if c_conf < self.min_confidence:
            conf_status = "FAILED"
            conf_score = c_conf
            conf_reason = f"Candidate confidence {c_conf:.2f} is below minimum threshold {self.min_confidence:.2f}"
            fatal_reason_codes.append("LOW_CONFIDENCE")
        else:
            conf_status = "PASSED"
            conf_score = c_conf
            conf_reason = f"Candidate confidence {c_conf:.2f} satisfies minimum threshold {self.min_confidence:.2f}"

        factor_evidence_map["confidence_threshold"] = FactorEvidence(
            factor_name="confidence_threshold",
            status=conf_status,
            score=conf_score,
            reason=conf_reason,
            evidence={"confidence": c_conf, "threshold": self.min_confidence},
        )

        # --- Factor B: Regime Alignment ---
        regime_status, regime_score, regime_reason, regime_meta, regime_failed = self._evaluate_regime_alignment(
            candidate=candidate,
            analysis=analysis,
        )
        factor_evidence_map["regime_alignment"] = FactorEvidence(
            factor_name="regime_alignment",
            status=regime_status,
            score=regime_score,
            reason=regime_reason,
            evidence=regime_meta,
        )
        if regime_failed:
            fatal_reason_codes.append("REGIME_CONTRADICTION")

        # --- Factor C: Volatility Context ---
        vol_status, vol_score, vol_reason, vol_meta, vol_code = self._evaluate_volatility_context(
            analysis=analysis,
        )
        factor_evidence_map["volatility_context"] = FactorEvidence(
            factor_name="volatility_context",
            status=vol_status,
            score=vol_score,
            reason=vol_reason,
            evidence=vol_meta,
        )
        if vol_code is not None:
            fatal_reason_codes.append(vol_code)

        # --- Factor D: Feature Confluence ---
        conf_status_d, conf_score_d, conf_reason_d, conf_meta_d, conf_failed_d = self._evaluate_feature_confluence(
            candidate=candidate,
            analysis=analysis,
        )
        factor_evidence_map["feature_confluence"] = FactorEvidence(
            factor_name="feature_confluence",
            status=conf_status_d,
            score=conf_score_d,
            reason=conf_reason_d,
            evidence=conf_meta_d,
        )
        if conf_failed_d:
            fatal_reason_codes.append("CONFLUENCE_CONTRADICTION")

        # 6. Overall Multi-Factor Evidence Suitability Score
        overall_score = round(
            (conf_score * self.WEIGHT_CONFIDENCE)
            + (regime_score * self.WEIGHT_REGIME)
            + (vol_score * self.WEIGHT_VOLATILITY)
            + (conf_score_d * self.WEIGHT_CONFLUENCE),
            4,
        )
        # Ensure bounded in [0.0, 1.0]
        overall_score = max(0.0, min(1.0, overall_score))

        # 7. Final Decision Formulation
        if fatal_reason_codes:
            verdict = JudgeVerdict.REJECT
            reason_codes = tuple(fatal_reason_codes)
            explanation = f"Judge rejected {candidate.symbol} ({candidate.direction.value}) due to: {', '.join(fatal_reason_codes)}"
        elif overall_score < self.decision_score_threshold:
            verdict = JudgeVerdict.REJECT
            reason_codes = ("INSUFFICIENT_EVIDENCE_SCORE",)
            explanation = f"Judge rejected {candidate.symbol} ({candidate.direction.value}): evidence suitability score {overall_score:.4f} is below threshold {self.decision_score_threshold:.2f}"
        else:
            verdict = JudgeVerdict.PASS
            reason_codes = ("CRITERIA_SATISFIED",)
            explanation = f"Judge passed {candidate.symbol} ({candidate.direction.value}) with evidence suitability score {overall_score:.4f} >= {self.decision_score_threshold:.2f}"

        return JudgeDecision(
            verdict=verdict,
            symbol=candidate.symbol,
            strategy_id=candidate.strategy_id,
            direction=candidate.direction,
            confidence=candidate.confidence,
            score=overall_score,
            reason_codes=reason_codes,
            explanation=explanation,
            factor_evidence=factor_evidence_map,
            candidate=candidate,
            policy_id=self.policy_id,
            metadata={
                "reference_timeframe": self.reference_timeframe,
                "decision_score_threshold": self.decision_score_threshold,
                "min_confidence": self.min_confidence,
            },
        )

    def _evaluate_regime_alignment(
        self,
        candidate: ValidatedCandidate,
        analysis: MarketAnalysis | None,
    ) -> tuple[str, float, str, dict[str, Any], bool]:
        if analysis is None or not analysis.regimes_by_granularity:
            return (
                "UNAVAILABLE",
                0.5,
                "Market regime evidence is unavailable (no analysis provided)",
                {"timeframe": self.reference_timeframe, "regime": "unavailable"},
                False,
            )

        tf_regime = analysis.regimes_by_granularity.get(self.reference_timeframe)
        if tf_regime is None or tf_regime.regime == Regime.UNKNOWN:
            reg_val = tf_regime.regime.value if tf_regime else "unavailable"
            return (
                "UNAVAILABLE",
                0.5,
                f"Market regime for '{self.reference_timeframe}' is {reg_val.upper()}",
                {"timeframe": self.reference_timeframe, "regime": reg_val},
                False,
            )

        regime = tf_regime.regime
        direction = candidate.direction
        strategy_id = candidate.strategy_id

        # Strategy-aware interpretation
        if strategy_id == "rsi_mean_reversion":
            # Mean reversion thrives in NEUTRAL ranging regime
            if regime == Regime.NEUTRAL:
                return (
                    "PASSED",
                    1.0,
                    f"RSI mean-reversion {direction.value} aligns with NEUTRAL ranging regime",
                    {"timeframe": self.reference_timeframe, "regime": regime.value},
                    False,
                )
            elif regime == Regime.BULLISH:
                if direction == SignalDirection.LONG:
                    # Buying dips in bullish trend
                    return (
                        "PASSED",
                        0.9,
                        "RSI mean-reversion LONG dip buying aligns with overall BULLISH trend",
                        {"timeframe": self.reference_timeframe, "regime": regime.value},
                        False,
                    )
                else:
                    # Shorting overbought in strong bull trend carries counter-trend risk
                    return (
                        "CAUTION",
                        0.5,
                        "RSI mean-reversion SHORT in BULLISH trend has counter-trend momentum risk",
                        {"timeframe": self.reference_timeframe, "regime": regime.value},
                        False,
                    )
            elif regime == Regime.BEARISH:
                if direction == SignalDirection.SHORT:
                    # Selling rallies in bear market
                    return (
                        "PASSED",
                        0.9,
                        "RSI mean-reversion SHORT rally selling aligns with overall BEARISH trend",
                        {"timeframe": self.reference_timeframe, "regime": regime.value},
                        False,
                    )
                else:
                    # Buying oversold in bear market carries counter-trend risk
                    return (
                        "CAUTION",
                        0.5,
                        "RSI mean-reversion LONG in BEARISH trend has counter-trend momentum risk",
                        {"timeframe": self.reference_timeframe, "regime": regime.value},
                        False,
                    )
        else:
            # Trend following / generic strategies (e.g. ema_trend_following)
            if direction == SignalDirection.LONG:
                if regime == Regime.BULLISH:
                    return (
                        "PASSED",
                        1.0,
                        "LONG trend signal fully aligns with BULLISH regime",
                        {"timeframe": self.reference_timeframe, "regime": regime.value},
                        False,
                    )
                elif regime == Regime.BEARISH:
                    return (
                        "FAILED",
                        0.0,
                        "LONG trend signal directly contradicts prevailing BEARISH regime",
                        {"timeframe": self.reference_timeframe, "regime": regime.value},
                        True,
                    )
                elif regime == Regime.NEUTRAL:
                    if self.require_regime_alignment:
                        return (
                            "FAILED",
                            0.2,
                            "LONG trend signal lacks required directional trend in NEUTRAL regime",
                            {"timeframe": self.reference_timeframe, "regime": regime.value},
                            True,
                        )
                    else:
                        return (
                            "CAUTION",
                            0.4,
                            "LONG trend signal in NEUTRAL regime",
                            {"timeframe": self.reference_timeframe, "regime": regime.value},
                            False,
                        )
            elif direction == SignalDirection.SHORT:
                if regime == Regime.BEARISH:
                    return (
                        "PASSED",
                        1.0,
                        "SHORT trend signal fully aligns with BEARISH regime",
                        {"timeframe": self.reference_timeframe, "regime": regime.value},
                        False,
                    )
                elif regime == Regime.BULLISH:
                    return (
                        "FAILED",
                        0.0,
                        "SHORT trend signal directly contradicts prevailing BULLISH regime",
                        {"timeframe": self.reference_timeframe, "regime": regime.value},
                        True,
                    )
                elif regime == Regime.NEUTRAL:
                    if self.require_regime_alignment:
                        return (
                            "FAILED",
                            0.2,
                            "SHORT trend signal lacks required directional trend in NEUTRAL regime",
                            {"timeframe": self.reference_timeframe, "regime": regime.value},
                            True,
                        )
                    else:
                        return (
                            "CAUTION",
                            0.4,
                            "SHORT trend signal in NEUTRAL regime",
                            {"timeframe": self.reference_timeframe, "regime": regime.value},
                            False,
                        )

        return (
            "PASSED",
            0.5,
            f"Regime {regime.value} evaluated for {direction.value}",
            {"timeframe": self.reference_timeframe, "regime": regime.value},
            False,
        )

    def _evaluate_volatility_context(
        self,
        analysis: MarketAnalysis | None,
    ) -> tuple[str, float, str, dict[str, Any], str | None]:
        if analysis is None or not analysis.features_by_granularity:
            return (
                "UNAVAILABLE",
                0.5,
                "Volatility features unavailable (no features present)",
                {"timeframe": self.reference_timeframe},
                None,
            )

        tf_features = analysis.features_by_granularity.get(self.reference_timeframe)
        if tf_features is None or not tf_features.values:
            return (
                "UNAVAILABLE",
                0.5,
                f"Volatility features unavailable for timeframe '{self.reference_timeframe}'",
                {"timeframe": self.reference_timeframe},
                None,
            )

        values = tf_features.values
        # Look for ATR key (e.g. atr_14)
        atr_val = values.get("atr_14")
        if atr_val is None:
            # Check any other atr_* key
            for k, v in values.items():
                if k.startswith("atr_"):
                    atr_val = v
                    break

        price_val = values.get("last_close")

        # If ATR or price is absent or not numeric
        if atr_val is None or price_val is None:
            return (
                "UNAVAILABLE",
                0.5,
                "ATR or price feature unavailable for relative volatility calculation",
                {"timeframe": self.reference_timeframe, "has_atr": atr_val is not None, "has_price": price_val is not None},
                None,
            )

        if not isinstance(atr_val, (int, float)) or not isinstance(price_val, (int, float)) or isinstance(atr_val, bool) or isinstance(price_val, bool):
            return (
                "UNAVAILABLE",
                0.5,
                "Non-numeric ATR or price feature encountered",
                {"timeframe": self.reference_timeframe},
                None,
            )

        f_atr = float(atr_val)
        f_price = float(price_val)

        if f_price <= 0 or f_atr <= 0:
            return (
                "FAILED",
                0.0,
                f"Invalid non-positive ATR ({f_atr}) or price ({f_price})",
                {"atr": f_atr, "last_close": f_price},
                "INSUFFICIENT_VOLATILITY",
            )

        relative_atr = f_atr / f_price

        if self.max_relative_atr is not None and relative_atr > self.max_relative_atr:
            return (
                "FAILED",
                0.0,
                f"Relative ATR {relative_atr:.4f} exceeds maximum threshold {self.max_relative_atr:.4f}",
                {"atr": f_atr, "last_close": f_price, "relative_atr": round(relative_atr, 6), "max_threshold": self.max_relative_atr},
                "EXCESSIVE_VOLATILITY",
            )

        if self.min_relative_atr is not None and relative_atr < self.min_relative_atr:
            return (
                "FAILED",
                0.0,
                f"Relative ATR {relative_atr:.6f} is below minimum threshold {self.min_relative_atr:.6f}",
                {"atr": f_atr, "last_close": f_price, "relative_atr": round(relative_atr, 6), "min_threshold": self.min_relative_atr},
                "INSUFFICIENT_VOLATILITY",
            )

        return (
            "PASSED",
            1.0,
            f"Relative ATR {relative_atr:.4f} is within acceptable volatility boundaries",
            {"atr": f_atr, "last_close": f_price, "relative_atr": round(relative_atr, 6)},
            None,
        )

    def _evaluate_feature_confluence(
        self,
        candidate: ValidatedCandidate,
        analysis: MarketAnalysis | None,
    ) -> tuple[str, float, str, dict[str, Any], bool]:
        if analysis is None or not analysis.features_by_granularity:
            return (
                "UNAVAILABLE",
                0.5,
                "Confluence features unavailable (no features present)",
                {"timeframe": self.reference_timeframe},
                False,
            )

        tf_features = analysis.features_by_granularity.get(self.reference_timeframe)
        if tf_features is None or not tf_features.values:
            return (
                "UNAVAILABLE",
                0.5,
                f"Confluence features unavailable for timeframe '{self.reference_timeframe}'",
                {"timeframe": self.reference_timeframe},
                False,
            )

        values = tf_features.values
        direction = candidate.direction
        contradictions = []

        # 1. RSI Extreme Overbought/Oversold Contradiction check
        rsi_val = values.get("rsi_14")
        if isinstance(rsi_val, (int, float)) and not isinstance(rsi_val, bool):
            f_rsi = float(rsi_val)
            # LONG into severe overbought (>= 80.0)
            if direction == SignalDirection.LONG and f_rsi >= 80.0:
                contradictions.append(f"LONG signal contradicts extreme overbought RSI ({f_rsi:.1f} >= 80.0)")
            # SHORT into severe oversold (<= 20.0)
            elif direction == SignalDirection.SHORT and f_rsi <= 20.0:
                contradictions.append(f"SHORT signal contradicts extreme oversold RSI ({f_rsi:.1f} <= 20.0)")

        # 2. EMA Directional Alignment check for trend-following strategies
        if candidate.strategy_id == "ema_trend_following":
            ema9 = values.get("ema_9")
            ema21 = values.get("ema_21")
            if (
                isinstance(ema9, (int, float))
                and isinstance(ema21, (int, float))
                and not isinstance(ema9, bool)
                and not isinstance(ema21, bool)
            ):
                f_ema9 = float(ema9)
                f_ema21 = float(ema21)
                if direction == SignalDirection.LONG and f_ema9 < f_ema21:
                    contradictions.append(f"LONG trend signal contradicts bearish EMA cross (EMA9 {f_ema9:.2f} < EMA21 {f_ema21:.2f})")
                elif direction == SignalDirection.SHORT and f_ema9 > f_ema21:
                    contradictions.append(f"SHORT trend signal contradicts bullish EMA cross (EMA9 {f_ema9:.2f} > EMA21 {f_ema21:.2f})")

        if contradictions:
            reason = "; ".join(contradictions)
            return (
                "FAILED",
                0.1,
                f"Confluence contradiction detected: {reason}",
                {"contradictions": contradictions},
                True,
            )

        # Check if at least one technical feature was verified
        has_tech_feature = "rsi_14" in values or "ema_9" in values
        if has_tech_feature:
            return (
                "PASSED",
                1.0,
                "Technical indicator confluence confirmed without contradictions",
                {"confluence_verified": True},
                False,
            )

        return (
            "UNAVAILABLE",
            0.5,
            "Relevant confluence indicator features not present",
            {"confluence_verified": False},
            False,
        )
