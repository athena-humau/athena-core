from datetime import datetime, timezone
from types import MappingProxyType
import unittest

from ai.deterministic_evaluator import DeterministicStrategyEvaluator
from ai.deterministic_ranker import DeterministicStrategyRanker
from ai.deterministic_selector import DeterministicStrategySelector
from ai.deterministic_validator import DeterministicSignalValidator
from ai.models import (
    StrategyCandidate,
    StrategySelection,
    ValidatedCandidate,
    ValidationStatus,
)
from ai.signal_validator import SignalValidator
from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from regime.models import Regime, TimeframeRegime
from scanner.ranking_engine import RankedMarket
from strategy.context import StrategyContext
from strategy.ema_trend_following import EMATrendFollowingStrategy
from strategy.models import Signal, SignalDirection
from strategy.registry import StrategyRegistry
from strategy.rsi_mean_reversion import RSIMeanReversionStrategy


def make_test_context(
    symbol: str = "BTCUSDT",
    regime: Regime = Regime.BULLISH,
    ema_9: float = 65000.0,
    ema_21: float = 64000.0,
    last_close: float = 65500.0,
    rsi_14: float = 50.0,
) -> StrategyContext:
    tf_regime = TimeframeRegime(
        symbol=symbol,
        granularity="1H",
        timestamp=datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc),
        candle_count=50,
        regime=regime,
    )
    tf_features = TimeframeFeatures(
        symbol=symbol,
        granularity="1H",
        timestamp=datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc),
        candle_count=50,
        values={
            "ema_9": ema_9,
            "ema_21": ema_21,
            "last_close": last_close,
            "rsi_14": rsi_14,
        },
    )
    analysis = MarketAnalysis(
        symbol=symbol,
        ranking=RankedMarket(
            symbol=symbol,
            score=85.0,
            liquidity_score=90.0,
            movement_score=80.0,
            spread_score=85.0,
            open_interest_score=75.0,
            funding_score=95.0,
        ),
        candles_by_granularity={},
        features_by_granularity={"1H": tf_features},
        regimes_by_granularity={"1H": tf_regime},
        success=True,
        error=None,
        analyzed_at=datetime.now(timezone.utc),
    )
    return StrategyContext(analysis=analysis)


def make_test_signal(
    symbol: str = "BTCUSDT",
    direction: SignalDirection = SignalDirection.LONG,
    strategy_id: str = "ema_trend_following",
    confidence: float = 0.8,
    reason: str = "Valid test signal",
    timestamp: datetime | None = None,
    metadata: dict | None = None,
) -> Signal:
    return Signal(
        symbol=symbol,
        direction=direction,
        strategy_id=strategy_id,
        timestamp=timestamp or datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc),
        confidence=confidence,
        reason=reason,
        metadata=metadata or {"source": "test"},
    )


