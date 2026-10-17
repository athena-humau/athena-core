from datetime import datetime, timezone
import threading
from typing import Any, Sequence

from config.settings import (
    MAX_RETRY_ATTEMPTS,
    PAPER_DB_PATH,
    POSITION_MONITOR_INTERVAL,
    RETRY_BACKOFF_SECONDS,
    RUNTIME_MODE,
    SCAN_INTERVAL,
    SHORTLIST_ANALYSIS_LIMIT,
)
from core.logger import log
from core.runtime import (
    ContinuousRuntime,
    PositionMonitor,
    RuntimeConfig,
    RuntimeMetrics,
    RuntimeMode,
)
from execution.paper_models import PaperPortfolio, PaperPosition, PaperTrade
from execution.paper_workflow import CandidateWorkflowResult, PaperTradingWorkflow
from exchange.manager import ExchangeManager
from exchange.market_data import MarketData
from exchange.parser import BitgetParser
from market.pipeline import MarketAnalysisPipeline
from scanner.eligibility_filter import MarketEligibilityFilter
from scanner.market_scanner import MarketScanner
from scanner.ranking_engine import RankingEngine


class Athena:
    """
    Core ATHENA Application Orchestrator.

    Integrates:
      1. Public Market Scan & Opportunity Filtering
      2. Multi-Timeframe Feature & Regime Analysis
      3. Strategy Selection, Signal Validation, Judge & Risk Engines
      4. Persistent Paper Trading Execution (SQLite with WAL mode)
      5. Active Position Monitoring (Automated Stop-Loss & Take-Profit)
      6. Coordinated Continuous Runtime & Graceful Shutdown
    """

    def __init__(
        self,
        db_path: str | None = None,
        runtime_mode: str | None = None,
        scan_interval: float | None = None,
        position_monitor_interval: float | None = None,
        shortlist_limit: int | None = None,
        exchange: Any | None = None,
        market: MarketData | None = None,
        scanner: MarketScanner | None = None,
        eligibility_filter: MarketEligibilityFilter | None = None,
        ranking_engine: RankingEngine | None = None,
        pipeline: MarketAnalysisPipeline | None = None,
        paper_workflow: PaperTradingWorkflow | None = None,
        position_monitor: PositionMonitor | None = None,
    ):
        # 1. Dependency Injection / Defaults
        self.exchange = exchange or ExchangeManager().get_exchange()
        self.market = market or MarketData()
        self.scanner = scanner or MarketScanner()
        self.eligibility_filter = eligibility_filter or MarketEligibilityFilter()
        self.ranking_engine = ranking_engine or RankingEngine()
        self.pipeline = pipeline or MarketAnalysisPipeline()

        self.db_path = db_path if db_path is not None else PAPER_DB_PATH
        self.paper_workflow = paper_workflow or PaperTradingWorkflow(db_path=self.db_path)

        raw_mode = runtime_mode if runtime_mode is not None else RUNTIME_MODE
        self.runtime_mode = RuntimeMode(str(raw_mode).upper())

        self.scan_interval = float(scan_interval if scan_interval is not None else SCAN_INTERVAL)
        self.position_monitor_interval = float(
            position_monitor_interval if position_monitor_interval is not None else POSITION_MONITOR_INTERVAL
        )
        self.shortlist_limit = int(shortlist_limit if shortlist_limit is not None else SHORTLIST_ANALYSIS_LIMIT)

        # 2. Thread-Safety Lock for shared Paper Ledger
        self._lock = threading.RLock()

        # 3. Position Monitor
        self.position_monitor = position_monitor or PositionMonitor(
            market=self.market,
            workflow=self.paper_workflow,
            lock=self._lock,
            max_retries=MAX_RETRY_ATTEMPTS,
            retry_backoff=RETRY_BACKOFF_SECONDS,
        )

        # 4. Continuous Runtime Controller
        self.config = RuntimeConfig(
            mode=self.runtime_mode,
            scan_interval=self.scan_interval,
            position_monitor_interval=self.position_monitor_interval,
            max_retries=MAX_RETRY_ATTEMPTS,
            retry_backoff=RETRY_BACKOFF_SECONDS,
        )
        self.runtime = ContinuousRuntime(
            config=self.config,
            scan_cycle_callback=lambda: self._execute_scan_cycle(verbose=False),
            position_monitor_callback=self._execute_position_monitor_cycle,
            cleanup_callback=self.close,
            lock=self._lock,
        )

    # -------------------------------------------------------------------------
    # Core Pipeline Execution Steps
    # -------------------------------------------------------------------------

    def _execute_scan_cycle(self, verbose: bool = True) -> list[CandidateWorkflowResult]:
        """
        Execute one full market analysis and paper trading pipeline cycle.
        """
        self.exchange.connect()

        # Fetch complete market snapshot
        response = self.market.get_all_tickers()
        tickers = BitgetParser.parse_tickers(response)

        # Update market cache
        for ticker in tickers:
            self.scanner.update(ticker)

        # Filter and rank
        eligible_tickers = self.eligibility_filter.filter(tickers)
        ranked_markets = self.ranking_engine.rank(eligible_tickers)

        if verbose:
            print()
            print("=" * 70)
            print("ATHENA MARKET PIPELINE")
            print("=" * 70)
            print(f"Parsed Tickers   : {len(tickers)}")
            print(f"Cached Tickers   : {self.scanner.get_ticker_count()}")
            print(f"Eligible Markets : {len(eligible_tickers)}")
            print(f"Ranked Markets   : {len(ranked_markets)}")
            print("=" * 70)

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
            for rank, market in enumerate(ranked_markets[:10], start=1):
                print(
                    f"{rank:<6}"
                    f"{market.symbol:<18}"
                    f"{market.score:<10.2f}"
                    f"{market.liquidity_score:<10.2f}"
                    f"{market.movement_score:<10.2f}"
                    f"{market.spread_score:<10.2f}"
                )
            print("=" * 70)

        # Multi-timeframe regime analysis
        if verbose:
            log(f"Analyzing Top {self.shortlist_limit} Markets (Candles -> Features -> Regimes)...")
        analyses = self.pipeline.analyze_shortlist(
            ranked_markets,
            limit=self.shortlist_limit,
        )

        if verbose:
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
                print(
                    f"{analysis.symbol:<14}"
                    f"{'OK':<10}"
                    f"{r1m.regime.value if r1m else 'n/a':<10}"
                    f"{r5m.regime.value if r5m else 'n/a':<10}"
                    f"{r15m.regime.value if r15m else 'n/a':<10}"
                    f"{r1h.regime.value if r1h else 'n/a':<10}"
                    f"{r4h.regime.value if r4h else 'n/a':<10}"
                )
            print("=" * 70)

        # Simulated Paper Trading under Thread-Safety Lock
        if verbose:
            log("Executing Simulated Paper Trading Workflow on Analyzed Shortlist...")

        with self._lock:
            paper_results = self.paper_workflow.process_shortlist(analyses)

        if verbose:
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

        return paper_results

    def _execute_position_monitor_cycle(self) -> list[PaperTrade]:
        """
        Execute one active position monitoring pass over all open positions.
        """
        return self.position_monitor.check_open_positions()

    # -------------------------------------------------------------------------
    # Application Entry Points
    # -------------------------------------------------------------------------

    def run_once(self) -> tuple[list[CandidateWorkflowResult], list[PaperTrade]]:
        """
        Execute a single pipeline pass and position monitoring check (One-Shot Mode).
        Preserves 100% backward-compatible CLI execution and outputs.
        """
        log("ATHENA Initialized in ONE_SHOT Mode")
        results = self._execute_scan_cycle(verbose=True)
        trades = self._execute_position_monitor_cycle()
        return results, trades

    def run_continuous(
        self,
        max_cycles: int | None = None,
        max_duration_seconds: float | None = None,
    ) -> RuntimeMetrics:
        """
        Start the coordinated continuous runtime (Continuous Mode).
        Runs scan cycles on scan_interval and position monitoring on position_monitor_interval
        until stopped via signal, deadline, max_cycles, or stop().
        """
        if max_cycles is not None or max_duration_seconds is not None:
            config = RuntimeConfig(
                mode=RuntimeMode.CONTINUOUS,
                scan_interval=self.scan_interval,
                position_monitor_interval=self.position_monitor_interval,
                max_retries=self.config.max_retries,
                retry_backoff=self.config.retry_backoff,
                max_cycles=max_cycles,
                max_duration_seconds=max_duration_seconds,
            )
            self.runtime = ContinuousRuntime(
                config=config,
                scan_cycle_callback=lambda: self._execute_scan_cycle(verbose=False),
                position_monitor_callback=self._execute_position_monitor_cycle,
                cleanup_callback=self.close,
                lock=self._lock,
            )

        return self.runtime.run()

    def run(self) -> Any:
        """
        Primary execution entry point. Dispatches based on configured runtime_mode.
        """
        if self.runtime_mode == RuntimeMode.CONTINUOUS:
            return self.run_continuous()
        else:
            return self.run_once()

    def stop(self, reason: str = "USER_REQUEST") -> None:
        """Initiate graceful shutdown of all running workers and flush persistent resources."""
        if hasattr(self, "runtime") and self.runtime:
            self.runtime.stop(reason=reason)
        self.close()

    def get_metrics(self) -> RuntimeMetrics:
        """Return runtime execution metrics."""
        return self.runtime.metrics

    def get_open_positions(self) -> tuple[PaperPosition, ...]:
        """Return currently open paper trading positions."""
        with self._lock:
            return self.paper_workflow.get_open_positions()

    def get_portfolio_snapshot(self) -> PaperPortfolio:
        """Return an immutable snapshot of current paper portfolio state."""
        with self._lock:
            return self.paper_workflow.get_portfolio_snapshot()

    def close(self) -> None:
        """Safely close paper workflow and underlying persistent storage."""
        if hasattr(self, "paper_workflow") and self.paper_workflow:
            self.paper_workflow.close()
