from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from config.settings import (
    MAX_RETRY_ATTEMPTS,
    PAPER_DB_PATH,
    POSITION_MONITOR_INTERVAL,
    RETRY_BACKOFF_SECONDS,
    RUNTIME_MODE,
    SCAN_INTERVAL,
    SHORTLIST_ANALYSIS_LIMIT,
)
from core.app import Athena
from core.runtime import (
    ContinuousRuntime,
    PositionMonitor,
    RuntimeConfig,
    RuntimeMetrics,
    RuntimeMode,
)
from data.candle import Candle
from data.ticker import Ticker
from execution.paper_models import (
    PaperExecutionResult,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    PaperTrade,
)
from execution.paper_workflow import (
    CandidateProcessingStatus,
    CandidateWorkflowResult,
    PaperTradingWorkflow,
)
from features.models import TimeframeFeatures
from market.analysis_models import MarketAnalysis
from regime.models import Regime, TimeframeRegime
from scanner.ranking_engine import RankedMarket


def make_test_ticker(
    symbol: str = "BTCUSDT",
    price: float = 50000.0,
    bid: float = 49990.0,
    ask: float = 50010.0,
) -> Ticker:
    return Ticker(
        symbol=symbol,
        price=price,
        bid=bid,
        ask=ask,
        volume_24h=15000.0,
        quote_volume_24h=750000000.0,
        change_24h=2.5,
        funding_rate=0.0001,
        mark_price=price,
        open_interest=50000.0,
    )


def make_test_candle(
    symbol: str = "BTCUSDT",
    granularity: str = "1H",
    open_price: float = 50000.0,
    high_price: float = 50500.0,
    low_price: float = 49500.0,
    close_price: float = 50000.0,
) -> Candle:
    return Candle(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=100.0,
        quote_volume=5000000.0,
        granularity=granularity,
    )


def make_test_market_analysis(
    symbol: str = "BTCUSDT",
    regime: Regime = Regime.BULLISH,
    last_close: float = 50000.0,
    ema_9: float = 47500.0,
    ema_21: float = 45000.0,
    rsi_14: float = 60.0,
    atr_14: float = 500.0,
    granularity: str = "1H",
    success: bool = True,
    error: str | None = None,
    candle_count: int = 50,
) -> MarketAnalysis:
    now_ts = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    tf_regime = TimeframeRegime(
        symbol=symbol,
        granularity=granularity,
        timestamp=now_ts,
        candle_count=candle_count,
        regime=regime,
    )
    feature_vals = {
        "last_close": last_close,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "rsi_14": rsi_14,
        "atr_14": atr_14,
    }
    tf_features = TimeframeFeatures(
        symbol=symbol,
        granularity=granularity,
        timestamp=now_ts,
        candle_count=candle_count,
        values=feature_vals,
    )
    test_candle = Candle(
        symbol=symbol,
        timestamp=now_ts,
        open=last_close * 0.995,
        high=last_close * 1.005,
        low=last_close * 0.990,
        close=last_close,
        volume=100.0,
        quote_volume=100.0 * last_close,
        granularity=granularity,
    )
    ranked = RankedMarket(
        symbol=symbol,
        score=88.0,
        liquidity_score=92.0,
        movement_score=85.0,
        spread_score=90.0,
        open_interest_score=80.0,
        funding_score=95.0,
    )
    return MarketAnalysis(
        symbol=symbol,
        ranking=ranked,
        candles_by_granularity={granularity: [test_candle]},
        features_by_granularity={granularity: tf_features},
        regimes_by_granularity={granularity: tf_regime},
        success=success,
        error=error,
        analyzed_at=now_ts,
    )


