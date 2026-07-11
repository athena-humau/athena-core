from market.market_cache import MarketCache


class MarketScanner:

    def __init__(self):

        self.cache = MarketCache()

    def update(self, ticker):

        self.cache.update_ticker(ticker)

    def get_ticker_count(self):

        return len(self.cache.get_all_tickers())

    def show_market(self):

        print()
        print("=" * 50)
        print("MARKET CACHE")
        print("=" * 50)

        for symbol, ticker in self.cache.get_all_tickers().items():

            print(f"{symbol:<12} {ticker.price}")