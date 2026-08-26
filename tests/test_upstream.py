"""Reading the vendor, where the status line is not the answer.

The test that carries the argument is `test_three_recordings_all_return_two_hundred_and_mean
_different_things`. A client that branched on the status line would give one answer to all
three and pass any test that only checked the happy path.
"""

from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from quenchz.upstream import CassetteTransport, Outcome, RawResponse, read

CASSETTES = pathlib.Path(__file__).resolve().parents[1] / "data" / "cassettes"


@pytest.fixture
def tape() -> CassetteTransport:
    return CassetteTransport()


def test_three_recordings_all_return_two_hundred_and_mean_different_things(
    tape: CassetteTransport,
) -> None:
    """The whole argument for not branching on the status line, as one assertion."""
    names = [
        "usd-eur-daily-one-month",
        "usd-eur-daily-one-weekend",
        "format-that-does-not-exist",
    ]
    responses = [tape.fetch(name) for name in names]
    assert {r.status for r in responses} == {200}, "all three really are 200"

    outcomes = [read(r).outcome for r in responses]
    assert outcomes == [Outcome.OBSERVATIONS, Outcome.EMPTY_WINDOW, Outcome.WRONG_FORMAT]
    assert len(set(outcomes)) == 3, "a client reading the status line would say one thing here"


def test_a_two_hundred_carrying_xml_is_not_treated_as_success(tape: CassetteTransport) -> None:
    """The case most likely to be swallowed: ok is true and the body is large.

    The detail is asserted, not just the outcome, and that is deliberate. Removing the XML
    check entirely still yields WRONG_FORMAT, because XML parsed as CSV has no TIME_PERIOD
    column either, so a test that stopped at the outcome would let the check be deleted
    without noticing. The two messages are not interchangeable to a caller: "the vendor
    ignored the format you asked for" tells them to fix the request, and "this is not the
    CSV I expected" tells them nothing they can act on.
    """
    response = tape.fetch("format-that-does-not-exist")
    assert response.status == 200
    assert len(response.body) > 7000
    reading = read(response)
    assert reading.outcome is Outcome.WRONG_FORMAT
    assert reading.observations == {}
    assert "received XML at status 200" in reading.detail
    assert str(len(response.body)) in reading.detail


def test_an_empty_body_is_a_normal_answer_and_not_a_failure(tape: CassetteTransport) -> None:
    reading = read(tape.fetch("usd-eur-daily-one-weekend"))
    assert reading.outcome is Outcome.EMPTY_WINDOW
    assert reading.observations == {}
    assert "window" in reading.detail


def test_the_two_error_shapes_are_different_and_both_are_handled(
    tape: CassetteTransport,
) -> None:
    """404 carries JSON and 400 carries HTML, so one error parser is not enough."""
    not_found = tape.fetch("unknown-series-key")
    assert not_found.status == 404
    assert json.loads(not_found.body)["status"] == 404
    assert read(not_found).outcome is Outcome.UNKNOWN_SERIES

    # The 400 is not recorded as a cassette because its body is a whole HTML error page, so
    # the shape is asserted directly. The point is that it is NOT json, and a client that
    # called .json() on every error body would raise here rather than report anything useful.
    html = RawResponse(status=400, content_type="text/html", body=b'<html lang="en"><head>')
    with pytest.raises(ValueError):
        json.loads(html.body)
    assert read(html).outcome is Outcome.REJECTED_PARAMETERS


def test_placeholder_rows_are_reported_and_not_folded_into_the_observations(
    tape: CassetteTransport,
) -> None:
    """The 62, surfaced rather than silently dropped."""
    reading = read(tape.fetch("usd-eur-daily-full-history"))
    assert len(reading.observations) == 7078
    assert len(reading.placeholders) == 62
    assert not (set(reading.observations) & reading.placeholders), "a day cannot be both"
    assert datetime.date(2012, 5, 1) in reading.placeholders
    assert "62 rows carrying no value" in reading.detail


def test_asking_for_a_recording_that_does_not_exist_fails_loudly(
    tape: CassetteTransport,
) -> None:
    """A transport that returned nothing for a typo would hide it until somebody debugs."""
    with pytest.raises(KeyError, match="no recording named"):
        tape.fetch("usd-eur-daily-full-histroy")


def test_every_recording_classifies_into_a_named_outcome(tape: CassetteTransport) -> None:
    for name in tape.names():
        reading = read(tape.fetch(name))
        assert reading.outcome in set(Outcome), f"{name} fell through to nothing"
        assert reading.detail, f"{name} classified with no explanation"
