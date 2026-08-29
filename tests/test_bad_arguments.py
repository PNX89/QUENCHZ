"""What a caller can send that is not a question, and what comes back when it does.

Three defects with one shape, found by driving real `tools/call` requests rather than by reading
the code. Each of them reached the wire as HTTP 200 with `isError: true` and the text
`Error executing tool rates.window`, having first printed a four frame anyio traceback into the
server's log:

    start="0000-00-00"          ValueError: year 0 is out of range
    cassette="no-such-thing"    KeyError, which enumerated every recording into the log
    end="9999-12-31"            OverflowError: date value out of range

That is the same failure the module docstring already describes for the budget refusal, in the
three remaining places a caller controls. A server that stack-traces its own expected inputs
buries the one real failure among a thousand ordinary ones, and a caller told only the tool's own
name cannot tell a typo from an outage.

The fourth one is different in kind and is here too: a window of 3.6 million days was ANSWERED,
in six seconds, from a 185 byte request.
"""

from __future__ import annotations

import datetime
import time

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from quenchz.coverage import MAX_WINDOW_DAYS, reconstruct
from quenchz.issuer import Issuer
from quenchz.server import build_server

NOW = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)


def tools() -> dict[str, object]:
    """The registered callables, reached the way the SDK reaches them."""
    server = build_server(Issuer())
    return {tool.name: tool.fn for tool in server._tool_manager.list_tools()}


@pytest.mark.parametrize(
    ("start", "end"),
    [("0000-00-00", "2026-08-29"), ("2026-08-01", "not-a-date"), ("", "2026-08-29")],
)
def test_a_date_that_is_not_a_date_is_an_answer_rather_than_a_traceback(
    start: str, end: str
) -> None:
    with pytest.raises(ToolError) as refusal:
        tools()["rates.window"]("usd-eur-daily-one-month", start, end)  # type: ignore[operator]
    assert "YYYY-MM-DD" in str(refusal.value)
    assert repr(start) in str(refusal.value), (
        "the refusal does not quote what was sent, so the caller cannot see which of its two "
        "dates this is about"
    )


def test_an_unrecorded_cassette_is_refused_without_listing_the_recorded_ones() -> None:
    """The half of this that is not about tidiness.

    The transport's `KeyError` names every recording it holds, which is right for a developer
    reading a stack trace in their own terminal and wrong at the wire. Which names exist is
    precisely what the concealment layer withholds from a caller with no scope for the
    catalogue, and an error message is a poor place to give it back.
    """
    with pytest.raises(ToolError) as refusal:
        tools()["rates.window"]("no-such-recording", "2026-08-01", "2026-08-29")  # type: ignore[operator]
    message = str(refusal.value)
    assert "no-such-recording" in message
    assert "usd-eur-daily-one-month" not in message, (
        "the refusal lists the recordings that do exist, which is the catalogue this server "
        "conceals from callers without the scope for it"
    )


def test_a_day_that_is_not_a_day_is_refused_by_the_calendar_tool_too() -> None:
    """The same defect lived in `calendar.why`, and fixing one tool would have left it there."""
    with pytest.raises(ToolError) as refusal:
        tools()["calendar.why"]("2026-13-45")  # type: ignore[operator]
    assert "YYYY-MM-DD" in str(refusal.value)


def test_a_window_wider_than_the_series_is_refused_rather_than_walked() -> None:
    """The measurement, kept as a test.

    Six seconds of a worker thread, from 185 bytes, is not a crash and not an error: it is a
    successful answer that costs more than the question. The walk is a day at a time, so the
    price of answering is the width of what was asked.
    """
    started = time.perf_counter()
    with pytest.raises(ValueError, match="days was asked for"):
        reconstruct(datetime.date(1, 1, 1), datetime.date(9999, 12, 30), set(), NOW)
    assert time.perf_counter() - started < 1.0, (
        "the refusal itself took a second, which means it is being decided after the walk "
        "rather than before it"
    )


def test_the_bound_admits_every_window_a_caller_could_mean() -> None:
    """A limit that refuses a real question is a worse defect than the one it fixes."""
    first_day = datetime.date(1999, 1, 4)
    assert reconstruct(first_day, datetime.date(2035, 12, 31), set(), NOW)
    assert (datetime.date(2035, 12, 31) - first_day).days + 1 < MAX_WINDOW_DAYS


def test_a_window_ending_on_the_last_representable_day_is_answered() -> None:
    """This raised OverflowError, and the span limit above does not fix it.

    The window is two days wide, so no bound rejects it. The crash came from the loop itself:
    incrementing at the top of the final iteration constructed the day AFTER the last one.
    """
    certificate = reconstruct(datetime.date(9999, 12, 30), datetime.date(9999, 12, 31), set(), NOW)
    assert certificate.requested_calendar_days == 2


def test_a_delivered_date_outside_the_window_is_refused_rather_than_counted() -> None:
    """The certificate could say complete while reporting a gap.

    `len(delivered)` counted dates the walk never visited, so a single observation from three
    days before the window satisfied a window that also reported a genuine absence. Not
    reachable through `rates.window`, whose one caller clips first: a contract nobody held.
    """
    with pytest.raises(ValueError, match="fall outside the window"):
        reconstruct(
            datetime.date(2026, 8, 24),
            datetime.date(2026, 8, 24),
            {datetime.date(2026, 8, 21)},
            NOW,
        )


def test_an_ordinary_window_still_answers() -> None:
    """Four guards now stand between a caller and this function. None of them may block a month."""
    certificate = reconstruct(
        datetime.date(2026, 8, 1),
        datetime.date(2026, 8, 31),
        {datetime.date(2026, 8, 3)},
        NOW,
    )
    assert certificate.requested_calendar_days == 31
    assert certificate.delivered_observations == 1
