import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import settings

_configured = False


def get_logger(name: str = "trading_system") -> logging.Logger:
    """Return a logger with console + rotating file output.

    File logs go to  logs/<name>.log  (auto-created, gitignored).
    """
    global _configured
    logger = logging.getLogger(name)

    if not _configured:
        logger.setLevel(logging.DEBUG)
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Console handler (INFO+).
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        # File handler (DEBUG+, rotating 5 MB × 3 backups).
        log_dir = settings.LOGS_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / f"{name}.log", maxBytes=5_000_000, backupCount=3,
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        _configured = True

    return logger
