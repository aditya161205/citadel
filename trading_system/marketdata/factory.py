from .base import DataSource
from ..config import settings


def get_data_source(name: str | None = None) -> DataSource:
    """Return a DataSource by name. None = global default from settings."""
    name = name or settings.DEFAULT_DATA_SOURCE

    if name == "yahoo":
        from .yahoo import YahooDataSource
        return YahooDataSource()

    if name == "csv":
        from .csv_source import CSVDataSource
        return CSVDataSource()

    raise ValueError(
        f"Unknown data source: {name!r}. Available: 'yahoo', 'csv'."
    )
