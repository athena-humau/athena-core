from dataclasses import dataclass


@dataclass
class Ticker:
    symbol: str

    price: float

    bid: float

    ask: float