from dataclasses import dataclass


@dataclass
class OrderBook:

    bids: list

    asks: list