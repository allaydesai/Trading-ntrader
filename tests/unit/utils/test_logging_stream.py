"""configure_logging can target a stream other than stdout (the MCP server uses stderr)."""

import io
import logging

import pytest
import structlog

from src.utils.logging import configure_logging

pytestmark = pytest.mark.unit


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


def test_console_output_goes_to_the_given_stream(restore_logging, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    buffer = io.StringIO()
    configure_logging(stream=buffer)
    structlog.get_logger("probe").warning("probe_event")

    assert "probe_event" in buffer.getvalue()
    assert "probe_event" not in capsys.readouterr().out


def test_defaults_to_stdout(restore_logging, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    configure_logging()
    structlog.get_logger("probe").warning("probe_default")

    assert "probe_default" in capsys.readouterr().out
