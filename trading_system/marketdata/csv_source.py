"""
CSV data source — documented stub.

To implement: place OHLCV files in a data/ folder at the repo root:

    data/
      RELIANCE.NS.csv
      TCS.NS.csv
      ...

Each CSV should have columns:  Date, Open, High, Low, Close, Volume
with Date parseable by pandas (e.g. 2024-01-15).

Then fill in get_history / get_recent below using pd.read_csv.
"""

from .base import DataSource


class CSVDataSource(DataSource):

    def get_history(self, symbols, start, end, interval="1d"):
        raise NotImplementedError(
            "CSVDataSource is not yet implemented. See the docstring at the top "
            "of csv_source.py for the expected file layout."
        )

    def get_recent(self, symbols, interval="1d", warmup=200):
        raise NotImplementedError(
            "CSVDataSource is not yet implemented. See the docstring at the top "
            "of csv_source.py for the expected file layout."
        )
