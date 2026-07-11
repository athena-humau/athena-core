from exchange.manager import ExchangeManager
from exchange.market_data import MarketData
from exchange.parser import BitgetParser
from scanner.market_scanner import MarketScanner
from scanner.ranking_engine import RankingEngine

from core.logger import log


class Athena:

    def __init__(self):

        self.exchange = ExchangeManager().get_exchange()
        self.market = MarketData()
        self.scanner = MarketScanner()
        self.ranking_engine = RankingEngine()

    def run(self):

        self.exchange.connect()

        log("ATHENA Core Initialized Successfully")

        # Fetch complete market snapshot
        response = self.market.get_all_tickers()

        # Parse valid ticker records
        tickers = BitgetParser.parse_tickers(response)

        # Update market cache
        for ticker in tickers:
            self.scanner.update(ticker)

        # Rank valid markets
        ranked_markets = self.ranking_engine.rank(tickers)

        # Show system summary
        print()
        print("=" * 70)
        print("BULK MARKET SNAPSHOT")
        print("=" * 70)
        print(f"Parsed Tickers : {len(tickers)}")
        print(f"Cached Tickers : {self.scanner.get_ticker_count()}")
        print(f"Ranked Markets : {len(ranked_markets)}")
        print("=" * 70)

        # Show Top 10 market shortlist
        print()
        print("=" * 70)
        print("TOP 10 MARKET OPPORTUNITY SHORTLIST")
        print("=" * 70)

        print(
            f"{'RANK':<6}"
            f"{'SYMBOL':<18}"
            f"{'SCORE':<10}"
            f"{'LIQ':<10}"
            f"{'MOVE':<10}"
            f"{'SPREAD':<10}"
        )

        print("-" * 70)

        for rank, market in enumerate(
            ranked_markets[:10],
            start=1,
        ):
            print(
                f"{rank:<6}"
                f"{market.symbol:<18}"
                f"{market.score:<10.2f}"
                f"{market.liquidity_score:<10.2f}"
                f"{market.movement_score:<10.2f}"
                f"{market.spread_score:<10.2f}"
            )

        print("=" * 70)