from data.ticker import Ticker


class BitgetParser:

    @staticmethod
    def parse_ticker(response):

        item = response["data"][0]

        return Ticker(
            symbol=item["symbol"],
            price=float(item["lastPr"]),
            bid=float(item["bidPr"]),
            ask=float(item["askPr"]),
        )

    @staticmethod
    def parse_tickers(response):

        tickers = []

        if response.get("code") != "00000":
            return tickers

        for item in response.get("data", []):

            try:
                ticker = Ticker(
                    symbol=item["symbol"],
                    price=float(item["lastPr"]),
                    bid=float(item["bidPr"]),
                    ask=float(item["askPr"]),
                )

                tickers.append(ticker)

            except (KeyError, TypeError, ValueError):
                continue

        return tickers