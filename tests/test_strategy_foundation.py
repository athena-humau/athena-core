from datetime import datetime, timezone
import unittest

from market.analysis_models import MarketAnalysis
from regime.models import Regime, TimeframeRegime
from scanner.ranking_engine import RankedMarket
from strategy.base import BaseStrategy
from strategy.context import StrategyContext
from strategy.models import Signal, SignalDirection
from strategy.registry import StrategyRegistry


class MockRegimeAlignmentStrategy(BaseStrategy):
    """
    Deterministic reference strategy testing the contract.
    Issues LONG if 1H and 4H regimes are BULLISH, SHORT if both BEARISH, otherwise FLAT.
    """
    strategy_id = "test_regime_alignment"
    name = "Test Regime Alignment Strategy"
    description = "Test reference strategy verifying contract compliance"

    def evaluate(self, context: StrategyContext) -> Signal:
        if not context.analysis.success:
            return Signal(
                symbol=context.symbol,
                direction=SignalDirection.FLAT,
                strategy_id=self.strategy_id,
                timestamp=context.timestamp,
                confidence=0.0,
                reason="Market analysis failed",
            )

        regimes = context.analysis.regimes_by_granularity
        r1h = regimes.get("1H")
        r4h = regimes.get("4H")

        if r1h and r4h:
            if r1h.regime == Regime.BULLISH and r4h.regime == Regime.BULLISH:
                return Signal(
                    symbol=context.symbol,
                    direction=SignalDirection.LONG,
                    strategy_id=self.strategy_id,
                    timestamp=context.timestamp,
                    confidence=0.8,
                    reason="Bullish alignment on 1H and 4H",
                    metadata={"1H": r1h.regime.value, "4H": r4h.regime.value},
                )
            elif r1h.regime == Regime.BEARISH and r4h.regime == Regime.BEARISH:
                return Signal(
                    symbol=context.symbol,
                    direction=SignalDirection.SHORT,
                    strategy_id=self.strategy_id,
                    timestamp=context.timestamp,
                    confidence=0.8,
                    reason="Bearish alignment on 1H and 4H",
                    metadata={"1H": r1h.regime.value, "4H": r4h.regime.value},
                )

        return Signal(
            symbol=context.symbol,
            direction=SignalDirection.FLAT,
            strategy_id=self.strategy_id,
            timestamp=context.timestamp,
            confidence=0.0,
            reason="No multi-timeframe regime alignment",
        )


def make_market_analysis(symbol: str, success: bool = True, regimes=None) -> MarketAnalysis:
    if regimes is None:
        regimes = {}
    return MarketAnalysis(
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
        features_by_granularity={},
        regimes_by_granularity=regimes,
        success=success,
        error=None if success else "Mock error",
        analyzed_at=datetime.now(timezone.utc),
    )


