"""Unified engine logging: structured logs to state/engine.log + console.

- state/engine.log: full log (INFO and above), rotating.
- state/error.log: errors only (ERROR and above), for TUI "Errors only" view.
Used by main, bot_runtime, ollama_client. TUI "Show logs" reads these files.
Works in both foreground and daemon mode.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_LOGGER: logging.Logger | None = None


class _ErrorOnlyFilter(logging.Filter):
    """Only allow ERROR and CRITICAL records."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.ERROR


def _ensure_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    import config

    logger = logging.getLogger("niffi")
    logger.setLevel(getattr(config, "ENGINE_LOG_LEVEL", "INFO"))
    if logger.handlers:
        return logger

    state_dir = getattr(config, "STATE_DIR", "state")
    log_path = getattr(config, "ENGINE_LOG_PATH", os.path.join(state_dir, "engine.log"))
    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = RotatingFileHandler(
        log_path,
        maxBytes=getattr(config, "ENGINE_LOG_MAX_BYTES", 5 * 1024 * 1024),
        backupCount=getattr(config, "ENGINE_LOG_BACKUP_COUNT", 2),
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    error_log_path = getattr(
        config, "ENGINE_ERROR_LOG_PATH", os.path.join(state_dir, "error.log")
    )
    if error_log_path:
        efh = RotatingFileHandler(
            error_log_path,
            maxBytes=getattr(config, "ENGINE_LOG_MAX_BYTES", 2 * 1024 * 1024),
            backupCount=getattr(config, "ENGINE_LOG_BACKUP_COUNT", 2),
            encoding="utf-8",
        )
        efh.setFormatter(fmt)
        efh.addFilter(_ErrorOnlyFilter())
        logger.addHandler(efh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    _LOGGER = logger
    return logger


def log_debug(msg: str) -> None:
    _ensure_logger().debug(msg)


def log_info(msg: str) -> None:
    _ensure_logger().info(msg)


def log_warn(msg: str) -> None:
    _ensure_logger().warning(msg)


def log_error(msg: str, exc_info: bool = False) -> None:
    _ensure_logger().error(msg, exc_info=exc_info)


def get_logger() -> logging.Logger:
    return _ensure_logger()
