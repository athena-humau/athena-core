import math
from datetime import datetime, timezone

from data.candle import Candle
from data.ticker import Ticker


class BitgetParser:

    @staticmethod
    def _parse_item(item):

        return Ticker(
            symbol=item["symbol"],
            price=float(item["lastPr"]),
            bid=float(item["bidPr"]),
            ask=float(item["askPr"]),
            volume_24h=float(item.get("baseVolume") or 0),
            quote_volume_24h=float(item.get("quoteVolume") or 0),
            change_24h=float(item.get("change24h") or 0),
            funding_rate=float(item.get("fundingRate") or 0),
            mark_price=float(item.get("markPrice") or 0),
            open_interest=float(item.get("holdingAmount") or 0),
        )

    @staticmethod
    def parse_ticker(response):

        if response.get("code") != "00000":
            raise ValueError("Invalid Bitget API response")

        data = response.get("data", [])

        if not data:
            raise ValueError("Bitget ticker response contains no data")

        return BitgetParser._parse_item(data[0])

    @staticmethod
    def parse_tickers(response):

        tickers = []

        if response.get("code") != "00000":
            return tickers

        for item in response.get("data", []):

            try:
                ticker = BitgetParser._parse_item(item)
                tickers.append(ticker)

            except (KeyError, TypeError, ValueError):
                continue

        return tickers

    @staticmethod
    def _parse_candle_item(item, symbol, granularity):

        if not isinstance(item, (list, tuple)) or len(item) < 7:
            raise ValueError("Invalid candle record")

        timestamp_ms = int(item[0])

        if timestamp_ms < 0:
            raise ValueError("Invalid candle timestamp")

        open_price = float(item[1])
        high = float(item[2])
        low = float(item[3])
        close = float(item[4])
        volume = float(item[5])
        quote_volume = float(item[6])

        numeric_values = (
            open_price,
            high,
            low,
            close,
            volume,
            quote_volume,
        )

        if not all(math.isfinite(value) for value in numeric_values):
            raise ValueError("Candle values must be finite")

        if min(open_price, high, low, close) <= 0:
            raise ValueError("Candle prices must be positive")

        if volume < 0 or quote_volume < 0:
            raise ValueError("Candle volumes cannot be negative")

        if low > min(open_price, close) or high < max(open_price, close):
            raise ValueError("Invalid candle OHLC relationship")

        return Candle(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(
                timestamp_ms / 1000,
                tz=timezone.utc,
            ),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            quote_volume=quote_volume,
            granularity=granularity,
        )

    @staticmethod
    def parse_candles(response, symbol: str, granularity: str):

        if response.get("code") != "00000":
            return []

        data = response.get("data", [])

        return BitgetParser.parse_candle_records(
            data,
            symbol,
            granularity,
        )

    @staticmethod
    def parse_candle_records(records, symbol: str, granularity: str):

        candles = []

        if not isinstance(records, list):
            return candles

        for item in records:

            try:
                candle = BitgetParser._parse_candle_item(
                    item,
                    symbol,
                    granularity,
                )
                candles.append(candle)

            except (
                KeyError,
                TypeError,
                ValueError,
                OverflowError,
                OSError,
            ):
                continue

        return candles