class TestStrategyModelsAndContract(unittest.TestCase):

    def test_signal_valid_instantiation(self):
        now = datetime.now(timezone.utc)
        sig = Signal(
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            strategy_id="test_strat",
            timestamp=now,
            confidence=0.75,
            reason="Breakout signal",
            metadata={"source": "test"},
        )
        self.assertEqual(sig.symbol, "BTCUSDT")
        self.assertEqual(sig.direction, SignalDirection.LONG)
        self.assertEqual(sig.strategy_id, "test_strat")
        self.assertEqual(sig.confidence, 0.75)
        self.assertEqual(sig.reason, "Breakout signal")
        self.assertEqual(sig.metadata["source"], "test")

    def test_signal_immutability(self):
        sig = Signal(
            symbol="BTCUSDT",
            direction=SignalDirection.FLAT,
            strategy_id="test_strat",
            timestamp=datetime.now(timezone.utc),
            metadata={"key": "val"},
        )
        with self.assertRaises(TypeError):
            sig.metadata["key"] = "new_val"

        with self.assertRaises(Exception):
            sig.symbol = "ETHUSDT"

    def test_signal_validation_failures(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            Signal("", SignalDirection.LONG, "id", now)
        with self.assertRaises(ValueError):
            Signal("BTCUSDT", "INVALID", "id", now)
        with self.assertRaises(ValueError):
            Signal("BTCUSDT", SignalDirection.LONG, "", now)
        with self.assertRaises(ValueError):
            Signal("BTCUSDT", SignalDirection.LONG, "id", "not-a-datetime")
        with self.assertRaises(ValueError):
            Signal("BTCUSDT", SignalDirection.LONG, "id", now, confidence=1.5)
        with self.assertRaises(ValueError):
            Signal("BTCUSDT", SignalDirection.LONG, "id", now, confidence=-0.1)

    def test_strategy_context_contract(self):
        analysis = make_market_analysis("BTCUSDT")
        ctx = StrategyContext(analysis=analysis, parameters={"param1": 10})
        self.assertEqual(ctx.symbol, "BTCUSDT")
        self.assertEqual(ctx.analysis, analysis)
        self.assertEqual(ctx.parameters["param1"], 10)

        with self.assertRaises(TypeError):
            ctx.parameters["param1"] = 20

        with self.assertRaises(ValueError):
            StrategyContext(analysis="not_an_analysis")

    def test_base_strategy_requires_strategy_id(self):
        class InvalidStrategy(BaseStrategy):
            strategy_id = ""
            def evaluate(self, context):
                pass

        with self.assertRaises(ValueError):
            InvalidStrategy()

    def test_strategy_evaluation_contract(self):
        strategy = MockRegimeAlignmentStrategy()
        analysis = make_market_analysis(
            symbol="BTCUSDT",
            regimes={
                "1H": TimeframeRegime("BTCUSDT", "1H", None, 1, Regime.BULLISH),
                "4H": TimeframeRegime("BTCUSDT", "4H", None, 1, Regime.BULLISH),
            },
        )
        ctx = StrategyContext(analysis=analysis)
        signal = strategy.evaluate(ctx)

        self.assertIsInstance(signal, Signal)
        self.assertEqual(signal.symbol, "BTCUSDT")
        self.assertEqual(signal.direction, SignalDirection.LONG)
        self.assertEqual(signal.confidence, 0.8)
        self.assertEqual(signal.strategy_id, "test_regime_alignment")

    def test_strategy_evaluation_bearish_and_flat(self):
        strategy = MockRegimeAlignmentStrategy()

        # Bearish alignment
        analysis_bearish = make_market_analysis(
            symbol="ETHUSDT",
            regimes={
                "1H": TimeframeRegime("ETHUSDT", "1H", None, 1, Regime.BEARISH),
                "4H": TimeframeRegime("ETHUSDT", "4H", None, 1, Regime.BEARISH),
            },
        )
        sig_bearish = strategy.evaluate(StrategyContext(analysis=analysis_bearish))
        self.assertEqual(sig_bearish.direction, SignalDirection.SHORT)

        # Conflicting regimes -> FLAT
        analysis_conflict = make_market_analysis(
            symbol="SOLUSDT",
            regimes={
                "1H": TimeframeRegime("SOLUSDT", "1H", None, 1, Regime.BULLISH),
                "4H": TimeframeRegime("SOLUSDT", "4H", None, 1, Regime.BEARISH),
            },
        )
        sig_flat = strategy.evaluate(StrategyContext(analysis=analysis_conflict))
        self.assertEqual(sig_flat.direction, SignalDirection.FLAT)

        # Failed analysis -> FLAT
        analysis_failed = make_market_analysis("XRPUSDT", success=False)
        sig_failed = strategy.evaluate(StrategyContext(analysis=analysis_failed))
        self.assertEqual(sig_failed.direction, SignalDirection.FLAT)
        self.assertEqual(sig_failed.reason, "Market analysis failed")

    def test_strategy_registry(self):
        registry = StrategyRegistry()
        strat = MockRegimeAlignmentStrategy()

        self.assertEqual(len(registry), 0)
        registry.register(strat)
        self.assertEqual(len(registry), 1)
        self.assertTrue(registry.has("test_regime_alignment"))
        self.assertEqual(registry.get("test_regime_alignment"), strat)
        self.assertEqual(registry.list_strategies(), ["test_regime_alignment"])

        # Duplicate registration raises ValueError
        with self.assertRaises(ValueError):
            registry.register(strat)

        # Invalid registration
        with self.assertRaises(ValueError):
            registry.register("not_a_strategy")

        # Unknown lookup
        with self.assertRaises(KeyError):
            registry.get("unknown_strategy")

        # Unregister and clear
        registry.unregister("test_regime_alignment")
        self.assertFalse(registry.has("test_regime_alignment"))
        self.assertEqual(len(registry), 0)


if __name__ == "__main__":
    unittest.main()
