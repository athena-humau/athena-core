from dataclasses import dataclass
from datetime import datetime


@dataclass
class Candle:
    symbol: str

    timestamp: datetime

    open: float
    high: float
    low: float
    close: float

    volume: float
    quote_volume: float

    granularity: str
