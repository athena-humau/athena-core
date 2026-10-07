from strategy.base import BaseStrategy


class StrategyRegistry:
    """
    Registry for managing available ATHENA trading strategies.
    Ensures safe strategy discovery, uniqueness, and lookup.
    """

    def __init__(self):
        self._strategies: dict[str, BaseStrategy] = {}

    def register(self, strategy: BaseStrategy) -> None:
        if not isinstance(strategy, BaseStrategy):
            raise ValueError("strategy must be an instance of BaseStrategy")

        if strategy.strategy_id in self._strategies:
            raise ValueError(f"Strategy with strategy_id '{strategy.strategy_id}' is already registered")

        self._strategies[strategy.strategy_id] = strategy

    def get(self, strategy_id: str) -> BaseStrategy:
        if not isinstance(strategy_id, str) or not strategy_id:
            raise ValueError("strategy_id must be a non-empty string")

        if strategy_id not in self._strategies:
            raise KeyError(f"Strategy '{strategy_id}' not found in registry")

        return self._strategies[strategy_id]

    def has(self, strategy_id: str) -> bool:
        return strategy_id in self._strategies

    def list_strategies(self) -> list[str]:
        return sorted(list(self._strategies.keys()))

    def unregister(self, strategy_id: str) -> None:
        if strategy_id in self._strategies:
            del self._strategies[strategy_id]

    def clear(self) -> None:
        self._strategies.clear()

    def __len__(self) -> int:
        return len(self._strategies)
