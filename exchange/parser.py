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