"""Tests for the headref log handler, which carries the agent's tool-call trace."""

import logging
from collections.abc import Iterator

import pytest

from app.main import LOG_HANDLER_NAME, configure_logging


@pytest.fixture
def headref_logger() -> Iterator[logging.Logger]:
    """The headref logger, restored to its original state after the test."""
    logger = logging.getLogger("headref")
    level, handlers = logger.level, list(logger.handlers)
    logger.handlers = [h for h in handlers if h.get_name() != LOG_HANDLER_NAME]
    yield logger
    logger.setLevel(level)
    logger.handlers = handlers


def test_info_lines_from_the_agent_are_printed(
    headref_logger: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    """An INFO line from a headref.* logger reaches stderr with its logger name."""
    configure_logging("info")

    logging.getLogger("headref.agent").info("s1_s2 done: question=current_work")

    assert "INFO headref.agent: s1_s2 done: question=current_work" in capsys.readouterr().err


def test_a_second_call_adds_no_second_handler(headref_logger: logging.Logger) -> None:
    """The reloader and tests start the app repeatedly; each line prints once."""
    configure_logging("INFO")
    configure_logging("WARNING")

    named = [h for h in headref_logger.handlers if h.get_name() == LOG_HANDLER_NAME]
    assert len(named) == 1
    assert headref_logger.level == logging.WARNING
