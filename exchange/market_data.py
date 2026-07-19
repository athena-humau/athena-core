import requests


class MarketData:

    BASE_URL = "https://api.bitget.com"
    TIMEOUT = 10
    SUPPORTED_CANDLE_GRANULARITIES = {
        "1m",
        "5m",
        "15m",
        "1H",
        "4H",
    }
    CANDLE_GRANULARITY_MS = {
        "1m": 60 * 1000,
        "5m": 5 * 60 * 1000,
        "15m": 15 * 60 * 1000,
        "1H": 60 * 60 * 1000,
        "4H": 4 * 60 * 60 * 1000,
    }
    MAX_HISTORICAL_CANDLE_LIMIT = 200
    MAX_HISTORICAL_CANDLE_RANGE_MS = 90 * 24 * 60 * 60 * 1000
    MAX_HISTORICAL_CANDLE_PAGES = 1000

    def _get(self, endpoint, params):

        url = f"{self.BASE_URL}{endpoint}"

        try:
            response = requests.get(
                url,
                params=params,
                timeout=self.TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            if data.get("code") != "00000":
                raise RuntimeError(
                    f"Bitget API error: "
                    f"code={data.get('code')} "
                    f"msg={data.get('msg')}"
                )

            return data

        except requests.RequestException as error:
            raise RuntimeError(
                f"Bitget HTTP request failed: {error}"
            ) from error

        except ValueError as error:
            raise RuntimeError(
                "Bitget returned invalid JSON"
            ) from error

    def get_ticker(self, symbol: str):

        return self._get(
            "/api/v2/mix/market/ticker",
            {
                "symbol": symbol,
                "productType": "USDT-FUTURES"
            }
        )

    def get_symbols(self):

        return self._get(
            "/api/v2/mix/market/contracts",
            {
                "productType": "USDT-FUTURES"
            }
        )

    def get_all_tickers(self):

        return self._get(
            "/api/v2/mix/market/tickers",
            {
                "productType": "USDT-FUTURES"
            }
        )

    def get_multiple_tickers(self, symbols):

        tickers = []

        for symbol in symbols:

            try:
                tickers.append(self.get_ticker(symbol))

            except RuntimeError as error:
                print(f"Error fetching {symbol}: {error}")

        return tickers

    def get_historical_candles(
        self,
        symbol: str,
        granularity: str,
        start_time=None,
        end_time=None,
        limit=100,
    ):

        self._validate_historical_candle_arguments(
            symbol,
            granularity,
            start_time,
            end_time,
            limit,
        )

        params = {
            "symbol": symbol,
            "productType": "USDT-FUTURES",
            "granularity": granularity,
            "limit": limit,
        }

        if start_time is not None:
            params["startTime"] = start_time

        if end_time is not None:
            params["endTime"] = end_time

        response = self._get(
            "/api/v2/mix/market/history-candles",
            params,
        )

        if not isinstance(response.get("data"), list):
            raise RuntimeError("Bitget candle response contains invalid data")

        return response

    def get_historical_candle_range(
        self,
        symbol: str,
        granularity: str,
        start_time: int,
        end_time: int,
        limit=100,
    ):

        self._validate_historical_candle_arguments(
            symbol,
            granularity,
            start_time,
            end_time,
            limit,
        )

        if end_time - start_time > self.MAX_HISTORICAL_CANDLE_RANGE_MS:
            raise ValueError(
                "Historical candle range cannot exceed 90 days"
            )

        candles_by_timestamp = {}
        page_end_time = end_time

        for _ in range(self.MAX_HISTORICAL_CANDLE_PAGES):

            response = self.get_historical_candles(
                symbol,
                granularity,
                start_time=start_time,
                end_time=page_end_time,
                limit=limit,
            )

            records = response["data"]

            if not records:
                break

            timestamps = []

            for record in records:
                timestamp = self._get_candle_timestamp(record)

                if timestamp is None:
                    continue

                if start_time <= timestamp <= end_time:
                    candles_by_timestamp[timestamp] = record
                    timestamps.append(timestamp)

            if not timestamps:
                break

            oldest_timestamp = min(timestamps)

            if oldest_timestamp <= start_time:
                break

            next_page_end_time = oldest_timestamp

            if next_page_end_time >= page_end_time:
                break

            page_end_time = next_page_end_time

        return [
            candles_by_timestamp[timestamp]
            for timestamp in sorted(candles_by_timestamp)
        ]

    def _validate_historical_candle_arguments(
        self,
        symbol,
        granularity,
        start_time,
        end_time,
        limit,
    ):

        if not isinstance(symbol, str) or not symbol:
            raise ValueError("Candle symbol must be a non-empty string")

        if granularity not in self.SUPPORTED_CANDLE_GRANULARITIES:
            raise ValueError("Unsupported candle granularity")

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= self.MAX_HISTORICAL_CANDLE_LIMIT
        ):
            raise ValueError("Candle limit must be between 1 and 200")

        self._validate_candle_timestamp("start_time", start_time)
        self._validate_candle_timestamp("end_time", end_time)

        interval_ms = self.CANDLE_GRANULARITY_MS[granularity]

        for timestamp in (start_time, end_time):
            if timestamp is not None and timestamp % interval_ms != 0:
                raise ValueError(
                    "Candle timestamps must align with the granularity"
                )

        if (
            start_time is not None
            and end_time is not None
            and start_time > end_time
        ):
            raise ValueError("Candle start_time cannot exceed end_time")

    @staticmethod
    def _validate_candle_timestamp(name, timestamp):

        if timestamp is None:
            return

        if (
            not isinstance(timestamp, int)
            or isinstance(timestamp, bool)
            or timestamp < 0
        ):
            raise ValueError(
                f"Candle {name} must be a non-negative millisecond timestamp"
            )

    @staticmethod
    def _get_candle_timestamp(record):

        if not isinstance(record, (list, tuple)) or not record:
            return None

        try:
            timestamp = int(record[0])
        except (TypeError, ValueError):
            return None

        if timestamp < 0:
            return None

        return timestamp
