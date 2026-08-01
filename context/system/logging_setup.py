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

import functools
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
    raw = os.environ.get(ENV_LEVEL)
    if not raw:
        # `settings` logs through this module, so it is imported lazily and its
        # own import is tolerated failing: during it, `current` does not exist
        # yet and the default level is the right answer anyway.
        try:
            from context.state import settings

            raw = settings.current().log_level
        except (ImportError, AttributeError):
            raw = "info"
    raw = raw.strip().lower()
    return {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "warn": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }.get(raw, logging.INFO)


def _formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _attach_file_handler(logger: logging.Logger, path: Path) -> None:
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            logger.removeHandler(handler)
            handler.close()
    # Recorded even when opening fails, so a broken path is not retried on
    # every call.
    logger._context_log_path = path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS)
    except OSError:
        # Logging must never be the reason the launcher fails to start.
        logger.warning("could not open the log file; logging to stderr only")
        return
    rotating.setFormatter(_formatter())
    logger.addHandler(rotating)


def configure() -> logging.Logger:
    """Set up the `context` logger. Safe to call more than once.

    The file handler follows `XDG_STATE_HOME` and is re-opened when it changes.
    It has to be: `get_logger` runs at module import, which under pytest is
    during collection — before the fixture that redirects the state directory.
    Resolving the path once meant the suite wrote its fixture names into the
    real log, which is actively misleading when reading it to debug a session.
    """
    logger = logging.getLogger(LOGGER_NAME)

    if not getattr(logger, "_context_configured", False):
        logger.setLevel(_level())
        logger.propagate = False
        stream = logging.StreamHandler()
        stream.setFormatter(_formatter())
        logger.addHandler(stream)
        logger._context_configured = True
        logger._context_log_path = None

    wanted = log_path()
    if logger._context_log_path != wanted:
        _attach_file_handler(logger, wanted)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """A child logger, e.g. get_logger("app") -> context.app."""
    configure()
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def traced(logger: logging.Logger, level: int = logging.DEBUG):
    """Log a function's arguments, result, and any exception.

    Most of what is worth logging is "this was called with X and returned Y",
    which is noise at info level but exactly what is needed when something
    misbehaves. Rather than writing that by hand in every method, decorate:

        @traced(log)
        def preselect(self, direction): ...

    Exceptions are logged at error level and re-raised, so tracing never
    changes behaviour.
    """

    def decorate(func):
        name = func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if logger.isEnabledFor(level):
                # Drop `self` so the line is about the call, not the instance.
                shown = args[1:] if args and hasattr(args[0], func.__name__) else args
                logger.log(level, "%s(%s)", name, _describe(shown, kwargs))
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                logger.error("%s failed: %s", name, exc)
                raise
            if logger.isEnabledFor(level):
                logger.log(level, "%s -> %s", name, _short(result))
            return result

        return wrapper

    return decorate


def _describe(args, kwargs) -> str:
    parts = [_short(a) for a in args]
    parts += [f"{k}={_short(v)}" for k, v in kwargs.items()]
    return ", ".join(parts)


def _short(value, limit: int = 80) -> str:
    """A compact repr, so a long list never floods the log."""
    if isinstance(value, (list, tuple, set, dict)) and len(value) > 4:
        return f"{type(value).__name__}[{len(value)}]"
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
