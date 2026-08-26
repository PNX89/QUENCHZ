"""The calendar, derived from the vendor's own series rather than asserted from a document.

The interesting test here is the first one. It does not check a handful of dates somebody
chose; it walks every weekday of the whole recorded series and requires that the six rules
in `target_calendar` explain **every** absence with nothing left over. That is a much harder
claim than "these three holidays are handled", and it is the claim that would break first if
the ECB added a closing day.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import pathlib

import pytest

from quenchz.target_calendar import (
    CALENDAR_IN_FORCE_FROM,
    ClosingReason,
    closing_reason,
    easter_sunday,
)

CASSETTES = pathlib.Path(__file__).resolve().parents[1] / "data" / "cassettes"


def observed_days() -> set[datetime.date]:
    body = (CASSETTES / "usd-eur-daily-full-history.body").read_bytes()
    rows = csv.DictReader(io.StringIO(body.decode("utf-8")))
    return {datetime.date.fromisoformat(row["TIME_PERIOD"]) for row in rows}


def test_every_absent_weekday_in_the_whole_series_is_explained() -> None:
    """Nothing left over, across the entire recorded history."""
    observed = observed_days()
    first, last = min(observed), max(observed)

    unexplained: list[datetime.date] = []
    day = first
    while day <= last:
        if day not in observed and closing_reason(day) is None:
            unexplained.append(day)
        day += datetime.timedelta(days=1)

    assert unexplained == [], (
        f"{len(unexplained)} absent days the calendar cannot account for, "
        f"first five: {unexplained[:5]}"
    )


def test_the_rules_are_not_vacuously_satisfied_by_explaining_everything() -> None:
    """The other direction, which is the half that catches a broken guard.

    A `closing_reason` that returned a reason for every date would pass the test above and be
    useless. This requires that every day the ECB DID publish on is a day the calendar says
    is open, which fails immediately if the function over-reaches.
    """
    observed = observed_days()
    wrongly_closed = sorted(d for d in observed if closing_reason(d) is not None)
    assert wrongly_closed == [], (
        f"{len(wrongly_closed)} days carry a published rate but the calendar calls them "
        f"closed, first five: {wrongly_closed[:5]}"
    )


def test_the_calendar_changed_at_the_end_of_2012() -> None:
    """Both sides of the boundary, because this is the fact a reader would assume away."""
    observed = observed_days()

    # Good Friday and 1 May 2012: the ECB published, so the calendar must not claim closure.
    assert datetime.date(2012, 4, 6) in observed
    assert datetime.date(2012, 5, 1) in observed
    assert closing_reason(datetime.date(2012, 4, 6)) is None
    assert closing_reason(datetime.date(2012, 5, 1)) is None

    # Christmas 2012, the first closing day it honours.
    assert datetime.date(2012, 12, 25) not in observed
    assert closing_reason(datetime.date(2012, 12, 25)) is ClosingReason.CHRISTMAS_DAY
    assert datetime.date(2012, 12, 25) == CALENDAR_IN_FORCE_FROM


def test_applying_todays_calendar_to_2010_would_invent_gaps() -> None:
    """What the boundary is worth, stated as a number rather than as a caution.

    If the holiday rules were applied to the whole series instead of from 2013, this many
    days that carry a real published rate would be reported as closed. A coverage
    certificate built that way is wrong on every historical window that spans a holiday.
    """
    observed = observed_days()
    would_be_wrong = [
        d
        for d in observed
        if d < CALENDAR_IN_FORCE_FROM and d.weekday() < 5 and _holiday_ignoring_the_boundary(d)
    ]
    assert len(would_be_wrong) == 62, f"expected 62, got {len(would_be_wrong)}"


def _holiday_ignoring_the_boundary(day: datetime.date) -> bool:
    easter = easter_sunday(day.year)
    return (
        day == easter - datetime.timedelta(days=2)
        or day == easter + datetime.timedelta(days=1)
        or (day.month, day.day) in {(1, 1), (5, 1), (12, 25), (12, 26)}
    )


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2000, datetime.date(2000, 4, 23)),
        (2012, datetime.date(2012, 4, 8)),
        (2024, datetime.date(2024, 3, 31)),
        (2025, datetime.date(2025, 4, 20)),
        (2026, datetime.date(2026, 4, 5)),
        (2038, datetime.date(2038, 4, 25)),
    ],
)
def test_easter_sunday_matches_known_dates(year: int, expected: datetime.date) -> None:
    assert easter_sunday(year) == expected


def test_the_vendor_publishes_no_rate_limit_header_of_any_kind() -> None:
    """The budget claim rests on this, so it is checked against a recording, not asserted.

    Every header of every recorded response is inspected. If the ECB ever starts publishing
    a rate-limit header, a limiter that cannot react stops being a necessity and starts being
    a choice, and this repository would have to say so.
    """
    index = json.loads((CASSETTES / "index.json").read_text())
    offenders = [
        (entry["name"], header)
        for entry in index
        for header in entry["response_headers"]
        if "ratelimit" in header.lower().replace("-", "") or header.lower() == "retry-after"
    ]
    assert offenders == [], f"the vendor did publish a rate-limit header: {offenders}"


def test_a_closed_period_comes_back_as_two_hundred_with_an_empty_body() -> None:
    """The pathology the client is built around, held by a recording."""
    index = {entry["name"]: entry for entry in json.loads((CASSETTES / "index.json").read_text())}
    weekend = index["usd-eur-daily-one-weekend"]
    assert weekend["status"] == 200
    assert weekend["bytes"] == 0
    assert (CASSETTES / "usd-eur-daily-one-weekend.body").read_bytes() == b""