class TestAthenaRuntimeConfig(unittest.TestCase):
    """Test RuntimeConfig validation and default parameters."""

    def test_default_config(self):
        cfg = RuntimeConfig()
        self.assertEqual(cfg.mode, RuntimeMode.ONE_SHOT)
        self.assertEqual(cfg.scan_interval, 5.0)
        self.assertEqual(cfg.position_monitor_interval, 1.0)
        self.assertEqual(cfg.max_retries, 3)
        self.assertEqual(cfg.retry_backoff, 1.0)
        self.assertIsNone(cfg.max_cycles)
        self.assertIsNone(cfg.max_duration_seconds)

    def test_custom_valid_config(self):
        cfg = RuntimeConfig(
            mode=RuntimeMode.CONTINUOUS,
            scan_interval=10.0,
            position_monitor_interval=2.0,
            max_retries=5,
            retry_backoff=0.5,
            max_cycles=10,
            max_duration_seconds=60.0,
        )
        self.assertEqual(cfg.mode, RuntimeMode.CONTINUOUS)
        self.assertEqual(cfg.scan_interval, 10.0)
        self.assertEqual(cfg.position_monitor_interval, 2.0)
        self.assertEqual(cfg.max_retries, 5)
        self.assertEqual(cfg.retry_backoff, 0.5)
        self.assertEqual(cfg.max_cycles, 10)
        self.assertEqual(cfg.max_duration_seconds, 60.0)

    def test_invalid_scan_interval(self):
        with self.assertRaises(ValueError):
            RuntimeConfig(scan_interval=0.0)
        with self.assertRaises(ValueError):
            RuntimeConfig(scan_interval=-5.0)

    def test_invalid_position_monitor_interval(self):
        with self.assertRaises(ValueError):
            RuntimeConfig(position_monitor_interval=0.0)
        with self.assertRaises(ValueError):
            RuntimeConfig(position_monitor_interval=-1.0)

    def test_invalid_max_retries(self):
        with self.assertRaises(ValueError):
            RuntimeConfig(max_retries=-1)

    def test_invalid_retry_backoff(self):
        with self.assertRaises(ValueError):
            RuntimeConfig(retry_backoff=-0.5)

    def test_invalid_max_cycles(self):
        with self.assertRaises(ValueError):
            RuntimeConfig(max_cycles=0)
        with self.assertRaises(ValueError):
            RuntimeConfig(max_cycles=-2)

    def test_invalid_max_duration_seconds(self):
        with self.assertRaises(ValueError):
            RuntimeConfig(max_duration_seconds=0.0)
        with self.assertRaises(ValueError):
            RuntimeConfig(max_duration_seconds=-10.0)


