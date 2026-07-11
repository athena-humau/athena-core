from dataclasses import dataclass


@dataclass
class Ticker:
    symbol: str

    price: float

    bid: float

    ask: float

    volume_24h: float

    quote_volume_24h: float

    change_24h: float

    funding_rate: float

    mark_price: float

    open_interest: float

    @property
    def spread(self):

        return self.ask - self.bid

    @property
    def spread_pct(self):

        if self.price <= 0:
            return 0.0

        return (self.spread / self.price) * 100