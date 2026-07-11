from exchange.manager import ExchangeManager
from exchange.market_data import MarketData
from exchange.parser import BitgetParser
from scanner.market_scanner import MarketScanner

from core.logger import log


class Athena:

    def __init__(self):

        self.exchange = ExchangeManager().get_exchange()
        self.market = MarketData()
        self.scanner = MarketScanner()

    def run(self):

        self.exchange.connect()

        log("ATHENA Core Initialized Successfully")

        # Fetch complete market snapshot in one request
        response = self.market.get_all_tickers()

        # Parse all valid ticker records
        tickers = BitgetParser.parse_tickers(response)

        # Update market cache
        for ticker in tickers:
            self.scanner.update(ticker)

        # Show summary
        print()
        print("=" * 50)
        print("BULK MARKET SNAPSHOT")
        print("=" * 50)
        print(f"Parsed Tickers : {len(tickers)}")
        print(f"Cached Tickers : {self.scanner.get_ticker_count()}")
        print("=" * 50)