class TestSignalValidator(unittest.TestCase):

    def setUp(self):
        self.validator = DeterministicSignalValidator()
        self.context = make_test_context()

    # =========================================================================
    # 1. CONTRACT AND MODEL INTEGRITY
    # =========================================================================

    def test_validator_contract(self):
        self.assertIsInstance(self.validator, SignalValidator)
        self.assertEqual(self.validator.validator_id, "deterministic_signal_validator")
        self.assertIn("Deterministic", self.validator.name)

    def test_validated_candidate_immutability(self):
        sig = make_test_signal()
        res = self.validator.validate(sig, context=self.context, selected_strategy_id="ema_trend_following")

        self.assertIsInstance(res, ValidatedCandidate)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.PASSED)

        # Attempting to mutate attributes must fail (frozen dataclass)
        with self.assertRaises(Exception):
            res.status = ValidationStatus.FAILED
        with self.assertRaises(Exception):
            res.symbol = "ETHUSDT"

        # Metadata must be immutable MappingProxyType
        self.assertIsInstance(res.metadata, MappingProxyType)
        with self.assertRaises(TypeError):
            res.metadata["forbidden"] = True

    # =========================================================================
    # 2. VALID SIGNALS (A, B, C, D, E)
    # =========================================================================

    def test_a_valid_long_signal(self):
        sig = make_test_signal(direction=SignalDirection.LONG)
        res = self.validator.validate(sig, context=self.context, selected_strategy_id="ema_trend_following")
        self.assertEqual(res.status, ValidationStatus.PASSED)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.direction, SignalDirection.LONG)
        self.assertEqual(res.failures, ())

    def test_b_valid_short_signal(self):
        sig = make_test_signal(direction=SignalDirection.SHORT)
        res = self.validator.validate(sig, context=self.context, selected_strategy_id="ema_trend_following")
        self.assertEqual(res.status, ValidationStatus.PASSED)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.direction, SignalDirection.SHORT)
        self.assertEqual(res.failures, ())

    def test_c_valid_flat_signal(self):
        # FLAT is a valid non-trading signal and must NOT be rejected
        sig = make_test_signal(direction=SignalDirection.FLAT, confidence=0.0)
        res = self.validator.validate(sig, context=self.context, selected_strategy_id="ema_trend_following")
        self.assertEqual(res.status, ValidationStatus.PASSED)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.direction, SignalDirection.FLAT)
        self.assertEqual(res.confidence, 0.0)
        self.assertEqual(res.failures, ())

    def test_d_confidence_zero(self):
        sig = make_test_signal(confidence=0.0)
        res = self.validator.validate(sig, context=self.context, selected_strategy_id="ema_trend_following")
        self.assertEqual(res.status, ValidationStatus.PASSED)
        self.assertEqual(res.confidence, 0.0)

    def test_e_confidence_one(self):
        sig = make_test_signal(confidence=1.0)
        res = self.validator.validate(sig, context=self.context, selected_strategy_id="ema_trend_following")
        self.assertEqual(res.status, ValidationStatus.PASSED)
        self.assertEqual(res.confidence, 1.0)

    # =========================================================================
    # 3. BOUNDARY AND STRUCTURAL FAILURES (F through Q)
    # =========================================================================

    def test_f_confidence_below_zero_rejected(self):
        # Normal constructor rejects confidence < 0
        with self.assertRaises(ValueError):
            make_test_signal(confidence=-0.1)

        # Validator also defensively checks any object with negative confidence
        raw_obj = object.__new__(Signal)
        object.__setattr__(raw_obj, "symbol", "BTCUSDT")
        object.__setattr__(raw_obj, "direction", SignalDirection.LONG)
        object.__setattr__(raw_obj, "strategy_id", "ema_trend_following")
        object.__setattr__(raw_obj, "timestamp", datetime.now(timezone.utc))
        object.__setattr__(raw_obj, "confidence", -0.05)
        object.__setattr__(raw_obj, "reason", "Negative confidence")
        object.__setattr__(raw_obj, "metadata", {})

        res = self.validator.validate(raw_obj, context=self.context)
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("below 0.0" in f for f in res.failures))

    def test_g_confidence_above_one_rejected(self):
        with self.assertRaises(ValueError):
            make_test_signal(confidence=1.4)

        raw_obj = object.__new__(Signal)
        object.__setattr__(raw_obj, "symbol", "BTCUSDT")
        object.__setattr__(raw_obj, "direction", SignalDirection.LONG)
        object.__setattr__(raw_obj, "strategy_id", "ema_trend_following")
        object.__setattr__(raw_obj, "timestamp", datetime.now(timezone.utc))
        object.__setattr__(raw_obj, "confidence", 1.4)
        object.__setattr__(raw_obj, "reason", "Over-confidence")
        object.__setattr__(raw_obj, "metadata", {})

        res = self.validator.validate(raw_obj, context=self.context)
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("above 1.0" in f for f in res.failures))

    def test_h_missing_and_empty_symbol_rejected(self):
        with self.assertRaises(ValueError):
            make_test_signal(symbol="")

        raw_obj = object.__new__(Signal)
        object.__setattr__(raw_obj, "symbol", "")
        object.__setattr__(raw_obj, "direction", SignalDirection.LONG)
        object.__setattr__(raw_obj, "strategy_id", "ema_trend_following")
        object.__setattr__(raw_obj, "timestamp", datetime.now(timezone.utc))
        object.__setattr__(raw_obj, "confidence", 0.5)
        object.__setattr__(raw_obj, "reason", "Empty symbol")
        object.__setattr__(raw_obj, "metadata", {})

        res = self.validator.validate(raw_obj, context=self.context)
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertTrue(any("symbol" in f for f in res.failures))

    def test_j_missing_strategy_id_rejected(self):
        with self.assertRaises(ValueError):
            make_test_signal(strategy_id="")

        raw_obj = object.__new__(Signal)
        object.__setattr__(raw_obj, "symbol", "BTCUSDT")
        object.__setattr__(raw_obj, "direction", SignalDirection.LONG)
        object.__setattr__(raw_obj, "strategy_id", "")
        object.__setattr__(raw_obj, "timestamp", datetime.now(timezone.utc))
        object.__setattr__(raw_obj, "confidence", 0.5)
        object.__setattr__(raw_obj, "reason", "Missing strategy id")
        object.__setattr__(raw_obj, "metadata", {})

        res = self.validator.validate(raw_obj, context=self.context)
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertTrue(any("strategy_id" in f for f in res.failures))

    def test_k_strategy_mismatch_rejected(self):
        sig = make_test_signal(strategy_id="ema_trend_following")
        # Expected strategy is rsi_mean_reversion
        res = self.validator.validate(sig, context=self.context, selected_strategy_id="rsi_mean_reversion")
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("does not match selected strategy" in f for f in res.failures))

    def test_l_symbol_mismatch_rejected(self):
        sig = make_test_signal(symbol="ETHUSDT")
        # Context symbol is BTCUSDT
        res = self.validator.validate(sig, context=self.context, selected_strategy_id="ema_trend_following")
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("does not match context symbol" in f for f in res.failures))

    def test_m_invalid_direction_rejected(self):
        raw_obj = object.__new__(Signal)
        object.__setattr__(raw_obj, "symbol", "BTCUSDT")
        object.__setattr__(raw_obj, "direction", "BUY")  # Not SignalDirection
        object.__setattr__(raw_obj, "strategy_id", "ema_trend_following")
        object.__setattr__(raw_obj, "timestamp", datetime.now(timezone.utc))
        object.__setattr__(raw_obj, "confidence", 0.5)
        object.__setattr__(raw_obj, "reason", "Invalid direction string")
        object.__setattr__(raw_obj, "metadata", {})

        res = self.validator.validate(raw_obj, context=self.context)
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertTrue(any("SignalDirection" in f for f in res.failures))

    def test_n_invalid_timestamp_rejected(self):
        raw_obj = object.__new__(Signal)
        object.__setattr__(raw_obj, "symbol", "BTCUSDT")
        object.__setattr__(raw_obj, "direction", SignalDirection.LONG)
        object.__setattr__(raw_obj, "strategy_id", "ema_trend_following")
        object.__setattr__(raw_obj, "timestamp", "2026-10-08T00:00:00")  # String instead of datetime
        object.__setattr__(raw_obj, "confidence", 0.5)
        object.__setattr__(raw_obj, "reason", "Invalid timestamp")
        object.__setattr__(raw_obj, "metadata", {})

        res = self.validator.validate(raw_obj, context=self.context)
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertTrue(any("timestamp" in f for f in res.failures))

    def test_o_none_signal_rejected(self):
        res = self.validator.validate(None, context=self.context)
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertFalse(res.is_valid)
        self.assertIn("Signal cannot be None", res.failures)

    def test_p_wrong_object_type_rejected(self):
        res = self.validator.validate("not_a_signal", context=self.context)
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("must be an instance of Signal" in f for f in res.failures))

    def test_q_malformed_metadata_rejected(self):
        raw_obj = object.__new__(Signal)
        object.__setattr__(raw_obj, "symbol", "BTCUSDT")
        object.__setattr__(raw_obj, "direction", SignalDirection.LONG)
        object.__setattr__(raw_obj, "strategy_id", "ema_trend_following")
        object.__setattr__(raw_obj, "timestamp", datetime.now(timezone.utc))
        object.__setattr__(raw_obj, "confidence", 0.5)
        object.__setattr__(raw_obj, "reason", "Malformed metadata")
        object.__setattr__(raw_obj, "metadata", "invalid_not_a_mapping")

        res = self.validator.validate(raw_obj, context=self.context)
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertTrue(any("metadata" in f for f in res.failures))

    # =========================================================================
    # 4. SAFETY, IMMUTABILITY & DETERMINISM (R through X)
    # =========================================================================

    def test_r_original_signal_remains_unchanged(self):
        sig = make_test_signal(confidence=0.75, metadata={"key": "val"})
        _ = self.validator.validate(sig, context=self.context, selected_strategy_id="ema_trend_following")

        self.assertEqual(sig.confidence, 0.75)
        self.assertEqual(sig.symbol, "BTCUSDT")
        self.assertEqual(sig.strategy_id, "ema_trend_following")
        self.assertEqual(sig.direction, SignalDirection.LONG)
        self.assertEqual(dict(sig.metadata), {"key": "val"})

    def test_s_validation_output_is_immutable(self):
        sig = make_test_signal()
        res = self.validator.validate(sig, context=self.context)

        with self.assertRaises(Exception):
            res.direction = SignalDirection.SHORT
        with self.assertRaises(Exception):
            res.confidence = 0.99
        with self.assertRaises(Exception):
            res.failures = ("tampered",)

    def test_t_validation_is_deterministic(self):
        sig = make_test_signal(confidence=0.85)
        results = [
            self.validator.validate(sig, context=self.context, selected_strategy_id="ema_trend_following")
            for _ in range(10)
        ]
        for r in results:
            self.assertEqual(r.status, ValidationStatus.PASSED)
            self.assertEqual(r.confidence, 0.85)
            self.assertEqual(r.failures, ())
            self.assertEqual(r.symbol, "BTCUSDT")

    def test_u_valid_signal_preserves_evidence(self):
        sig = make_test_signal(confidence=0.92, reason="Strong EMA trend breakout")
        res = self.validator.validate(sig, context=self.context, selected_strategy_id="ema_trend_following")

        self.assertEqual(res.signal, sig)
        self.assertEqual(res.confidence, 0.92)
        self.assertEqual(res.symbol, "BTCUSDT")
        self.assertEqual(res.strategy_id, "ema_trend_following")
        self.assertEqual(res.direction, SignalDirection.LONG)
        self.assertEqual(res.metadata["signal_reason"], "Strong EMA trend breakout")
        self.assertEqual(res.metadata["expected_strategy_id"], "ema_trend_following")

    def test_v_multiple_validation_failures_represented_clearly(self):
        # Simultaneously: symbol mismatch AND strategy mismatch
        sig = make_test_signal(symbol="ETHUSDT", strategy_id="rsi_mean_reversion")
        res = self.validator.validate(
            sig,
            context=self.context,  # symbol is BTCUSDT
            selected_strategy_id="ema_trend_following",  # strategy is ema_trend_following
        )
        self.assertEqual(res.status, ValidationStatus.FAILED)
        self.assertEqual(len(res.failures), 2)
        self.assertTrue(any("does not match context symbol" in f for f in res.failures))
        self.assertTrue(any("does not match selected strategy" in f for f in res.failures))

    def test_w_validator_does_not_mutate_strategy_context(self):
        original_symbol = self.context.symbol
        original_params = dict(self.context.parameters)
        sig = make_test_signal()

        _ = self.validator.validate(sig, context=self.context)

        self.assertEqual(self.context.symbol, original_symbol)
        self.assertEqual(dict(self.context.parameters), original_params)

    def test_x_validator_does_not_create_or_modify_execution_state(self):
        # Verification that validator has no execution side effects or methods
        self.assertFalse(hasattr(self.validator, "execute"))
        self.assertFalse(hasattr(self.validator, "place_order"))
        self.assertFalse(hasattr(self.validator, "position_size"))

    # =========================================================================
    # 5. END-TO-END PIPELINE INTEGRATION
    # =========================================================================

    def test_end_to_end_selection_to_signal_validation(self):
        """
        Complete analytical pipeline flow:
        MarketAnalysis -> StrategyContext -> Candidates -> Evaluator ->
        Ranker -> Selector -> Selected Strategy -> Signal -> Validator -> Validated Candidate
        """
        registry = StrategyRegistry()
        ema_strat = EMATrendFollowingStrategy()
        rsi_strat = RSIMeanReversionStrategy()
        registry.register(ema_strat)
        registry.register(rsi_strat)

        candidates = [
            StrategyCandidate("ema_trend_following", "EMA Trend Following"),
            StrategyCandidate("rsi_mean_reversion", "RSI Mean Reversion"),
        ]

        # 1. Market context with strong EMA trend alignment
        ctx = make_test_context(
            regime=Regime.BULLISH,
            ema_9=66000.0,
            ema_21=64000.0,
            last_close=67000.0,
            rsi_14=45.0,
        )

        # 2. Evaluation
        evaluator = DeterministicStrategyEvaluator(registry=registry)
        evaluations = [evaluator.evaluate(ctx, c) for c in candidates]

        # 3. Ranking
        ranker = DeterministicStrategyRanker()
        ranked = ranker.rank(evaluations)
        self.assertEqual(ranked[0].strategy_id, "ema_trend_following")

        # 4. Selection
        selector = DeterministicStrategySelector(registry=registry, evaluator=evaluator, ranker=ranker)
        selection = selector.select_from_ranked(ranked, context=ctx)
        self.assertEqual(selection.selected_strategy_id, "ema_trend_following")

        # 5. Retrieve selected strategy and generate Signal
        selected_strat = registry.get(selection.selected_strategy_id)
        signal = selected_strat.evaluate(ctx)
        self.assertIsInstance(signal, Signal)
        self.assertEqual(signal.direction, SignalDirection.LONG)

        # 6. Validate Signal via SignalValidator
        validator = DeterministicSignalValidator()
        validated_candidate = validator.validate(
            signal=signal,
            context=ctx,
            selected_strategy_id=selection,  # passes StrategySelection directly
        )

        # 7. Pre-Judge Contract Verification
        self.assertIsInstance(validated_candidate, ValidatedCandidate)
        self.assertEqual(validated_candidate.status, ValidationStatus.PASSED)
        self.assertTrue(validated_candidate.is_valid)
        self.assertEqual(validated_candidate.symbol, "BTCUSDT")
        self.assertEqual(validated_candidate.strategy_id, "ema_trend_following")
        self.assertEqual(validated_candidate.direction, SignalDirection.LONG)
        self.assertEqual(validated_candidate.failures, ())
        self.assertIsNotNone(validated_candidate.signal)
        self.assertEqual(validated_candidate.signal, signal)


if __name__ == "__main__":
    unittest.main()
