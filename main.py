from core.banner import show_banner
from core.startup import startup
from core.logger import log

from exchange.manager import ExchangeManager
from exchange.market_data import MarketData
from exchange.parser import BitgetParser

# Startup
show_banner()
startup()

# Exchange
manager = ExchangeManager()
exchange = manager.get_exchange()
exchange.connect()

log("ATHENA Core Initialized Successfully")

# Live Market Data
market = MarketData()

response = market.get_ticker("BTCUSDT")

ticker = BitgetParser.parse_ticker(response)

print()
print("=" * 40)
print("LIVE MARKET DATA")
print("=" * 40)
print(f"Symbol : {ticker.symbol}")
print(f"Price  : {ticker.price}")
print(f"Bid    : {ticker.bid}")
print(f"Ask    : {ticker.ask}")
print("=" * 40)
from scanner.market_scanner import MarketScanner

scanner = MarketScanner()

scanner.update(ticker)

scanner.show_market()
from config.secrets import TRADING_MODE

print()
print(f"Trading Mode : {TRADING_MODE}")