from abc import ABC, abstractmethod


class FeatureCalculator(ABC):

    name = ""
    minimum_candles = 1

    @abstractmethod
    def calculate(self, candles):
        pass
