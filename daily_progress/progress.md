# 19/5
"The technical analyst believes that anything that can possibly affect the price — fundamentally, politically, psychologically — is reflected in the price."
— John Murphy

Universe: Nifty 100
Pull the Nifty 100 constituent list from NSE's website. Before running any strategy, apply a basic liquidity filter — remove stocks with average daily traded value below a minimum threshold. This keeps execution clean and slippage manageable. Use Yahoo Finance's .NS suffix for data

Initial Plan:
SMA base → SMA crossover → EMA test → Volume → RSI → MACD → Breakout → ATR risk → Ranking → Portfolio → Walk-forward

Created the project structure and added basic back testing engine and metrics.

