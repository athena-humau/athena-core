import unittest

from data.ticker import Ticker
from scanner.eligibility_filter import MarketEligibilityFilter


def make_ticker(
    symbol,
    quote_volume,
    bid,
    ask,
    open_interest=1000.0,
    change=0.01,
    funding=0.0001,
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


class TestMarketEligibilityFilter(unittest.TestCase):

    def test_empty_input_returns_empty_list(self):
        market_filter = MarketEligibilityFilter()

        self.assertEqual(market_filter.filter([]), [])

    def test_low_volume_market_is_filtered(self):
        market_filter = MarketEligibilityFilter()

        tickers = [
            make_ticker("LOWUSDT", 100, 99.9, 100.1),
            make_ticker("MIDUSDT", 1000, 99.9, 100.1),
            make_ticker("HIGHUSDT", 10000, 99.9, 100.1),
        ]

        eligible = market_filter.filter(tickers)

        symbols = [ticker.symbol for ticker in eligible]

        self.assertNotIn("LOWUSDT", symbols)
        self.assertIn("MIDUSDT", symbols)
        self.assertIn("HIGHUSDT", symbols)

    def test_wide_spread_market_is_filtered(self):
        market_filter = MarketEligibilityFilter()

        tickers = [
            make_ticker("AUSDT", 1000, 99.95, 100.05),
            make_ticker("BUSDT", 2000, 99.9, 100.1),
            make_ticker("CUSDT", 3000, 90, 110),
            make_ticker("DUSDT", 4000, 99.8, 100.2),
        ]

        eligible = market_filter.filter(tickers)

        symbols = [ticker.symbol for ticker in eligible]

        self.assertNotIn("CUSDT", symbols)

    def test_zero_open_interest_is_filtered(self):
        market_filter = MarketEligibilityFilter()

        ticker = make_ticker(
            "ZEROIUSDT",
            1000,
            99.9,
            100.1,
            open_interest=0.0,
        )

        self.assertEqual(market_filter.filter([ticker]), [])

    def test_crossed_market_is_filtered(self):
        market_filter = MarketEligibilityFilter()

        ticker = make_ticker(
            "CROSSEDUSDT",
            1000,
            101,
            100,
        )

        self.assertEqual(market_filter.filter([ticker]), [])

    def test_non_finite_market_is_filtered(self):
        market_filter = MarketEligibilityFilter()

        ticker = make_ticker(
            "NANUSDT",
            float("nan"),
            99,
            101,
        )

        self.assertEqual(market_filter.filter([ticker]), [])


if __name__ == "__main__":
    unittest.main()