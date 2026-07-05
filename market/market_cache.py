from data.ticker import Ticker


class MarketCache:

    def __init__(self):

        self.tickers = {}

    def update_ticker(self, ticker: Ticker):

        self.tickers[ticker.symbol] = ticker

    def get_ticker(self, symbol: str):

        return self.tickers.get(symbol)

    def get_all_tickers(self):

        return self.tickers