from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

import requests

from exchange.market_data import MarketData
from exchange.parser import BitgetParser


def candle_record(timestamp, open_price="100", high="110", low="90",
                  close="105", volume="10", quote_volume="1000"):
    return [
        str(timestamp),
        open_price,
        high,
        low,
        close,
        volume,
        quote_volume,
    ]


class TestBitgetCandleParser(unittest.TestCase):

    def test_parse_candles_preserves_canonical_fields(self):

        response = {
            "code": "00000",
            "data": [candle_record(1704067200000)],
        }

        candles = BitgetParser.parse_candles(response, "BTCUSDT", "1H")

        self.assertEqual(len(candles), 1)
        candle = candles[0]
        self.assertEqual(candle.symbol, "BTCUSDT")
        self.assertEqual(
            candle.timestamp,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(candle.open, 100.0)
        self.assertEqual(candle.high, 110.0)
        self.assertEqual(candle.low, 90.0)
        self.assertEqual(candle.close, 105.0)
        self.assertEqual(candle.volume, 10.0)
        self.assertEqual(candle.quote_volume, 1000.0)
        self.assertEqual(candle.granularity, "1H")

    def test_empty_or_invalid_response_returns_empty_list(self):

        self.assertEqual(
            BitgetParser.parse_candles(
                {"code": "00000", "data": []},
                "BTCUSDT",
                "1m",
            ),
            [],
        )
        self.assertEqual(
            BitgetParser.parse_candles(
                {"code": "40000", "data": [candle_record(1)]},
                "BTCUSDT",
                "1m",
            ),
            [],
        )
        self.assertEqual(
            BitgetParser.parse_candles(
                {"code": "00000", "data": {}},
                "BTCUSDT",
                "1m",
            ),
            [],
        )

    def test_parser_skips_malformed_candle_rows(self):

        malformed_rows = [
            None,
            {"timestamp": "1704067200000"},
            ["1704067200000", "100"],
            candle_record("not-a-timestamp"),
            candle_record(1704067200000, open_price="not-a-number"),
            candle_record(1704067200000, high="nan"),
            candle_record(1704067200000, volume="inf"),
            candle_record(1704067200000, volume="-1"),
            candle_record(1704067200000, quote_volume="-1"),
            candle_record(1704067200000, high="99"),
            candle_record(1704067200000, low="106"),
        ]
        response = {
            "code": "00000",
            "data": [candle_record(1704067200000)] + malformed_rows,
        }

        candles = BitgetParser.parse_candles(response, "BTCUSDT", "1m")

        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].timestamp.tzinfo, timezone.utc)


class TestHistoricalCandleMarketData(unittest.TestCase):

    def test_get_historical_candles_uses_exact_endpoint_and_parameters(self):

        market_data = MarketData()
        response = {"code": "00000", "data": []}

        with patch.object(market_data, "_get", return_value=response) as get:
            result = market_data.get_historical_candles(
                "BTCUSDT",
                "1H",
                start_time=1704067200000,
                end_time=1704070800000,
                limit=200,
            )

        self.assertIs(result, response)
        get.assert_called_once_with(
            "/api/v2/mix/market/history-candles",
            {
                "symbol": "BTCUSDT",
                "productType": "USDT-FUTURES",
                "granularity": "1H",
                "limit": 200,
                "startTime": 1704067200000,
                "endTime": 1704070800000,
            },
        )

    def test_get_historical_candles_omits_optional_parameters(self):

        market_data = MarketData()

        with patch.object(
            market_data,
            "_get",
            return_value={"code": "00000", "data": []},
        ) as get:
            market_data.get_historical_candles("BTCUSDT", "5m")

        self.assertEqual(
            get.call_args.args[1],
            {
                "symbol": "BTCUSDT",
                "productType": "USDT-FUTURES",
                "granularity": "5m",
                "limit": 100,
            },
        )

    def test_historical_candle_argument_validation(self):

        market_data = MarketData()
        invalid_arguments = [
            {"symbol": "", "granularity": "1m"},
            {"symbol": "BTCUSDT", "granularity": "3m"},
            {"symbol": "BTCUSDT", "granularity": "1m", "limit": 0},
            {"symbol": "BTCUSDT", "granularity": "1m", "limit": 201},
            {"symbol": "BTCUSDT", "granularity": "1m", "limit": True},
            {"symbol": "BTCUSDT", "granularity": "1m", "start_time": -1},
            {"symbol": "BTCUSDT", "granularity": "1m", "start_time": 1},
            {
                "symbol": "BTCUSDT",
                "granularity": "1m",
                "start_time": 2,
                "end_time": 1,
            },
        ]

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    market_data.get_historical_candles(**arguments)

        with self.assertRaises(ValueError):
            market_data.get_historical_candle_range(
                "BTCUSDT",
                "1m",
                0,
                market_data.MAX_HISTORICAL_CANDLE_RANGE_MS + 1,
            )

    def test_invalid_candle_data_shape_raises_error(self):

        market_data = MarketData()

        with patch.object(
            market_data,
            "_get",
            return_value={"code": "00000", "data": {}},
        ):
            with self.assertRaises(RuntimeError):
                market_data.get_historical_candles("BTCUSDT", "1m")

    def test_http_errors_are_wrapped_by_existing_market_data_handler(self):

        with patch(
            "exchange.market_data.requests.get",
            side_effect=requests.Timeout("timed out"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Bitget HTTP request failed",
            ):
                MarketData().get_historical_candles("BTCUSDT", "1m")

    def test_bitget_api_errors_are_propagated(self):

        response = Mock()
        response.json.return_value = {
            "code": "40000",
            "msg": "invalid request",
        }

        with patch(
            "exchange.market_data.requests.get",
            return_value=response,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "code=40000 msg=invalid request",
            ):
                MarketData().get_historical_candles("BTCUSDT", "1m")

    def test_range_pagination_is_ascending_deduplicated_and_bounded(self):

        market_data = MarketData()
        first_page = {
            "code": "00000",
            "data": [
                candle_record(180000),
                candle_record(120000),
                candle_record(150000),
            ],
        }
        second_page = {
            "code": "00000",
            "data": [
                candle_record(120000, close="106"),
                candle_record(60000),
                candle_record(30000),
            ],
        }

        with patch.object(
            market_data,
            "get_historical_candles",
            side_effect=[first_page, second_page],
        ) as get:
            candles = market_data.get_historical_candle_range(
                "BTCUSDT",
                "1m",
                60000,
                180000,
                limit=3,
            )

        self.assertEqual(
            [int(candle[0]) for candle in candles],
            [60000, 120000, 150000, 180000],
        )
        self.assertEqual(candles[1][4], "106")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[1].kwargs["end_time"], 120000)

    def test_range_stops_on_empty_page(self):

        market_data = MarketData()

        with patch.object(
            market_data,
            "get_historical_candles",
            return_value={"code": "00000", "data": []},
        ) as get:
            candles = market_data.get_historical_candle_range(
                "BTCUSDT",
                "1m",
                60000,
                180000,
            )

        self.assertEqual(candles, [])
        get.assert_called_once()

    def test_range_stops_when_pagination_cannot_make_progress(self):

        market_data = MarketData()
        response = {
            "code": "00000",
            "data": [candle_record(120000)],
        }

        with patch.object(
            market_data,
            "get_historical_candles",
            return_value=response,
        ) as get:
            candles = market_data.get_historical_candle_range(
                "BTCUSDT",
                "1m",
                60000,
                180000,
            )

        self.assertEqual([int(candle[0]) for candle in candles], [120000])
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
