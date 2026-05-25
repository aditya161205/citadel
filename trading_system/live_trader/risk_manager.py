def size_order(cash: float, price: float, position_size: float,
               capital_per_symbol: float) -> int:
    """Compute the number of shares to buy for an equal-weight allocation.

    Uses the smaller of:
      - capital_per_symbol * position_size  (equal-weight target)
      - available cash * position_size      (can't spend more than you have)
    """
    max_spend = min(capital_per_symbol, cash) * position_size
    qty = int(max_spend // price)
    return qty if qty > 0 else 0
