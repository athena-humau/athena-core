from datetime import datetime, timezone
import unittest

from data.candle import Candle
from features.calculators.atr import ATRCalculator
from features.calculators.basic_price import BasicPriceCalculator
from features.calculators.ema import EMACalculator
from features.calculators.rsi import RSICalculator
from features.feature_engine import FeatureEngine


def make_test_candle(
    index: int,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    volume: float = 100.0,
    symbol: str = "BTCUSDT",
    granularity: str = "1h",
) -> Candle:
    # 3600 seconds per index step
    ts = datetime.fromtimestamp(1700000000 + (index * 3600), tz=timezone.utc)
    return Candle(
        symbol=symbol,
        timestamp=ts,
        open=float(open_p),
        high=float(high_p),
        low=float(low_p),
        close=float(close_p),
        volume=float(volume),
        quote_volume=float(volume * close_p),
        granularity=granularity,
    )


class TestIndicatorCalculators(unittest.TestCase):

    # =========================================================================
    # EMA CALCULATOR TESTS
    # =========================================================================

    def test_ema_initialization_and_validation(self):
        ema = EMACalculator(period=10)
        self.assertEqual(ema.period, 10)
        self.assertEqual(ema.name, "ema_10")
        self.assertEqual(ema.minimum_candles, 10)

        with self.assertRaises(ValueError):
            EMACalculator(period=0)
        with self.assertRaises(ValueError):
            EMACalculator(period=-5)
        with self.assertRaises(ValueError):
            EMACalculator(period="10")
        with self.assertRaises(ValueError):
            EMACalculator(period=True)

    def test_ema_insufficient_candles(self):
        ema = EMACalculator(period=5)
        candles = [make_test_candle(i, 100, 110, 90, 105) for i in range(4)]
        with self.assertRaises(ValueError) as ctx:
            ema.calculate(candles)
        self.assertIn("Insufficient candles", str(ctx.exception))

    def test_ema_constant_price(self):
        ema = EMACalculator(period=5)
        candles = [make_test_candle(i, 100, 100, 100, 100) for i in range(10)]
        res = ema.calculate(candles)
        self.assertEqual(res, {"ema_5": 100.0})

    def test_ema_hand_verified_numerical_correctness(self):
        """
        Period = 3, Multiplier = 2 / (3 + 1) = 0.5
        Closes: [10, 20, 30, 40, 50]
        - Seed EMA (first 3): (10 + 20 + 30) / 3 = 20.0
        - Candle 4 (close=40): (40 * 0.5) + (20.0 * 0.5) = 30.0
        - Candle 5 (close=50): (50 * 0.5) + (30.0 * 0.5) = 40.0
        Expected: ema_3 = 40.0
        """
        ema = EMACalculator(period=3)
        closes = [10.0, 20.0, 30.0, 40.0, 50.0]
        candles = [make_test_candle(i, c, c + 1, c - 1, c) for i, c in enumerate(closes)]
        res = ema.calculate(candles)
        self.assertAlmostEqual(res["ema_3"], 40.0, places=6)

    def test_ema_increasing_series(self):
        ema = EMACalculator(period=4)
        closes = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
        candles = [make_test_candle(i, c, c + 1, c - 1, c) for i, c in enumerate(closes)]
        res = ema.calculate(candles)
        # Seed (10+12+14+16)/4 = 13.0
        # Mult = 2/5 = 0.4
        # At 18: 18*0.4 + 13*0.6 = 7.2 + 7.8 = 15.0
        # At 20: 20*0.4 + 15*0.6 = 8.0 + 9.0 = 17.0
        self.assertAlmostEqual(res["ema_4"], 17.0, places=6)

    # =========================================================================
    # RSI CALCULATOR TESTS
    # =========================================================================

    def test_rsi_initialization_and_validation(self):
        rsi = RSICalculator(period=14)
        self.assertEqual(rsi.period, 14)
        self.assertEqual(rsi.name, "rsi_14")
        self.assertEqual(rsi.minimum_candles, 15)  # 14 changes requires 15 candles

        with self.assertRaises(ValueError):
            RSICalculator(period=0)
        with self.assertRaises(ValueError):
            RSICalculator(period=-1)
        with self.assertRaises(ValueError):
            RSICalculator(period=False)

    def test_rsi_insufficient_candles(self):
        rsi = RSICalculator(period=5)
        # 5 candles only provides 4 changes, minimum required is 6
        candles = [make_test_candle(i, 100, 105, 95, 100) for i in range(5)]
        with self.assertRaises(ValueError) as ctx:
            rsi.calculate(candles)
        self.assertIn("Insufficient candles", str(ctx.exception))

    def test_rsi_constant_price_is_50(self):
        rsi = RSICalculator(period=5)
        candles = [make_test_candle(i, 100, 100, 100, 100) for i in range(10)]
        res = rsi.calculate(candles)
        self.assertEqual(res, {"rsi_5": 50.0})

    def test_rsi_strictly_increasing_is_100(self):
        rsi = RSICalculator(period=4)
        closes = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
        candles = [make_test_candle(i, c, c + 1, c - 1, c) for i, c in enumerate(closes)]
        res = rsi.calculate(candles)
        self.assertEqual(res, {"rsi_4": 100.0})

    def test_rsi_strictly_decreasing_is_0(self):
        rsi = RSICalculator(period=4)
        closes = [20.0, 18.0, 16.0, 14.0, 12.0, 10.0]
        candles = [make_test_candle(i, c, c + 1, c - 1, c) for i, c in enumerate(closes)]
        res = rsi.calculate(candles)
        self.assertEqual(res, {"rsi_4": 0.0})

    def test_rsi_hand_verified_numerical_correctness(self):
        """
        Period = 3 (requires at least 4 candles).
        Closes: [10, 12, 10, 14, 12]
        Changes:
          1) 12 - 10 = +2
          2) 10 - 12 = -2
          3) 14 - 10 = +4
          4) 12 - 14 = -2
        Seed (first 3 changes):
          Gains: [2, 0, 4] -> AvgGain = (2 + 0 + 4) / 3 = 2.0
          Losses: [0, 2, 0] -> AvgLoss = (0 + 2 + 0) / 3 = 2/3 = 0.6666667
        Next change (candle 4, gain=0, loss=2):
          AvgGain = (2.0 * 2 + 0) / 3 = 4.0 / 3 = 1.3333333
          AvgLoss = ((2/3) * 2 + 2) / 3 = (4/3 + 6/3) / 3 = 10/9 = 1.1111111
        RS = AvgGain / AvgLoss = (4/3) / (10/9) = (4/3) * (9/10) = 36/30 = 1.2
        RSI = 100 - (100 / (1 + 1.2)) = 100 - (100 / 2.2) = 100 - 45.454545 = 54.545455
        """
        rsi = RSICalculator(period=3)
        closes = [10.0, 12.0, 10.0, 14.0, 12.0]
        candles = [make_test_candle(i, c, c + 1, c - 1, c) for i, c in enumerate(closes)]
        res = rsi.calculate(candles)
        expected_rsi = 100.0 - (100.0 / (1.0 + 1.2))
        self.assertAlmostEqual(res["rsi_3"], expected_rsi, places=4)

    # =========================================================================
    # ATR CALCULATOR TESTS
    # =========================================================================

    def test_atr_initialization_and_validation(self):
        atr = ATRCalculator(period=14)
        self.assertEqual(atr.period, 14)
        self.assertEqual(atr.name, "atr_14")
        self.assertEqual(atr.minimum_candles, 14)

        with self.assertRaises(ValueError):
            ATRCalculator(period=0)
        with self.assertRaises(ValueError):
            ATRCalculator(period=-3)
        with self.assertRaises(ValueError):
            ATRCalculator(period="14")

    def test_atr_insufficient_candles(self):
        atr = ATRCalculator(period=5)
        candles = [make_test_candle(i, 100, 110, 90, 100) for i in range(4)]
        with self.assertRaises(ValueError) as ctx:
            atr.calculate(candles)
        self.assertIn("Insufficient candles", str(ctx.exception))

    def test_atr_flat_candles(self):
        atr = ATRCalculator(period=5)
        # High=105, Low=95, Close=100 -> TR = 10 on every candle
        candles = [make_test_candle(i, 100, 105, 95, 100) for i in range(8)]
        res = atr.calculate(candles)
        self.assertEqual(res, {"atr_5": 10.0})

    def test_atr_hand_verified_numerical_correctness(self):
        """
        Period = 3
        Candles:
        0: H=110, L=90, C=100 -> TR_0 = 110 - 90 = 20
        1: H=120, L=105, C=115 -> TR_1 = max(120-105=15, |120-100|=20, |105-100|=5) = 20
        2: H=125, L=110, C=120 -> TR_2 = max(125-110=15, |125-115|=10, |110-115|=5) = 15
        Initial ATR = (20 + 20 + 15) / 3 = 55 / 3 = 18.333333

        Candle 3: H=135, L=115, C=130 (prev C=120)
        TR_3 = max(135-115=20, |135-120|=15, |115-120|=5) = 20
        Wilder smoothed ATR = ((55/3 * 2) + 20) / 3 = (110/3 + 60/3) / 3 = 170 / 9 = 18.888889
        """
        atr = ATRCalculator(period=3)
        candles = [
            make_test_candle(0, 95, 110, 90, 100),
            make_test_candle(1, 105, 120, 105, 115),
            make_test_candle(2, 115, 125, 110, 120),
            make_test_candle(3, 120, 135, 115, 130),
        ]
        res = atr.calculate(candles)
        expected_atr = 170.0 / 9.0
        self.assertAlmostEqual(res["atr_3"], expected_atr, places=4)

    # =========================================================================
    # FEATURE ENGINE INTEGRATION TESTS
    # =========================================================================

    def test_feature_engine_coexistence_and_immutability(self):
        engine = FeatureEngine(
            calculators=[
                BasicPriceCalculator(),
                EMACalculator(period=3),
                RSICalculator(period=3),
                ATRCalculator(period=3),
            ]
        )

        candles_1h = [
            make_test_candle(0, 95, 110, 90, 100, granularity="1h"),
            make_test_candle(1, 105, 120, 105, 115, granularity="1h"),
            make_test_candle(2, 115, 125, 110, 120, granularity="1h"),
            make_test_candle(3, 120, 135, 115, 130, granularity="1h"),
            make_test_candle(4, 125, 140, 120, 135, granularity="1h"),
        ]

        candles_4h = [
            make_test_candle(0, 90, 120, 85, 110, granularity="4h"),
            make_test_candle(1, 110, 130, 100, 120, granularity="4h"),
            make_test_candle(2, 120, 140, 110, 130, granularity="4h"),
            make_test_candle(3, 130, 150, 125, 145, granularity="4h"),
        ]

        features_by_granularity = engine.calculate(
            {"1h": candles_1h, "4h": candles_4h}
        )

        self.assertIn("1h", features_by_granularity)
        self.assertIn("4h", features_by_granularity)

        f_1h = features_by_granularity["1h"]
        f_4h = features_by_granularity["4h"]

        # Check all expected feature keys are present
        for key in ["last_close", "ema_3", "rsi_3", "atr_3"]:
            self.assertIn(key, f_1h.values)
            self.assertIn(key, f_4h.values)

        # Check immutability
        with self.assertRaises(TypeError):
            f_1h.values["ema_3"] = 999.0

        # Check independence between timeframes
        self.assertEqual(f_1h.values["last_close"], 135.0)
        self.assertEqual(f_4h.values["last_close"], 145.0)


if __name__ == "__main__":
    unittest.main()
