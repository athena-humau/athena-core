from exchange.base import BaseExchange

class BitgetExchange(BaseExchange):

    def connect(self):
        print("Connecting to Bitget...")

    def get_balance(self):
        print("Getting Balance...")

    def get_price(self, symbol):
        print(f"Getting price for {symbol}")

    def place_order(self, symbol, side, quantity):
        print(f"Placing {side} order")

    def close(self):
        print("Closing connection")