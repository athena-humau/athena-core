import math
from dataclasses import dataclass

from data.ticker import Ticker


@dataclass(frozen=True)
class RankedMarket:
    symbol: str
    score: float
    liquidity_score: float
    movement_score: float
    spread_score: float
    open_interest_score: float
    funding_score: float


class RankingEngine:

    WEIGHTS = {
        "liquidity": 0.30,
        "movement": 0.20,
        "spread": 0.20,
        "open_interest": 0.20,
        "funding": 0.10,
    }

    def rank(self, tickers: list[Ticker]) -> list[RankedMarket]:

        valid_tickers = [
            ticker
            for ticker in tickers
            if self._is_valid_ticker(ticker)
        ]

        if not valid_tickers:
            return []

        liquidity = self._percentile_scores(
            [ticker.quote_volume_24h for ticker in valid_tickers]
        )

        movement = self._percentile_scores(
            [abs(ticker.change_24h) for ticker in valid_tickers]
        )

        spread_quality = self._percentile_scores(
            [-ticker.spread_pct for ticker in valid_tickers]
        )

        open_interest = self._percentile_scores(
            [ticker.open_interest for ticker in valid_tickers]
        )

        funding_quality = self._percentile_scores(
            [-abs(ticker.funding_rate) for ticker in valid_tickers]
        )

        ranked_markets = []

        for index, ticker in enumerate(valid_tickers):

            score = (
                liquidity[index] * self.WEIGHTS["liquidity"]
                + movement[index] * self.WEIGHTS["movement"]
                + spread_quality[index] * self.WEIGHTS["spread"]
                + open_interest[index] * self.WEIGHTS["open_interest"]
                + funding_quality[index] * self.WEIGHTS["funding"]
            )

            ranked_markets.append(
                RankedMarket(
                    symbol=ticker.symbol,
                    score=round(score, 2),
                    liquidity_score=liquidity[index],
                    movement_score=movement[index],
                    spread_score=spread_quality[index],
                    open_interest_score=open_interest[index],
                    funding_score=funding_quality[index],
                )
            )

        return sorted(
            ranked_markets,
            key=lambda market: (-market.score, market.symbol),
        )

    @staticmethod
    def _is_valid_ticker(ticker: Ticker) -> bool:

        numeric_values = (
            ticker.price,
            ticker.bid,
            ticker.ask,
            ticker.quote_volume_24h,
            ticker.change_24h,
            ticker.funding_rate,
            ticker.open_interest,
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

        if ticker.open_interest < 0:
            return False

        return True

    @staticmethod
    def _percentile_scores(values: list[float]) -> list[float]:

        if not values:
            return []

        if len(values) == 1:
            return [100.0]

        sorted_values = sorted(values)
        positions = {}

        index = 0

        while index < len(sorted_values):

            end = index

            while (
                end + 1 < len(sorted_values)
                and sorted_values[end + 1] == sorted_values[index]
            ):
                end += 1

            average_rank = (index + end) / 2

            score = round(
                (average_rank / (len(sorted_values) - 1)) * 100,
                2,
            )

            positions[sorted_values[index]] = score

            index = end + 1

        return [positions[value] for value in values]