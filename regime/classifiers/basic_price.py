import math

from regime.classifiers.base import RegimeClassifier
from regime.models import Regime


class BasicPriceRegimeClassifier(RegimeClassifier):

    def classify(self, features):

        last_open = features.values.get("last_open")
        last_close = features.values.get("last_close")

        if not self._is_numeric(last_open) or not self._is_numeric(last_close):
            return Regime.UNKNOWN

        if last_close > last_open:
            return Regime.BULLISH

        if last_close < last_open:
            return Regime.BEARISH

        return Regime.NEUTRAL

    @staticmethod
    def _is_numeric(value):

        return (
            type(value) in (int, float)
            and (type(value) is int or math.isfinite(value))
        )
