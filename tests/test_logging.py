"""Where the log goes.

The suite used to write into the user's real log, because `get_logger` runs at
import time and the path was resolved once. That is not cosmetic: the log is
what you read to debug a live session, and fixture names in it are misleading.
"""

from __future__ import annotations

from context.system import logging_setup


def test_the_log_follows_the_state_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    log = logging_setup.get_logger("probe")
    log.warning("hello")

    written = tmp_path / "state" / "context" / "context.log"
    assert written.exists()
    assert "hello" in written.read_text()


def test_a_changed_state_directory_moves_the_log(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "first"))
    logging_setup.get_logger("probe").warning("one")

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "second"))
    logging_setup.get_logger("probe").warning("two")

    first = (tmp_path / "first" / "context" / "context.log").read_text()
    second = (tmp_path / "second" / "context" / "context.log").read_text()
    assert "one" in first and "two" not in first
    assert "two" in second


def test_only_one_file_handler_is_ever_attached(tmp_path, monkeypatch):
    from logging.handlers import RotatingFileHandler

    for name in ("a", "b", "c"):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / name))
        logger = logging_setup.configure()

    files = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(files) == 1


def test_ui_state_round_trips(tmp_path, monkeypatch):
    from context.state import uistate

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert uistate.get("collapsed", False) is False
    uistate.save(collapsed=True)
    assert uistate.get("collapsed") is True


def test_ui_state_merges_rather_than_replaces(tmp_path, monkeypatch):
    from context.state import uistate

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    uistate.save(collapsed=True)
    uistate.save(other="kept")
    assert uistate.get("collapsed") is True
    assert uistate.get("other") == "kept"


def test_broken_ui_state_is_ignored(tmp_path, monkeypatch):
    from context.state import uistate

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = uistate.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert uistate.load() == {}
