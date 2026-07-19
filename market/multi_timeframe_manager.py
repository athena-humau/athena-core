from exchange.market_data import MarketData
from exchange.parser import BitgetParser


class MultiTimeframeManager:

    DEFAULT_GRANULARITIES = (
        "1m",
        "5m",
        "15m",
        "1H",
        "4H",
    )

    def __init__(self, market_data=None, parser=BitgetParser):

        if market_data is None:
            market_data = MarketData()

        self.market_data = market_data
        self.parser = parser

    def get_historical_candles(
        self,
        symbol: str,
        start_time: int,
        end_time: int,
        granularities=None,
        limit=100,
    ):

        requested_granularities = self._validate_request(
            symbol,
            start_time,
            end_time,
            granularities,
            limit,
        )

        candles_by_granularity = {}

        for granularity in requested_granularities:

            records = self.market_data.get_historical_candle_range(
                symbol,
                granularity,
                start_time,
                end_time,
                limit,
            )

            candles_by_granularity[granularity] = (
                self.parser.parse_candle_records(
                    records,
                    symbol,
                    granularity,
                )
            )

        return candles_by_granularity

    @staticmethod
    def _validate_request(
        symbol,
        start_time,
        end_time,
        granularities,
        limit,
    ):

        if not isinstance(symbol, str) or not symbol:
            raise ValueError("Candle symbol must be a non-empty string")

        if granularities is None:
            requested_granularities = (
                MultiTimeframeManager.DEFAULT_GRANULARITIES
            )
        else:
            if not isinstance(granularities, (list, tuple)):
                raise ValueError("Granularities must be a list or tuple")

            requested_granularities = tuple(granularities)

        if not requested_granularities:
            raise ValueError("At least one granularity is required")

        if len(set(requested_granularities)) != len(requested_granularities):
            raise ValueError("Duplicate candle granularities are not allowed")

        for granularity in requested_granularities:
            if granularity not in MarketData.SUPPORTED_CANDLE_GRANULARITIES:
                raise ValueError("Unsupported candle granularity")

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MarketData.MAX_HISTORICAL_CANDLE_LIMIT
        ):
            raise ValueError("Candle limit must be between 1 and 200")

        MultiTimeframeManager._validate_timestamp("start_time", start_time)
        MultiTimeframeManager._validate_timestamp("end_time", end_time)

        if start_time > end_time:
            raise ValueError("Candle start_time cannot exceed end_time")

        if (
            end_time - start_time
            > MarketData.MAX_HISTORICAL_CANDLE_RANGE_MS
        ):
            raise ValueError("Historical candle range cannot exceed 90 days")

        for granularity in requested_granularities:
            interval_ms = MarketData.CANDLE_GRANULARITY_MS[granularity]

            if start_time % interval_ms != 0 or end_time % interval_ms != 0:
                raise ValueError(
                    "Candle timestamps must align with every granularity"
                )

        return requested_granularities

    @staticmethod
    def _validate_timestamp(name, timestamp):

        if (
            not isinstance(timestamp, int)
            or isinstance(timestamp, bool)
            or timestamp < 0
        ):
            raise ValueError(
                f"Candle {name} must be a non-negative millisecond timestamp"
            )
