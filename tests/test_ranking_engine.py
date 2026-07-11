import unittest

from data.ticker import Ticker
from scanner.ranking_engine import RankingEngine


def make_ticker(
    symbol,
    quote_volume,
    change,
    bid,
    ask,
    open_interest,
    funding,
):
    return Ticker(
        symbol=symbol,
        price=(bid + ask) / 2,
        bid=bid,
        ask=ask,
        volume_24h=0.0,
        quote_volume_24h=quote_volume,
        change_24h=change,
        funding_rate=funding,
        mark_price=(bid + ask) / 2,
        open_interest=open_interest,
    )


class TestRankingEngine(unittest.TestCase):

    def test_empty_market_returns_empty_list(self):
        engine = RankingEngine()

        self.assertEqual(engine.rank([]), [])

    def test_single_market_gets_full_score(self):
        engine = RankingEngine()

        ticker = make_ticker(
            "BTCUSDT",
            1000000,
            0.05,
            99,
            101,
            500000,
            0.0001,
        )

        ranked = engine.rank([ticker])

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].score, 100.0)

    def test_better_market_ranks_first(self):
        engine = RankingEngine()

        weak = make_ticker(
            "WEAKUSDT",
            1000,
            0.01,
            90,
            110,
            1000,
            0.05,
        )

        strong = make_ticker(
            "STRONGUSDT",
            1000000,
            0.10,
            99.9,
            100.1,
            500000,
            0.0001,
        )

        ranked = engine.rank([weak, strong])

        self.assertEqual(ranked[0].symbol, "STRONGUSDT")
        self.assertGreater(ranked[0].score, ranked[1].score)

    def test_ranking_does_not_mutate_input_order(self):
        engine = RankingEngine()

        tickers = [
            make_ticker(
                "AUSDT",
                100,
                0.01,
                99,
                101,
                100,
                0.01,
            ),
            make_ticker(
                "BUSDT",
                200,
                0.02,
                99.5,
                100.5,
                200,
                0.001,
            ),
        ]

        original_order = [
            ticker.symbol
            for ticker in tickers
        ]

        engine.rank(tickers)

        self.assertEqual(
            [
                ticker.symbol
                for ticker in tickers
            ],
            original_order,
        )

    def test_equal_values_receive_equal_percentile_scores(self):
        scores = RankingEngine._percentile_scores(
            [10.0, 10.0, 20.0]
        )

        self.assertEqual(scores[0], scores[1])
        self.assertEqual(scores[0], 25.0)
        self.assertEqual(scores[2], 100.0)

    def test_input_order_does_not_change_percentile_scores(self):
        first = RankingEngine._percentile_scores(
            [10.0, 20.0, 10.0]
        )

        second = RankingEngine._percentile_scores(
            [10.0, 10.0, 20.0]
        )

        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1], second[2])
        self.assertEqual(first[2], second[1])

    def test_zero_price_market_is_filtered(self):
        engine = RankingEngine()

        ticker = make_ticker(
            "ZEROUSDT",
            1000,
            0.01,
            0,
            0,
            1000,
            0.001,
        )

        self.assertEqual(engine.rank([ticker]), [])

    def test_crossed_market_is_filtered(self):
        engine = RankingEngine()

        ticker = make_ticker(
            "CROSSEDUSDT",
            1000,
            0.01,
            101,
            100,
            1000,
            0.001,
        )

        self.assertEqual(engine.rank([ticker]), [])

    def test_non_finite_market_is_filtered(self):
        engine = RankingEngine()

        ticker = make_ticker(
            "NANUSDT",
            float("nan"),
            0.01,
            99,
            101,
            1000,
            0.001,
        )

        self.assertEqual(engine.rank([ticker]), [])


if __name__ == "__main__":
    unittest.main()