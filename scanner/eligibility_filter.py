import math
import statistics

from data.ticker import Ticker


class MarketEligibilityFilter:

    def filter(self, tickers: list[Ticker]) -> list[Ticker]:

        structurally_valid = [
            ticker
            for ticker in tickers
            if self._is_structurally_valid(ticker)
        ]

        if not structurally_valid:
            return []

        minimum_quote_volume = statistics.median(
            ticker.quote_volume_24h
            for ticker in structurally_valid
        )

        maximum_spread_pct = self._percentile(
            [
                ticker.spread_pct
                for ticker in structurally_valid
            ],
            0.75,
        )

        return [
            ticker
            for ticker in structurally_valid
            if (
                ticker.quote_volume_24h >= minimum_quote_volume
                and ticker.spread_pct <= maximum_spread_pct
            )
        ]

    @staticmethod
    def _is_structurally_valid(ticker: Ticker) -> bool:

        numeric_values = (
            ticker.price,
            ticker.bid,
            ticker.ask,
            ticker.quote_volume_24h,
            ticker.open_interest,
            ticker.change_24h,
            ticker.funding_rate,
        )

        if not all(math.isfinite(value) for value in numeric_values):
            return False

        if ticker.price <= 0:
            return False

        if ticker.bid <= 0 or ticker.ask <= 0:
            return False

        if ticker.bid > ticker.ask:
            return False

        if ticker.quote_volume_24h < 0:
            return False

        if ticker.open_interest <= 0:
            return False

        return True

    @staticmethod
    def _percentile(values: list[float], percentile_value: float) -> float:

        if not values:
            return 0.0

        sorted_values = sorted(values)

        if len(sorted_values) == 1:
            return sorted_values[0]

        position = (len(sorted_values) - 1) * percentile_value

        lower_index = math.floor(position)
        upper_index = math.ceil(position)

        if lower_index == upper_index:
            return sorted_values[lower_index]

        lower_value = sorted_values[lower_index]
        upper_value = sorted_values[upper_index]

        fraction = position - lower_index

        return lower_value + (
            upper_value - lower_value
        ) * fraction