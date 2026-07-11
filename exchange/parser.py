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