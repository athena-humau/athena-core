from datetime import datetime, timezone
from typing import Any, Mapping

from ai.models import JudgeDecision, JudgeVerdict
from risk.base import RiskEngine
from risk.models import (
    RiskAssessment,
    RiskPolicy,
    RiskVerdict,
    TradeProposal,
)
from strategy.models import SignalDirection


class DeterministicRiskEngine(RiskEngine):
    """
    Deterministic reference implementation of the ATHENA Risk Engine.

    Evaluates trade proposals against linear risk constraints:
      1. Proposal & Decision Integrity: Requires valid proposal and passing JudgeDecision.
      2. Actionable Direction: Rejects FLAT signals and undefined directions.
      3. Capital & Price Sanity: Requires positive equity, entry price, stop-loss price, quantity.
      4. Linear Stop Placement: Enforces stop strictly below entry for LONG, strictly above for SHORT.
      5. Position Notional Limit: Enforces maximum notional per trade.
      6. Aggregate Exposure Limit: Enforces maximum portfolio-level notional exposure.
      7. Equity Risk Limit: Enforces maximum dollar loss percentage per trade.
      8. Leverage Limit: Enforces upper bound on requested leverage.
      9. Margin Check: Verifies sufficient available margin if provided.
    """

    engine_id: str = "deterministic_risk_engine"
    policy_id: str = "deterministic_risk_v1"
    name: str = "Deterministic Risk Engine"
    description: str = "Evaluates trade proposals against account equity, stop-loss, notional, and leverage constraints"

    def __init__(
        self,
        policy: RiskPolicy | None = None,
        engine_id: str | None = None,
    ):
        self.policy = policy or RiskPolicy()
        if not isinstance(self.policy, RiskPolicy):
            raise ValueError("policy must be an instance of RiskPolicy")

        super().__init__(
            engine_id=engine_id or self.engine_id,
            policy_id=self.policy.policy_id,
        )

    def assess(self, proposal: Any) -> RiskAssessment:
        """
        Evaluate proposal against configured risk rules to produce an immutable RiskAssessment.
        """
        # 1. Proposal presence check
        if proposal is None:
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol="",
                strategy_id="",
                direction=None,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("MISSING_PROPOSAL",),
                explanation="Trade proposal is None",
                check_evidence={"proposal": {"status": "FAIL", "reason": "Proposal is None"}},
                proposal=None,
                policy_id=self.policy.policy_id,
            )

        if not isinstance(proposal, TradeProposal):
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol="",
                strategy_id="",
                direction=None,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("INVALID_PROPOSAL_TYPE",),
                explanation=f"Proposal must be an instance of TradeProposal, got {type(proposal).__name__}",
                check_evidence={"proposal": {"status": "FAIL", "reason": f"Expected TradeProposal, got {type(proposal).__name__}"}},
                proposal=None,
                policy_id=self.policy.policy_id,
            )

        # 2. JudgeDecision presence check
        judge_decision = proposal.judge_decision
        if judge_decision is None:
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol=proposal.symbol,
                strategy_id=proposal.strategy_id,
                direction=proposal.direction,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("MISSING_JUDGE_DECISION",),
                explanation="Trade proposal lacks an associated JudgeDecision",
                check_evidence={"judge_decision": {"status": "FAIL", "reason": "JudgeDecision is None"}},
                proposal=proposal,
                policy_id=self.policy.policy_id,
            )

        if not isinstance(judge_decision, JudgeDecision):
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol=proposal.symbol,
                strategy_id=proposal.strategy_id,
                direction=proposal.direction,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("INVALID_JUDGE_DECISION",),
                explanation=f"Associated decision is not a valid JudgeDecision instance, got {type(judge_decision).__name__}",
                check_evidence={"judge_decision": {"status": "FAIL", "reason": f"Expected JudgeDecision, got {type(judge_decision).__name__}"}},
                proposal=proposal,
                policy_id=self.policy.policy_id,
            )

        # 3. Judge verdict check
        if judge_decision.verdict != JudgeVerdict.PASS:
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol=proposal.symbol,
                strategy_id=proposal.strategy_id,
                direction=proposal.direction,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("JUDGE_VERDICT_REJECTED",),
                explanation=f"Associated Judge decision was {judge_decision.verdict.value}, not PASS",
                check_evidence={
                    "judge_verdict": {
                        "status": "FAIL",
                        "verdict": judge_decision.verdict.value,
                        "judge_reasons": list(judge_decision.reason_codes),
                    }
                },
                proposal=proposal,
                policy_id=self.policy.policy_id,
            )

        # 4. Actionable direction check
        direction = judge_decision.direction
        if direction == SignalDirection.FLAT:
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol=proposal.symbol,
                strategy_id=proposal.strategy_id,
                direction=direction,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("FLAT_SIGNAL_NOT_ACTIONABLE",),
                explanation="FLAT signals are non-actionable for position entry",
                check_evidence={"direction": {"status": "FAIL", "direction": "FLAT"}},
                proposal=proposal,
                policy_id=self.policy.policy_id,
            )

        if direction not in (SignalDirection.LONG, SignalDirection.SHORT):
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol=proposal.symbol,
                strategy_id=proposal.strategy_id,
                direction=direction,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("INVALID_DIRECTION",),
                explanation=f"Signal direction must be LONG or SHORT, got {direction}",
                check_evidence={"direction": {"status": "FAIL", "direction": str(direction)}},
                proposal=proposal,
                policy_id=self.policy.policy_id,
            )

        # 5. Account equity validity check
        if proposal.account_equity <= 0.0:
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol=proposal.symbol,
                strategy_id=proposal.strategy_id,
                direction=direction,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("INVALID_EQUITY",),
                explanation=f"Account equity must be strictly positive (got {proposal.account_equity})",
                check_evidence={"account_equity": {"status": "FAIL", "value": proposal.account_equity}},
                proposal=proposal,
                policy_id=self.policy.policy_id,
            )

        # 6. Price & quantity validity check
        if (
            proposal.entry_price <= 0.0
            or proposal.stop_loss_price <= 0.0
            or proposal.quantity <= 0.0
            or proposal.contract_multiplier <= 0.0
        ):
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol=proposal.symbol,
                strategy_id=proposal.strategy_id,
                direction=direction,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("INVALID_PRICE_OR_QUANTITY",),
                explanation="Entry price, stop loss price, quantity, and contract multiplier must be strictly positive",
                check_evidence={
                    "pricing": {
                        "status": "FAIL",
                        "entry_price": proposal.entry_price,
                        "stop_loss_price": proposal.stop_loss_price,
                        "quantity": proposal.quantity,
                        "contract_multiplier": proposal.contract_multiplier,
                    }
                },
                proposal=proposal,
                policy_id=self.policy.policy_id,
            )

        # 7. Stop placement check
        if direction == SignalDirection.LONG and proposal.stop_loss_price >= proposal.entry_price:
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol=proposal.symbol,
                strategy_id=proposal.strategy_id,
                direction=direction,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("WRONG_SIDE_STOP",),
                explanation=(
                    f"For LONG trade, stop_loss_price ({proposal.stop_loss_price}) "
                    f"must be strictly below entry_price ({proposal.entry_price})"
                ),
                check_evidence={
                    "stop_placement": {
                        "status": "FAIL",
                        "direction": direction.value,
                        "entry_price": proposal.entry_price,
                        "stop_loss_price": proposal.stop_loss_price,
                    }
                },
                proposal=proposal,
                policy_id=self.policy.policy_id,
            )

        if direction == SignalDirection.SHORT and proposal.stop_loss_price <= proposal.entry_price:
            return RiskAssessment(
                verdict=RiskVerdict.REJECT,
                symbol=proposal.symbol,
                strategy_id=proposal.strategy_id,
                direction=direction,
                notional=0.0,
                estimated_loss=0.0,
                risk_percent=0.0,
                reason_codes=("WRONG_SIDE_STOP",),
                explanation=(
                    f"For SHORT trade, stop_loss_price ({proposal.stop_loss_price}) "
                    f"must be strictly above entry_price ({proposal.entry_price})"
                ),
                check_evidence={
                    "stop_placement": {
                        "status": "FAIL",
                        "direction": direction.value,
                        "entry_price": proposal.entry_price,
                        "stop_loss_price": proposal.stop_loss_price,
                    }
                },
                proposal=proposal,
                policy_id=self.policy.policy_id,
            )

        # 8. Sizing and risk calculations
        notional = proposal.entry_price * proposal.quantity * proposal.contract_multiplier
        if direction == SignalDirection.LONG:
            loss_per_unit = proposal.entry_price - proposal.stop_loss_price
        else:
            loss_per_unit = proposal.stop_loss_price - proposal.entry_price

        estimated_loss = loss_per_unit * proposal.quantity * proposal.contract_multiplier
        risk_percent = (estimated_loss / proposal.account_equity) * 100.0

        # 9. Evaluate limits
        failures: list[str] = []
        evidence: dict[str, Any] = {
            "judge_verdict": {
                "status": "PASS",
                "verdict": judge_decision.verdict.value,
            },
            "stop_placement": {
                "status": "PASS",
                "direction": direction.value,
                "entry_price": proposal.entry_price,
                "stop_loss_price": proposal.stop_loss_price,
            },
            "pricing": {
                "status": "PASS",
                "entry_price": proposal.entry_price,
                "stop_loss_price": proposal.stop_loss_price,
                "quantity": proposal.quantity,
                "contract_multiplier": proposal.contract_multiplier,
            },
        }

        # Check Position Notional Limit
        if notional > self.policy.max_position_notional + 1e-9:
            failures.append("EXCEEDS_POSITION_NOTIONAL_LIMIT")
            evidence["position_notional"] = {
                "status": "FAIL",
                "notional": notional,
                "limit": self.policy.max_position_notional,
            }
        else:
            evidence["position_notional"] = {
                "status": "PASS",
                "notional": notional,
                "limit": self.policy.max_position_notional,
            }

        # Check Aggregate Exposure Limit
        new_aggregate_exposure = proposal.existing_exposure + notional
        if new_aggregate_exposure > self.policy.max_aggregate_exposure + 1e-9:
            failures.append("EXCEEDS_AGGREGATE_EXPOSURE_LIMIT")
            evidence["aggregate_exposure"] = {
                "status": "FAIL",
                "new_aggregate_exposure": new_aggregate_exposure,
                "existing_exposure": proposal.existing_exposure,
                "trade_notional": notional,
                "limit": self.policy.max_aggregate_exposure,
            }
        else:
            evidence["aggregate_exposure"] = {
                "status": "PASS",
                "new_aggregate_exposure": new_aggregate_exposure,
                "existing_exposure": proposal.existing_exposure,
                "trade_notional": notional,
                "limit": self.policy.max_aggregate_exposure,
            }

        # Check Symbol Exposure Limit (if defined)
        if self.policy.max_symbol_exposure is not None:
            new_symbol_exposure = proposal.existing_symbol_exposure + notional
            if new_symbol_exposure > self.policy.max_symbol_exposure + 1e-9:
                failures.append("EXCEEDS_SYMBOL_EXPOSURE_LIMIT")
                evidence["symbol_exposure"] = {
                    "status": "FAIL",
                    "new_symbol_exposure": new_symbol_exposure,
                    "existing_symbol_exposure": proposal.existing_symbol_exposure,
                    "trade_notional": notional,
                    "limit": self.policy.max_symbol_exposure,
                }
            else:
                evidence["symbol_exposure"] = {
                    "status": "PASS",
                    "new_symbol_exposure": new_symbol_exposure,
                    "existing_symbol_exposure": proposal.existing_symbol_exposure,
                    "trade_notional": notional,
                    "limit": self.policy.max_symbol_exposure,
                }

        # Check Risk Percent Limit
        if risk_percent > self.policy.max_risk_per_trade_pct + 1e-9:
            failures.append("EXCEEDS_MAX_RISK_PER_TRADE")
            evidence["risk_percent"] = {
                "status": "FAIL",
                "risk_percent": risk_percent,
                "limit": self.policy.max_risk_per_trade_pct,
                "estimated_loss": estimated_loss,
                "account_equity": proposal.account_equity,
            }
        else:
            evidence["risk_percent"] = {
                "status": "PASS",
                "risk_percent": risk_percent,
                "limit": self.policy.max_risk_per_trade_pct,
                "estimated_loss": estimated_loss,
                "account_equity": proposal.account_equity,
            }

        # Check Requested Leverage Bounds
        if proposal.requested_leverage < 1.0 - 1e-9:
            failures.append("INVALID_LEVERAGE")
            evidence["leverage"] = {
                "status": "FAIL",
                "requested_leverage": proposal.requested_leverage,
                "reason": "Requested leverage must be at least 1.0",
            }
        elif proposal.requested_leverage > self.policy.max_leverage + 1e-9:
            failures.append("EXCEEDS_MAX_LEVERAGE")
            evidence["leverage"] = {
                "status": "FAIL",
                "requested_leverage": proposal.requested_leverage,
                "limit": self.policy.max_leverage,
            }
        else:
            evidence["leverage"] = {
                "status": "PASS",
                "requested_leverage": proposal.requested_leverage,
                "limit": self.policy.max_leverage,
            }

        # Check Available Margin (if provided)
        if proposal.available_margin is not None:
            effective_leverage = max(proposal.requested_leverage, 1.0)
            required_margin = notional / effective_leverage
            if required_margin > proposal.available_margin + 1e-9:
                failures.append("INSUFFICIENT_MARGIN")
                evidence["margin"] = {
                    "status": "FAIL",
                    "required_margin": required_margin,
                    "available_margin": proposal.available_margin,
                }
            else:
                evidence["margin"] = {
                    "status": "PASS",
                    "required_margin": required_margin,
                    "available_margin": proposal.available_margin,
                }

        # 10. Generate Assessment
        if failures:
            verdict = RiskVerdict.REJECT
            reason_codes = tuple(failures)
            explanation = f"Risk constraints violated: {', '.join(failures)}"
        else:
            verdict = RiskVerdict.PASS
            reason_codes = ("RISK_CONSTRAINTS_SATISFIED",)
            explanation = "All risk constraints satisfied"

        return RiskAssessment(
            verdict=verdict,
            symbol=proposal.symbol,
            strategy_id=proposal.strategy_id,
            direction=direction,
            notional=notional,
            estimated_loss=estimated_loss,
            risk_percent=risk_percent,
            reason_codes=reason_codes,
            explanation=explanation,
            check_evidence=evidence,
            proposal=proposal,
            policy_id=self.policy.policy_id,
        )
