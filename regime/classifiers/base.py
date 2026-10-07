from abc import ABC, abstractmethod


class RegimeClassifier(ABC):

    @abstractmethod
    def classify(self, features):
        pass
