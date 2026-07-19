from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from data.candle import Candle
from market.multi_timeframe_manager import MultiTimeframeManager


def candle_record(timestamp):
    return [str(timestamp), "100", "110", "90", "105", "10", "1000"]


def make_candle(granularity):
    return Candle(
        symbol="BTCUSDT",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=10.0,
        quote_volume=1000.0,
        granularity=granularity,
    )


class TestMultiTimeframeManager(unittest.TestCase):

    def setUp(self):

        self.market_data = Mock()
        self.parser = Mock()
        self.manager = MultiTimeframeManager(
            market_data=self.market_data,
            parser=self.parser,
        )

    def test_default_request_fetches_all_supported_granularities(self):

        self.market_data.get_historical_candle_range.return_value = []
        self.parser.parse_candle_records.return_value = []

        result = self.manager.get_historical_candles(
            "BTCUSDT",
            0,
            14400000,
        )

        self.assertEqual(
            tuple(result),
            MultiTimeframeManager.DEFAULT_GRANULARITIES,
        )
        self.assertEqual(
            self.market_data.get_historical_candle_range.call_count,
            5,
        )
        self.assertEqual(
            self.parser.parse_candle_records.call_count,
            5,
        )

    def test_subset_is_fetched_and_grouped_by_granularity(self):

        one_minute = [make_candle("1m")]
        one_hour = [make_candle("1H")]
        self.market_data.get_historical_candle_range.side_effect = [
            [candle_record(0)],
            [candle_record(0)],
        ]
        self.parser.parse_candle_records.side_effect = [
            one_minute,
            one_hour,
        ]

        result = self.manager.get_historical_candles(
            "BTCUSDT",
            0,
            3600000,
            granularities=("1m", "1H"),
            limit=50,
        )

        self.assertEqual(result, {"1m": one_minute, "1H": one_hour})
        self.market_data.get_historical_candle_range.assert_any_call(
            "BTCUSDT", "1m", 0, 3600000, 50
        )
        self.market_data.get_historical_candle_range.assert_any_call(
            "BTCUSDT", "1H", 0, 3600000, 50
        )
        self.parser.parse_candle_records.assert_any_call(
            [candle_record(0)], "BTCUSDT", "1m"
        )
        self.parser.parse_candle_records.assert_any_call(
            [candle_record(0)], "BTCUSDT", "1H"
        )

    def test_empty_history_returns_empty_list_for_timeframe(self):

        self.market_data.get_historical_candle_range.return_value = []
        self.parser.parse_candle_records.return_value = []

        result = self.manager.get_historical_candles(
            "BTCUSDT",
            0,
            60000,
            granularities=("1m",),
        )

        self.assertEqual(result, {"1m": []})

    def test_manager_returns_parsed_candle_objects(self):

        self.market_data.get_historical_candle_range.return_value = [
            candle_record(0),
            ["invalid"],
        ]
        manager = MultiTimeframeManager(market_data=self.market_data)

        result = manager.get_historical_candles(
            "BTCUSDT",
            0,
            60000,
            granularities=("1m",),
        )

        self.assertEqual(len(result["1m"]), 1)
        self.assertIsInstance(result["1m"][0], Candle)
        self.assertEqual(result["1m"][0].granularity, "1m")

    def test_duplicate_or_unsupported_granularity_fails_before_fetching(self):

        for granularities in (("1m", "1m"), ("3m",)):
            with self.subTest(granularities=granularities):
                with self.assertRaises(ValueError):
                    self.manager.get_historical_candles(
                        "BTCUSDT",
                        0,
                        60000,
                        granularities=granularities,
                    )

        self.market_data.get_historical_candle_range.assert_not_called()

    def test_unaligned_range_fails_before_fetching(self):

        with self.assertRaises(ValueError):
            self.manager.get_historical_candles(
                "BTCUSDT",
                0,
                60000,
                granularities=("5m",),
            )

        self.market_data.get_historical_candle_range.assert_not_called()

    def test_market_data_error_propagates_without_partial_result(self):

        self.market_data.get_historical_candle_range.side_effect = RuntimeError(
            "Bitget HTTP request failed"
        )

        with self.assertRaisesRegex(RuntimeError, "Bitget HTTP request failed"):
            self.manager.get_historical_candles(
                "BTCUSDT",
                0,
                60000,
                granularities=("1m",),
            )

        self.parser.parse_candle_records.assert_not_called()

    def test_invalid_request_arguments_fail_before_fetching(self):

        invalid_arguments = [
            {"symbol": "", "start_time": 0, "end_time": 60000},
            {"symbol": "BTCUSDT", "start_time": -1, "end_time": 60000},
            {"symbol": "BTCUSDT", "start_time": 60000, "end_time": 0},
            {"symbol": "BTCUSDT", "start_time": 0, "end_time": 60000,
             "limit": 0},
            {"symbol": "BTCUSDT", "start_time": 0, "end_time": 60000,
             "granularities": ()},
        ]

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.manager.get_historical_candles(**arguments)

        self.market_data.get_historical_candle_range.assert_not_called()


if __name__ == "__main__":
    unittest.main()