class TestAthenaInitialization(unittest.TestCase):
    """Test Athena app initialization and dependency injection."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "athena_test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_initialization(self):
        with patch("core.app.ExchangeManager") as mock_mgr, \
             patch("core.app.MarketData") as mock_md, \
             patch("core.app.MarketScanner") as mock_scanner, \
             patch("core.app.MarketEligibilityFilter") as mock_filter, \
             patch("core.app.RankingEngine") as mock_ranker, \
             patch("core.app.MarketAnalysisPipeline") as mock_pipe:

            mock_exchange = Mock()
            mock_mgr.return_value.get_exchange.return_value = mock_exchange

            app = Athena(db_path=self.db_path)
            self.assertEqual(app.runtime_mode, RuntimeMode.ONE_SHOT)
            self.assertEqual(app.scan_interval, SCAN_INTERVAL)
            self.assertEqual(app.position_monitor_interval, POSITION_MONITOR_INTERVAL)
            self.assertEqual(app.shortlist_limit, SHORTLIST_ANALYSIS_LIMIT)
            self.assertIsNotNone(app.paper_workflow)
            self.assertIsNotNone(app.position_monitor)
            self.assertIsNotNone(app.runtime)
            app.close()

    def test_custom_injected_components(self):
        mock_exchange = Mock()
        mock_market = Mock()
        mock_scanner = Mock()
        mock_filter = Mock()
        mock_ranking = Mock()
        mock_pipeline = Mock()
        mock_workflow = Mock()
        mock_workflow.storage = None
        mock_monitor = Mock()

        app = Athena(
            db_path=self.db_path,
            runtime_mode="CONTINUOUS",
            scan_interval=2.5,
            position_monitor_interval=0.5,
            shortlist_limit=3,
            exchange=mock_exchange,
            market=mock_market,
            scanner=mock_scanner,
            eligibility_filter=mock_filter,
            ranking_engine=mock_ranking,
            pipeline=mock_pipeline,
            paper_workflow=mock_workflow,
            position_monitor=mock_monitor,
        )

        self.assertEqual(app.runtime_mode, RuntimeMode.CONTINUOUS)
        self.assertEqual(app.scan_interval, 2.5)
        self.assertEqual(app.position_monitor_interval, 0.5)
        self.assertEqual(app.shortlist_limit, 3)
        self.assertIs(app.exchange, mock_exchange)
        self.assertIs(app.market, mock_market)
        self.assertIs(app.paper_workflow, mock_workflow)
        self.assertIs(app.position_monitor, mock_monitor)
        app.close()

    def test_simulation_lock_enforcement(self):
        """ContinuousRuntime must raise RuntimeError if TRADING_MODE != 'SIMULATION'."""
        with patch("core.runtime.TRADING_MODE", "LIVE"):
            runtime = ContinuousRuntime(
                config=RuntimeConfig(),
                scan_cycle_callback=Mock(),
                position_monitor_callback=Mock(),
            )
            with self.assertRaises(RuntimeError) as ctx:
                runtime.start()
            self.assertIn("CRITICAL SAFETY LOCK", str(ctx.exception))
            self.assertIn("Live trading is prohibited", str(ctx.exception))


class TestAthenaRunOnce(unittest.TestCase):
    """Test 100% backward-compatible run_once execution."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "athena_test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_run_once_pipeline_execution(self):
        mock_exchange = Mock()
        mock_market = Mock()
        mock_scanner = Mock()
        mock_scanner.get_ticker_count.return_value = 1
        mock_filter = Mock()
        mock_ranking = Mock()
        mock_pipeline = Mock()
        mock_workflow = Mock()
        mock_workflow.storage = None

        raw_ticker_response = {
            "code": "00000",
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "lastPr": "50000.0",
                    "bidPr": "49990.0",
                    "askPr": "50010.0",
                    "baseVolume": "1000",
                    "quoteVolume": "50000000",
                    "change24h": "1.5",
                    "fundingRate": "0.0001",
                    "markPrice": "50000.0",
                    "holdingAmount": "20000",
                }
            ],
        }
        mock_market.get_all_tickers.return_value = raw_ticker_response

        ranked_mkt = RankedMarket(
            symbol="BTCUSDT",
            score=90.0,
            liquidity_score=95.0,
            movement_score=85.0,
            spread_score=90.0,
            open_interest_score=80.0,
            funding_score=95.0,
        )
        mock_filter.filter.return_value = [make_test_ticker("BTCUSDT")]
        mock_ranking.rank.return_value = [ranked_mkt]

        analysis = make_test_market_analysis(symbol="BTCUSDT")
        mock_pipeline.analyze_shortlist.return_value = [analysis]

        workflow_res = CandidateWorkflowResult(
            symbol="BTCUSDT",
            status=CandidateProcessingStatus.PAPER_FILLED,
            order=Mock(spec=PaperOrder),
            position=Mock(spec=PaperPosition),
        )
        mock_workflow.process_shortlist.return_value = [workflow_res]
        mock_workflow.get_open_positions.return_value = ()
        mock_workflow.get_portfolio_snapshot.return_value = PaperPortfolio(
            starting_equity=10000.0,
            cash=10000.0,
            reserved_margin=0.0,
            open_notional=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            fees_paid=0.0,
            total_equity=10000.0,
            open_positions=(),
        )

        app = Athena(
            db_path=self.db_path,
            runtime_mode="ONE_SHOT",
            exchange=mock_exchange,
            market=mock_market,
            scanner=mock_scanner,
            eligibility_filter=mock_filter,
            ranking_engine=mock_ranking,
            pipeline=mock_pipeline,
            paper_workflow=mock_workflow,
        )

        results, trades = app.run_once()

        mock_exchange.connect.assert_called_once()
        mock_market.get_all_tickers.assert_called_once()
        mock_scanner.update.assert_called_once()
        mock_filter.filter.assert_called_once()
        mock_ranking.rank.assert_called_once()
        mock_pipeline.analyze_shortlist.assert_called_once_with([ranked_mkt], limit=app.shortlist_limit)
        mock_workflow.process_shortlist.assert_called_once_with([analysis])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].symbol, "BTCUSDT")
        self.assertEqual(trades, [])
        app.close()

    def test_run_dispatches_to_run_once_when_one_shot_mode(self):
        app = Athena(db_path=self.db_path, runtime_mode="ONE_SHOT")
        with patch.object(app, "run_once", return_value=([], [])) as mock_run_once:
            res = app.run()
            mock_run_once.assert_called_once()
            self.assertEqual(res, ([], []))
        app.close()


