import importlib
import inspect
import pkgutil

from ..backtester.strategy_base import StrategyBase


def discover_strategies() -> list[type[StrategyBase]]:
    """Auto-discover all concrete StrategyBase subclasses in this package.

    Drop a new .py file into strategies/ with a class that subclasses
    StrategyBase, and it will be picked up here automatically.
    """
    classes = []
    for mod_info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{mod_info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, StrategyBase) and obj is not StrategyBase:
                classes.append(obj)
    return classes
