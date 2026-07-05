from dataclasses import dataclass


@dataclass
class Position:

    symbol: str

    side: str

    entry_price: float

    quantity: float

    pnl: float