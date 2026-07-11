from exchange.manager import ExchangeManager
from exchange.market_data import MarketData
from exchange.parser import BitgetParser
from scanner.market_scanner import MarketScanner

from core.logger import log
from config.constants import WATCHLIST


class Athena:

    def __init__(self):

        self.exchange = ExchangeManager().get_exchange()
        self.market = MarketData()
        self.scanner = MarketScanner()

    def run(self):

        # Connect to exchange
        self.exchange.connect()

        log("ATHENA Core Initialized Successfully")

        # Fetch market data for all symbols in watchlist
        responses = self.market.get_multiple_tickers(WATCHLIST)

        # Parse and cache all tickers
        for response in responses:

            ticker = BitgetParser.parse_ticker(response)

            self.scanner.update(ticker)

        # Display market cache
        self.scanner.show_market()