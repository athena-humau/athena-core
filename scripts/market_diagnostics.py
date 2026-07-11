import math
import statistics

from exchange.market_data import MarketData
from exchange.parser import BitgetParser


def percentile(values, percentile_value):

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


def print_distribution(name, values):

    finite_values = [
        value
        for value in values
        if math.isfinite(value)
    ]

    zero_count = sum(
        value == 0
        for value in finite_values
    )

    print()
    print("=" * 70)
    print(name)
    print("=" * 70)

    if not finite_values:
        print("No finite values available.")
        return

    print(f"Count       : {len(finite_values)}")
    print(f"Zero Count  : {zero_count}")
    print(f"Minimum     : {min(finite_values)}")
    print(f"Median      : {statistics.median(finite_values)}")
    print(f"P75         : {percentile(finite_values, 0.75)}")
    print(f"P90         : {percentile(finite_values, 0.90)}")
    print(f"P95         : {percentile(finite_values, 0.95)}")
    print(f"Maximum     : {max(finite_values)}")


def main():

    market = MarketData()

    response = market.get_all_tickers()

    tickers = BitgetParser.parse_tickers(response)

    print()
    print("=" * 70)
    print("ATHENA MARKET DIAGNOSTICS")
    print("=" * 70)
    print(f"Parsed Tickers : {len(tickers)}")

    print_distribution(
        "QUOTE VOLUME 24H",
        [
            ticker.quote_volume_24h
            for ticker in tickers
        ],
    )

    print_distribution(
        "SPREAD PERCENT",
        [
            ticker.spread_pct
            for ticker in tickers
        ],
    )

    print_distribution(
        "OPEN INTEREST",
        [
            ticker.open_interest
            for ticker in tickers
        ],
    )

    print_distribution(
        "ABSOLUTE 24H CHANGE",
        [
            abs(ticker.change_24h)
            for ticker in tickers
        ],
    )

    print_distribution(
        "ABSOLUTE FUNDING RATE",
        [
            abs(ticker.funding_rate)
            for ticker in tickers
        ],
    )


if __name__ == "__main__":
    main()