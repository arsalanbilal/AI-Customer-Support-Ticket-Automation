"""
Centralized logging setup. Writes to both console and a rotating log file
under LOG_DIR so failures (LLM errors, SMTP errors, Airtable errors) are
auditable after the fact.
"""
import logging
import logging.handlers
from pathlib import Path

from config import settings

_LOGGER_NAME = "support_automation"
_configured = False


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)

    if not _configured:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        log_path = Path(settings.log_dir) / "app.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.INFO)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)
        console_handler.setLevel(logging.INFO)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        logger.propagate = False
        _configured = True

    if name == _LOGGER_NAME:
        return logger
    return logger.getChild(name)
