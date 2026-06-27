from config.settings import EXCHANGE
from exchange.bitget import BitgetExchange

class ExchangeManager:

    def __init__(self):

        if EXCHANGE == "Bitget":
            self.exchange = BitgetExchange()

        else:
            raise Exception("Exchange not supported")

    def get_exchange(self):
        return self.exchange