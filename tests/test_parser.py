import unittest

from exchange.parser import BitgetParser


class TestBitgetParser(unittest.TestCase):

    def test_parse_ticker_with_complete_data(self):

        response = {
            "code": "00000",
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "lastPr": "64000.0",
                    "bidPr": "63999.0",
                    "askPr": "64001.0",
                    "baseVolume": "100.5",
                    "quoteVolume": "6432000.0",
                    "change24h": "0.05",
                    "fundingRate": "0.0001",
                    "markPrice": "64000.5",
                    "holdingAmount": "25000.0",
                }
            ],
        }

        ticker = BitgetParser.parse_ticker(response)

        self.assertEqual(ticker.symbol, "BTCUSDT")
        self.assertEqual(ticker.price, 64000.0)
        self.assertEqual(ticker.volume_24h, 100.5)
        self.assertEqual(ticker.change_24h, 0.05)
        self.assertEqual(ticker.funding_rate, 0.0001)
        self.assertEqual(ticker.spread, 2.0)
        self.assertAlmostEqual(
            ticker.spread_pct,
            (2.0 / 64000.0) * 100
        )

    def test_missing_optional_fields_default_to_zero(self):

        response = {
            "code": "00000",
            "data": [
                {
                    "symbol": "ETHUSDT",
                    "lastPr": "1800",
                    "bidPr": "1799",
                    "askPr": "1801",
                }
            ],
        }

        ticker = BitgetParser.parse_ticker(response)

        self.assertEqual(ticker.volume_24h, 0.0)
        self.assertEqual(ticker.quote_volume_24h, 0.0)
        self.assertEqual(ticker.funding_rate, 0.0)
        self.assertEqual(ticker.open_interest, 0.0)

    def test_empty_single_ticker_response_raises_error(self):

        response = {
            "code": "00000",
            "data": [],
        }

        with self.assertRaises(ValueError):
            BitgetParser.parse_ticker(response)

    def test_bulk_parser_skips_malformed_records(self):

        response = {
            "code": "00000",
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "lastPr": "64000",
                    "bidPr": "63999",
                    "askPr": "64001",
                },
                {
                    "symbol": "BROKENUSDT",
                    "lastPr": "invalid",
                    "bidPr": "1",
                    "askPr": "2",
                },
            ],
        }

        tickers = BitgetParser.parse_tickers(response)

        self.assertEqual(len(tickers), 1)
        self.assertEqual(tickers[0].symbol, "BTCUSDT")


if __name__ == "__main__":
    unittest.main()