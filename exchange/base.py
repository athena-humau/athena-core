from abc import ABC, abstractmethod

class BaseExchange(ABC):

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def get_balance(self):
        pass

    @abstractmethod
    def get_price(self, symbol):
        pass

    @abstractmethod
    def place_order(self, symbol, side, quantity):
        pass

    @abstractmethod
    def close(self):
        pass