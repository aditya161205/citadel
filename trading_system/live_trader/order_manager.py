from .risk_manager import size_order


def decide_order(signal: int, symbol: str, price: float,
                 positions: dict, cash: float,
                 position_size: float, capital_per_symbol: float) -> dict | None:
    """Translate a signal + current state into an order (or None).

    signal  1 = buy,  -1 = sell,  0 = hold.
    Returns {"side": "BUY"/"SELL", "qty": int} or None.
    """
    held = positions.get(symbol, 0)

    if signal == 1 and held == 0:
        qty = size_order(cash, price, position_size, capital_per_symbol)
        if qty > 0:
            return {"side": "BUY", "qty": qty}

    elif signal == -1 and held > 0:
        return {"side": "SELL", "qty": held}

    return None
