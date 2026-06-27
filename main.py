from core.banner import show_banner
from core.startup import startup
from core.logger import log

from exchange.manager import ExchangeManager

show_banner()

startup()

manager = ExchangeManager()

exchange = manager.get_exchange()

exchange.connect()

log("ATHENA Core Initialized Successfully")