import math
from datetime import datetime, timedelta
from typing import Mapping

from features.models import TimeframeFeatures
from regime.classifiers.base import RegimeClassifier
from regime.classifiers.basic_price import BasicPriceRegimeClassifier
from regime.models import Regime, TimeframeRegime


class MarketRegimeEngine:

    def __init__(self, classifier=None):

        if classifier is None:
            classifier = BasicPriceRegimeClassifier()

        if not isinstance(classifier, RegimeClassifier):
            raise ValueError("Classifier must implement RegimeClassifier")

        self.classifier = classifier

    def classify(self, features_by_granularity):

        if not isinstance(features_by_granularity, dict):
            raise ValueError("Features must be grouped in a dictionary")

        regimes_by_granularity = {}
        symbol = None

        for granularity in sorted(features_by_granularity):
            features = features_by_granularity[granularity]
            timeframe_symbol = self._validate_features(
                granularity,
                features,
            )

            if timeframe_symbol is not None:
                if symbol is None:
                    symbol = timeframe_symbol
                elif timeframe_symbol != symbol:
                    raise ValueError("Timeframe features must use one symbol")

            regime = self.classifier.classify(features)

            if not isinstance(regime, Regime):
                raise ValueError("Classifier must return a Regime")

            regimes_by_granularity[granularity] = TimeframeRegime(
                symbol=features.symbol,
                granularity=features.granularity,
                timestamp=features.timestamp,
                candle_count=features.candle_count,
                regime=regime,
            )

        return regimes_by_granularity

    @staticmethod
    def _validate_features(granularity, features):

        if not isinstance(granularity, str) or not granularity:
            raise ValueError("Feature granularity must be a non-empty string")

        if not isinstance(features, TimeframeFeatures):
            raise ValueError("Regime input must contain TimeframeFeatures")

        if features.granularity != granularity:
            raise ValueError("Feature granularity does not match its group")

        if (
            not isinstance(features.candle_count, int)
            or isinstance(features.candle_count, bool)
            or features.candle_count < 0
        ):
            raise ValueError("Feature candle_count must be non-negative")

        if not isinstance(features.values, Mapping):
            raise ValueError("Feature values must be a mapping")

        MarketRegimeEngine._validate_values(features.values)

        if features.candle_count == 0:
            if features.symbol is not None or features.timestamp is not None:
                raise ValueError("Empty features cannot include candle metadata")

            return None

        if not isinstance(features.symbol, str) or not features.symbol:
            raise ValueError("Feature symbol must be a non-empty string")

        if not isinstance(features.timestamp, datetime):
            raise ValueError("Feature timestamp must be a datetime")

        if (
            features.timestamp.tzinfo is None
            or features.timestamp.utcoffset() != timedelta(0)
        ):
            raise ValueError("Feature timestamp must be UTC")

        return features.symbol

    @staticmethod
    def _validate_values(values):

        for name, value in values.items():

            if not isinstance(name, str) or not name:
                raise ValueError("Feature name must be a non-empty string")

            if type(value) not in (int, float, bool):
                raise ValueError("Feature value must be int, float, or bool")

            if type(value) is float and not math.isfinite(value):
                raise ValueError("Feature value must be finite")