class TestPositionMonitor(unittest.TestCase):
    """Test PositionMonitor active monitoring and Stop-Loss / Take-Profit trigger logic."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "athena_test.db")
        self.workflow = PaperTradingWorkflow(db_path=self.db_path)
        self.market = Mock()
        self.lock = threading.RLock()
        self.monitor = PositionMonitor(
            market=self.market,
            workflow=self.workflow,
            lock=self.lock,
            max_retries=2,
            retry_backoff=0.01,
        )

    def tearDown(self):
        self.workflow.close()
        self.temp_dir.cleanup()

    def _open_test_position(self, symbol="BTCUSDT", last_close=50000.0, atr=500.0) -> CandidateWorkflowResult:
        analysis = make_test_market_analysis(
            symbol=symbol,
            regime=Regime.BULLISH,
            last_close=last_close,
            ema_9=last_close * 0.95,
            ema_21=last_close * 0.90,
            atr_14=atr,
        )
        res = self.workflow.process_market_analysis(analysis)
        self.assertEqual(res.status, CandidateProcessingStatus.PAPER_FILLED)
        return res

    def test_check_open_positions_empty(self):
        closed = self.monitor.check_open_positions()
        self.assertEqual(closed, [])
        self.market.get_ticker.assert_not_called()

    def test_check_open_positions_sl_exit(self):
        """Verify position monitor detects price drop and executes SL exit."""
        res = self._open_test_position("BTCUSDT", last_close=50000.0, atr=500.0)
        self.assertEqual(len(self.workflow.get_open_positions()), 1)
        open_pos = self.workflow.get_open_positions()[0]
        # With entry 50000 and atr 500, SL is 49000
        self.assertEqual(open_pos.stop_loss_price, 49000.0)

        # Mock market ticker price drop below stop_loss (48500 <= 49000)
        self.market.get_ticker.return_value = {
            "code": "00000",
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "lastPr": "48500.0",
                    "bidPr": "48490.0",
                    "askPr": "48510.0",
                }
            ],
        }

        closed_trades = self.monitor.check_open_positions()
        self.assertEqual(len(closed_trades), 1)
        trade = closed_trades[0]
        self.assertEqual(trade.symbol, "BTCUSDT")
        self.assertEqual(trade.exit_price, 49000.0)
        self.assertEqual(trade.exit_reason, "STOP_LOSS_TRIGGERED")
        self.assertLess(trade.net_pnl, 0.0)

        # Position must be closed in workflow
        self.assertEqual(len(self.workflow.get_open_positions()), 0)

        # Verify trade is persisted in database
        persisted_state = self.workflow.storage.load_ledger_state()
        self.assertIsNotNone(persisted_state)
        self.assertEqual(len(persisted_state.closed_trades), 1)
        self.assertEqual(persisted_state.closed_trades[0].trade_id, trade.trade_id)

    def test_check_open_positions_tp_exit(self):
        """Verify position monitor detects price surge and executes TP exit."""
        res = self._open_test_position("BTCUSDT", last_close=50000.0, atr=500.0)
        self.assertEqual(len(self.workflow.get_open_positions()), 1)
        open_pos = self.workflow.get_open_positions()[0]
        # TP is 50000 + 3*500 = 51500
        self.assertEqual(open_pos.take_profit_price, 51500.0)

        # Mock price surge above take_profit (52500 >= 51500)
        self.market.get_ticker.return_value = {
            "code": "00000",
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "lastPr": "52500.0",
                    "bidPr": "52490.0",
                    "askPr": "52510.0",
                }
            ],
        }

        closed_trades = self.monitor.check_open_positions()
        self.assertEqual(len(closed_trades), 1)
        trade = closed_trades[0]
        self.assertEqual(trade.symbol, "BTCUSDT")
        self.assertEqual(trade.exit_price, 51500.0)
        self.assertEqual(trade.exit_reason, "TAKE_PROFIT_TRIGGERED")
        self.assertGreater(trade.net_pnl, 0.0)
        self.assertEqual(len(self.workflow.get_open_positions()), 0)

    def test_check_candle_protective_exit(self):
        """Verify check_candle processes protective exits via candle high/low."""
        self._open_test_position("BTCUSDT", last_close=50000.0, atr=500.0)
        self.assertEqual(len(self.workflow.get_open_positions()), 1)

        candle = make_test_candle(
            symbol="BTCUSDT",
            open_price=50000.0,
            high_price=50200.0,
            low_price=48800.0,  # breached stop loss 49000
            close_price=49100.0,
        )

        closed = self.monitor.check_candle(candle)
        self.assertEqual(len(closed), 1)
        self.assertIn("STOP_LOSS", closed[0].exit_reason)
        self.assertEqual(len(self.workflow.get_open_positions()), 0)

    def test_check_candle_invalid_argument(self):
        with self.assertRaises(ValueError):
            self.monitor.check_candle("not_a_candle")  # type: ignore

    def test_market_data_failure_resilience(self):
        """Position monitor must not crash or close positions on transient API failure."""
        self._open_test_position("BTCUSDT", last_close=50000.0, atr=500.0)
        self.assertEqual(len(self.workflow.get_open_positions()), 1)

        # Mock exception on get_ticker
        self.market.get_ticker.side_effect = ConnectionError("Bitget timeout")
        closed = self.monitor.check_open_positions()

        self.assertEqual(closed, [])
        # Position must remain intact
        self.assertEqual(len(self.workflow.get_open_positions()), 1)
        self.assertEqual(self.market.get_ticker.call_count, self.monitor.max_retries)

    def test_market_data_invalid_payload_resilience(self):
        """Position monitor must safely ignore malformed payload or non-positive price."""
        self._open_test_position("BTCUSDT", last_close=50000.0, atr=500.0)

        # Non-positive price
        self.market.get_ticker.return_value = {
            "code": "00000",
            "data": [{"symbol": "BTCUSDT", "lastPr": "0.0", "bidPr": "0.0", "askPr": "0.0"}],
        }
        closed = self.monitor.check_open_positions()
        self.assertEqual(closed, [])
        self.assertEqual(len(self.workflow.get_open_positions()), 1)


class TestContinuousRuntimeOrchestration(unittest.TestCase):
    """Test ContinuousRuntime scheduling, workers, limits, and responsive shutdown."""

    def test_max_cycles_termination(self):
        scan_count = 0
        monitor_count = 0

        def scan_cb():
            nonlocal scan_count
            scan_count += 1
            return []

        def monitor_cb():
            nonlocal monitor_count
            monitor_count += 1
            return []

        config = RuntimeConfig(
            mode=RuntimeMode.CONTINUOUS,
            scan_interval=0.01,
            position_monitor_interval=0.01,
            max_cycles=3,
        )
        runtime = ContinuousRuntime(
            config=config,
            scan_cycle_callback=scan_cb,
            position_monitor_callback=monitor_cb,
        )

        metrics = runtime.run()
        self.assertFalse(runtime.is_running)
        self.assertGreaterEqual(metrics.cycles_completed, 3)
        self.assertEqual(metrics.exit_reason, "MAX_CYCLES_REACHED")
        self.assertIsNotNone(metrics.start_time)
        self.assertIsNotNone(metrics.stop_time)

    def test_max_duration_termination(self):
        def scan_cb():
            time.sleep(0.01)
            return []

        def monitor_cb():
            time.sleep(0.01)
            return []

        config = RuntimeConfig(
            mode=RuntimeMode.CONTINUOUS,
            scan_interval=0.02,
            position_monitor_interval=0.02,
            max_duration_seconds=0.15,
        )
        runtime = ContinuousRuntime(
            config=config,
            scan_cycle_callback=scan_cb,
            position_monitor_callback=monitor_cb,
        )

        metrics = runtime.run()
        self.assertFalse(runtime.is_running)
        self.assertEqual(metrics.exit_reason, "DEADLINE_REACHED")
        self.assertGreater(metrics.cycles_completed, 0)
        self.assertGreater(metrics.monitor_checks_completed, 0)

    def test_responsive_shutdown_interrupts_long_sleep(self):
        """Workers configured with long intervals must terminate immediately when stop() is called."""
        config = RuntimeConfig(
            mode=RuntimeMode.CONTINUOUS,
            scan_interval=60.0,
            position_monitor_interval=60.0,
        )
        runtime = ContinuousRuntime(
            config=config,
            scan_cycle_callback=lambda: [],
            position_monitor_callback=lambda: [],
        )

        runtime.start()
        self.assertTrue(runtime.is_running)
        # Give threads a moment to enter sleep
        time.sleep(0.05)

        start_stop = time.time()
        runtime.stop(reason="USER_INTERRUPT")
        stop_duration = time.time() - start_stop

        self.assertFalse(runtime.is_running)
        self.assertLess(stop_duration, 1.0, f"Shutdown took too long: {stop_duration:.2f}s")
        self.assertEqual(runtime.metrics.exit_reason, "USER_INTERRUPT")

    def test_cleanup_callback_invoked_on_stop(self):
        cleanup_called = False

        def cleanup():
            nonlocal cleanup_called
            cleanup_called = True

        config = RuntimeConfig(
            mode=RuntimeMode.CONTINUOUS,
            scan_interval=0.01,
            position_monitor_interval=0.01,
            max_cycles=1,
        )
        runtime = ContinuousRuntime(
            config=config,
            scan_cycle_callback=lambda: [],
            position_monitor_callback=lambda: [],
            cleanup_callback=cleanup,
        )
        runtime.run()
        self.assertTrue(cleanup_called)

    def test_thread_safety_under_rlock(self):
        """Verify lock ensures mutual exclusion between scan cycle and position monitor."""
        lock = threading.RLock()
        active_critical_section = 0
        max_concurrent_seen = 0

        def scan_cb():
            nonlocal active_critical_section, max_concurrent_seen
            with lock:
                active_critical_section += 1
                if active_critical_section > max_concurrent_seen:
                    max_concurrent_seen = active_critical_section
                time.sleep(0.02)
                active_critical_section -= 1
            return []

        def monitor_cb():
            nonlocal active_critical_section, max_concurrent_seen
            with lock:
                active_critical_section += 1
                if active_critical_section > max_concurrent_seen:
                    max_concurrent_seen = active_critical_section
                time.sleep(0.02)
                active_critical_section -= 1
            return []

        config = RuntimeConfig(
            mode=RuntimeMode.CONTINUOUS,
            scan_interval=0.01,
            position_monitor_interval=0.01,
            max_cycles=2,
        )
        runtime = ContinuousRuntime(
            config=config,
            scan_cycle_callback=scan_cb,
            position_monitor_callback=monitor_cb,
            lock=lock,
        )
        runtime.run()

        self.assertEqual(max_concurrent_seen, 1, "Mutual exclusion violated under shared lock")


class TestAthenaRestartPersistence(unittest.TestCase):
    """Test full application shutdown and restart persistence."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "athena_restart_test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_positions_and_cash_survive_application_restart(self):
        mock_exchange = Mock()
        mock_market = Mock()
        app1 = None
        app2 = None

        try:
            # 1. First Athena session opens a position
            app1 = Athena(
                db_path=self.db_path,
                runtime_mode="ONE_SHOT",
                exchange=mock_exchange,
                market=mock_market,
            )

            analysis = make_test_market_analysis(
                symbol="BTCUSDT",
                regime=Regime.BULLISH,
                last_close=50000.0,
                ema_9=47500.0,
                ema_21=45000.0,
                atr_14=500.0,
            )
            res = app1.paper_workflow.process_market_analysis(analysis)
            self.assertEqual(res.status, CandidateProcessingStatus.PAPER_FILLED)

            snap1 = app1.get_portfolio_snapshot()
            self.assertEqual(len(snap1.open_positions), 1)
            expected_cash = snap1.cash
            expected_margin = snap1.reserved_margin
            open_pos = snap1.open_positions[0]

            # Gracefully stop session 1
            app1.stop(reason="SESSION_1_SHUTDOWN")

            # 2. Second Athena session boots from the exact same SQLite database
            app2 = Athena(
                db_path=self.db_path,
                runtime_mode="ONE_SHOT",
                exchange=mock_exchange,
                market=mock_market,
            )
            snap2 = app2.get_portfolio_snapshot()

            self.assertEqual(len(snap2.open_positions), 1)
            recovered_pos = snap2.open_positions[0]
            self.assertEqual(recovered_pos.symbol, "BTCUSDT")
            self.assertEqual(recovered_pos.quantity, open_pos.quantity)
            self.assertEqual(recovered_pos.entry_price, open_pos.entry_price)
            self.assertEqual(recovered_pos.stop_loss_price, open_pos.stop_loss_price)
            self.assertEqual(recovered_pos.take_profit_price, open_pos.take_profit_price)
            self.assertAlmostEqual(snap2.cash, expected_cash, places=4)
            self.assertAlmostEqual(snap2.reserved_margin, expected_margin, places=4)

            # 3. Position monitor in session 2 can immediately monitor and exit recovered position
            app2.market.get_ticker.return_value = {
                "code": "00000",
                "data": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPr": "55000.0",
                        "bidPr": "54990.0",
                        "askPr": "55010.0",
                    }
                ],
            }
            closed_trades = app2.position_monitor.check_open_positions()
            self.assertEqual(len(closed_trades), 1)
            self.assertEqual(closed_trades[0].exit_reason, "TAKE_PROFIT_TRIGGERED")
            self.assertEqual(len(app2.get_open_positions()), 0)

            app2.stop(reason="SESSION_2_SHUTDOWN")
        finally:
            if app1 is not None:
                app1.close()
            if app2 is not None:
                app2.close()


if __name__ == "__main__":
    unittest.main()
