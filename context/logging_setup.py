"""Logging for Context.

Output goes to a file as well as stderr. The file matters because the launcher
re-execs itself to install the layer-shell preload, which replaces the process and
drops any shell redirection — so `python3 -m context > log` captures nothing after
the re-exec, and stderr alone is unreliable for anything started from a keybind.

Level comes from CONTEXT_LOG_LEVEL (debug, info, warning, error, critical),
defaulting to info. The log lives at $XDG_STATE_HOME/context/context.log and is
rotated so it cannot grow without bound.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

ENV_LEVEL = "CONTEXT_LOG_LEVEL"
LOGGER_NAME = "context"

MAX_BYTES = 512 * 1024
BACKUPS = 3


def log_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "context"


def log_path() -> Path:
    return log_dir() / "context.log"


def _level() -> int:
    raw = (os.environ.get(ENV_LEVEL) or "info").strip().lower()
    return {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "warn": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }.get(raw, logging.INFO)


def configure() -> logging.Logger:
    """Set up the `context` logger. Safe to call more than once."""
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_context_configured", False):
        return logger

    logger.setLevel(_level())
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(
            log_path(), maxBytes=MAX_BYTES, backupCount=BACKUPS
        )
        rotating.setFormatter(formatter)
        logger.addHandler(rotating)
    except OSError:
        # Logging must never be the reason the launcher fails to start.
        logger.warning("could not open the log file; logging to stderr only")

    logger._context_configured = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """A child logger, e.g. get_logger("app") -> context.app."""
    configure()
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
