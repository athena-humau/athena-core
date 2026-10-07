from datetime import datetime, timezone
import time
from typing import Sequence

from exchange.market_data import MarketData
from features.feature_engine import FeatureEngine
from market.analysis_models import MarketAnalysis
from market.multi_timeframe_manager import MultiTimeframeManager
from regime.market_regime_engine import MarketRegimeEngine
from scanner.ranking_engine import RankedMarket


class MarketAnalysisPipeline:
    """
    Coordinates historical candle extraction, feature computation,
    and market regime classification for ranked market opportunities.
    """

    DEFAULT_LOOKBACK_MS = 24 * 60 * 60 * 1000  # 24 hours of history

    def __init__(
        self,
        mtf_manager: MultiTimeframeManager | None = None,
        feature_engine: FeatureEngine | None = None,
        regime_engine: MarketRegimeEngine | None = None,
        granularities: Sequence[str] | None = None,
        lookback_ms: int = DEFAULT_LOOKBACK_MS,
    ):
        if mtf_manager is None:
            mtf_manager = MultiTimeframeManager()
        if feature_engine is None:
            feature_engine = FeatureEngine()
        if regime_engine is None:
            regime_engine = MarketRegimeEngine()

        if granularities is None:
            granularities = MultiTimeframeManager.DEFAULT_GRANULARITIES

        if not isinstance(lookback_ms, int) or isinstance(lookback_ms, bool) or lookback_ms <= 0:
            raise ValueError("lookback_ms must be a positive integer")

        self.mtf_manager = mtf_manager
        self.feature_engine = feature_engine
        self.regime_engine = regime_engine
        self.granularities = tuple(granularities)
        self.lookback_ms = lookback_ms

    def analyze_symbol(
        self,
        symbol: str,
        ranking: RankedMarket | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> MarketAnalysis:
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("Symbol must be a non-empty string")

        if end_time is None or start_time is None:
            computed_start, computed_end = self._calculate_aligned_window()
            start_time = computed_start if start_time is None else start_time
            end_time = computed_end if end_time is None else end_time

        analyzed_at = datetime.now(timezone.utc)

        try:
            candles_by_granularity = self.mtf_manager.get_historical_candles(
                symbol=symbol,
                start_time=start_time,
                end_time=end_time,
                granularities=self.granularities,
            )
        except Exception as error:
            return MarketAnalysis(
                symbol=symbol,
                ranking=ranking,
                candles_by_granularity={},
                features_by_granularity={},
                regimes_by_granularity={},
                success=False,
                error=f"Historical candles failed: {error}",
                analyzed_at=analyzed_at,
            )

        try:
            features_by_granularity = self.feature_engine.calculate(
                candles_by_granularity
            )
        except Exception as error:
            return MarketAnalysis(
                symbol=symbol,
                ranking=ranking,
                candles_by_granularity=candles_by_granularity,
                features_by_granularity={},
                regimes_by_granularity={},
                success=False,
                error=f"Feature calculation failed: {error}",
                analyzed_at=analyzed_at,
            )

        try:
            regimes_by_granularity = self.regime_engine.classify(
                features_by_granularity
            )
        except Exception as error:
            return MarketAnalysis(
                symbol=symbol,
                ranking=ranking,
                candles_by_granularity=candles_by_granularity,
                features_by_granularity=features_by_granularity,
                regimes_by_granularity={},
                success=False,
                error=f"Regime classification failed: {error}",
                analyzed_at=analyzed_at,
            )

        return MarketAnalysis(
            symbol=symbol,
            ranking=ranking,
            candles_by_granularity=candles_by_granularity,
            features_by_granularity=features_by_granularity,
            regimes_by_granularity=regimes_by_granularity,
            success=True,
            error=None,
            analyzed_at=analyzed_at,
        )

    def analyze_shortlist(
        self,
        ranked_markets: Sequence[RankedMarket],
        limit: int = 10,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[MarketAnalysis]:
        if not isinstance(ranked_markets, (list, tuple)):
            raise ValueError("ranked_markets must be a list or tuple")

        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        if end_time is None or start_time is None:
            computed_start, computed_end = self._calculate_aligned_window()
            start_time = computed_start if start_time is None else start_time
            end_time = computed_end if end_time is None else end_time

        shortlist = ranked_markets[:limit]
        results = []

        for market in shortlist:
            if not isinstance(market, RankedMarket):
                raise ValueError("Shortlist items must be RankedMarket instances")

            analysis = self.analyze_symbol(
                symbol=market.symbol,
                ranking=market,
                start_time=start_time,
                end_time=end_time,
            )
            results.append(analysis)

        return results

    def _calculate_aligned_window(self) -> tuple[int, int]:
        max_interval_ms = max(
            MarketData.CANDLE_GRANULARITY_MS[granularity]
            for granularity in self.granularities
        )
        now_ms = int(time.time() * 1000)
        end_time = (now_ms // max_interval_ms) * max_interval_ms
        start_time = end_time - self.lookback_ms
        return start_time, end_time
