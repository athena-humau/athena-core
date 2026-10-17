from exchange.manager import ExchangeManager
from exchange.market_data import MarketData
from exchange.parser import BitgetParser

from scanner.market_scanner import MarketScanner
from scanner.eligibility_filter import MarketEligibilityFilter
from scanner.ranking_engine import RankingEngine

from market.pipeline import MarketAnalysisPipeline
from execution.paper_workflow import PaperTradingWorkflow
from config.settings import SHORTLIST_ANALYSIS_LIMIT

from core.logger import log


class Athena:

    def __init__(self):

        self.exchange = ExchangeManager().get_exchange()
        self.market = MarketData()
        self.scanner = MarketScanner()
        self.eligibility_filter = MarketEligibilityFilter()
        self.ranking_engine = RankingEngine()
        self.pipeline = MarketAnalysisPipeline()
        self.paper_workflow = PaperTradingWorkflow()

    def run(self):

        self.exchange.connect()

        log("ATHENA Core Initialized Successfully")

        # Fetch complete market snapshot
        response = self.market.get_all_tickers()

        # Parse market records
        tickers = BitgetParser.parse_tickers(response)

        # Update complete market cache
        for ticker in tickers:
            self.scanner.update(ticker)

        # Filter eligible markets before ranking
        eligible_tickers = self.eligibility_filter.filter(tickers)

        # Rank only eligible markets
        ranked_markets = self.ranking_engine.rank(eligible_tickers)

        # Show system summary
        print()
        print("=" * 70)
        print("ATHENA MARKET PIPELINE")
        print("=" * 70)
        print(f"Parsed Tickers   : {len(tickers)}")
        print(f"Cached Tickers   : {self.scanner.get_ticker_count()}")
        print(f"Eligible Markets : {len(eligible_tickers)}")
        print(f"Ranked Markets   : {len(ranked_markets)}")
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

        # Analyze top ranked markets across multi-timeframes
        log(f"Analyzing Top {SHORTLIST_ANALYSIS_LIMIT} Markets (Candles -> Features -> Regimes)...")
        analyses = self.pipeline.analyze_shortlist(
            ranked_markets,
            limit=SHORTLIST_ANALYSIS_LIMIT,
        )

        print()
        print("=" * 70)
        print("TOP OPPORTUNITY MULTI-TIMEFRAME REGIME ANALYSIS")
        print("=" * 70)
        print(
            f"{'SYMBOL':<14}"
            f"{'STATUS':<10}"
            f"{'1M':<10}"
            f"{'5M':<10}"
            f"{'15M':<10}"
            f"{'1H':<10}"
            f"{'4H':<10}"
        )
        print("-" * 70)

        for analysis in analyses:
            if not analysis.success:
                print(f"{analysis.symbol:<14}{'FAILED':<10}{analysis.error or 'Unknown error'}")
                continue

            r1m = analysis.regimes_by_granularity.get("1m")
            r5m = analysis.regimes_by_granularity.get("5m")
            r15m = analysis.regimes_by_granularity.get("15m")
            r1h = analysis.regimes_by_granularity.get("1H")
            r4h = analysis.regimes_by_granularity.get("4H")

            s1m = r1m.regime.value if r1m else "n/a"
            s5m = r5m.regime.value if r5m else "n/a"
            s15m = r15m.regime.value if r15m else "n/a"
            s1h = r1h.regime.value if r1h else "n/a"
            s4h = r4h.regime.value if r4h else "n/a"

            print(
                f"{analysis.symbol:<14}"
                f"{'OK':<10}"
                f"{s1m:<10}"
                f"{s5m:<10}"
                f"{s15m:<10}"
                f"{s1h:<10}"
                f"{s4h:<10}"
            )

        print("=" * 70)

        # Sprint 31: End-to-End Paper Trading Pipeline
        log("Executing Simulated Paper Trading Workflow on Analyzed Shortlist...")
        paper_results = self.paper_workflow.process_shortlist(analyses)

        print()
        print("=" * 70)
        print("ATHENA END-TO-END PAPER TRADING PIPELINE SUMMARY")
        print("=" * 70)
        print(
            f"{'SYMBOL':<14}"
            f"{'STATUS':<20}"
            f"{'STRATEGY':<24}"
            f"{'DIR':<8}"
            f"{'REASON / DETAILS'}"
        )
        print("-" * 70)

        for res in paper_results:
            strat_id = res.selection.selected_strategy_id if res.selection else "n/a"
            dir_str = res.signal.direction.value if res.signal else "n/a"
            reason_str = res.explanation or "; ".join(res.reason_codes) or "OK"
            print(
                f"{res.symbol:<14}"
                f"{res.status.value:<20}"
                f"{strat_id:<24}"
                f"{dir_str:<8}"
                f"{reason_str}"
            )

        print("=" * 70)
        snapshot = self.paper_workflow.get_portfolio_snapshot()
        print(
            f"Paper Portfolio: Equity={snapshot.total_equity:.2f} USDT | "
            f"Cash={snapshot.cash:.2f} USDT | "
            f"Reserved Margin={snapshot.reserved_margin:.2f} USDT | "
            f"Open Positions={len(snapshot.open_positions)}"
        )
        print("=" * 70)
