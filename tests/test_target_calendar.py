"""The calendar, derived from the vendor's own series rather than asserted from a document.

The load-bearing test walks every day of the whole recorded history and requires that the
rules explain **every** absent value with nothing left over, and, in the other direction,
that every day carrying a real value is one the rules call open. Either half alone is easy to
satisfy with a broken implementation.

The subtlety that cost the most to find is what "absent" means. Until May 2012 the ECB
emitted a row for each closing day with an empty value and `OBS_STATUS` of `H`; afterwards it
emits no row. Counting ROWS and counting VALUES therefore disagree by 62 across this series,
and only counting values is right in both eras.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import pathlib

import pytest

from quenchz.target_calendar import (
    ANNUAL_CLOSURES_FROM,
    PLACEHOLDER_ROWS_END,
    SERIES_BEGINS,
    SPECIAL_CLOSURES,
    ClosingReason,
    closing_reason,
    easter_sunday,
)

CASSETTES = pathlib.Path(__file__).resolve().parents[1] / "data" / "cassettes"
REPO_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "quenchz"


def _rows() -> list[dict[str, str]]:
    body = (CASSETTES / "usd-eur-daily-full-history.body").read_bytes()
    return list(csv.DictReader(io.StringIO(body.decode("utf-8"))))


def days_with_a_value() -> set[datetime.date]:
    return {datetime.date.fromisoformat(r["TIME_PERIOD"]) for r in _rows() if r["OBS_VALUE"]}


def days_with_a_row() -> set[datetime.date]:
    return {datetime.date.fromisoformat(r["TIME_PERIOD"]) for r in _rows()}


def test_every_day_without_a_value_in_the_whole_series_is_explained() -> None:
    """Nothing left over, across twenty-seven years."""
    valued = days_with_a_value()
    first, last = min(days_with_a_row()), max(days_with_a_row())

    unexplained: list[datetime.date] = []
    day = first
    while day <= last:
        if day not in valued and closing_reason(day) is None:
            unexplained.append(day)
        day += datetime.timedelta(days=1)

    assert unexplained == [], (
        f"{len(unexplained)} days with no rate that the calendar cannot account for, "
        f"first five: {unexplained[:5]}"
    )


def test_the_rules_are_not_vacuously_satisfied_by_explaining_everything() -> None:
    """The other direction, which is the half that catches a guard that over-reaches."""
    wrongly_closed = sorted(d for d in days_with_a_value() if closing_reason(d) is not None)
    assert wrongly_closed == [], (
        f"{len(wrongly_closed)} days carry a real rate but the calendar calls them closed, "
        f"first five: {wrongly_closed[:5]}"
    )


def test_counting_rows_and_counting_values_disagree_by_sixty_two() -> None:
    """The defect a row-counting client has, stated as a number.

    Every one of the sixty-two is a placeholder: a row that exists, carries no value, and is
    flagged `H`. A client that counted rows would have reported a complete year for 2005 and
    handed its caller six days that contain nothing.
    """
    rows = _rows()
    placeholders = [r for r in rows if not r["OBS_VALUE"]]
    assert len(placeholders) == 62
    assert {r["OBS_STATUS"] for r in placeholders} == {"H"}
    assert len(days_with_a_row()) - len(days_with_a_value()) == 62


def test_the_placeholder_encoding_stopped_in_may_2012() -> None:
    """Both sides of the encoding change, and it is not a calendar change."""
    rows = _rows()
    last_placeholder = max(r["TIME_PERIOD"] for r in rows if r["OBS_STATUS"] == "H")
    assert datetime.date.fromisoformat(last_placeholder) == PLACEHOLDER_ROWS_END

    # Before: a row exists for the closure. After: no row exists, and the calendar says the
    # same thing about both.
    assert datetime.date(2012, 5, 1) in days_with_a_row()
    assert datetime.date(2012, 5, 1) not in days_with_a_value()
    assert closing_reason(datetime.date(2012, 5, 1)) is ClosingReason.LABOUR_DAY

    assert datetime.date(2012, 12, 25) not in days_with_a_row()
    assert closing_reason(datetime.date(2012, 12, 25)) is ClosingReason.CHRISTMAS_DAY


def test_the_two_special_closures_are_real_and_flagged_by_the_vendor() -> None:
    """Two one-off closures in twenty-seven years, both confirmed in the payload."""
    flagged = {
        datetime.date.fromisoformat(r["TIME_PERIOD"]) for r in _rows() if r["OBS_STATUS"] == "H"
    }
    for day in SPECIAL_CLOSURES:
        assert day in flagged, f"{day} is claimed special but the vendor does not flag it"
        assert closing_reason(day) is ClosingReason.SPECIAL_CLOSURE
        assert day.weekday() < 5, "a weekend needs no special rule"

    # And no annual rule produces either of them, which is why they have to be listed.
    for day in SPECIAL_CLOSURES:
        easter = easter_sunday(day.year)
        assert day != easter - datetime.timedelta(days=2)
        assert day != easter + datetime.timedelta(days=1)
        assert (day.month, day.day) not in {(1, 1), (5, 1), (12, 25), (12, 26)}


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
    """The budget claim rests on this, so it is checked against a recording, not asserted."""
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


def test_nineteen_ninety_nine_had_no_harmonised_closing_calendar() -> None:
    """Two days in seven thousand, and the reason the vacuity test earns its place.

    TARGET's first year ran on national calendars. Good Friday and Easter Monday 1999 carry
    real rates and no later year does, which is why the annual rules start in 2000 rather
    than at the beginning of the series.
    """
    valued = days_with_a_value()
    assert datetime.date(1999, 4, 2) in valued, "Good Friday 1999 carries a rate"
    assert datetime.date(1999, 4, 5) in valued, "Easter Monday 1999 carries a rate"
    assert closing_reason(datetime.date(1999, 4, 2)) is None
    assert closing_reason(datetime.date(1999, 4, 5)) is None

    # And every later Easter is a closure, so this really is an era and not a rule change.
    assert closing_reason(datetime.date(2000, 4, 21)) is ClosingReason.GOOD_FRIDAY
    assert datetime.date(2000, 1, 1) == ANNUAL_CLOSURES_FROM


def test_the_numbers_in_the_docstrings_are_recomputed_rather_than_remembered() -> None:
    """The two figures `easter_sunday`'s docstring quotes, derived from the committed series.

    It used to say "the two most common absences in the series, at fourteen each" and both
    halves were wrong. Fourteen was left over from counting missing ROWS before the 2012
    encoding change and survived the correction that doubled it, and weekends are and always
    were the most common absence by two orders of magnitude. A docstring number that nothing
    recomputes goes stale the first time the code under it changes.
    """
    valued = days_with_a_value()
    first, last = min(days_with_a_row()), max(days_with_a_row())

    moving, weekends = 0, 0
    day = first
    while day <= last:
        if day not in valued:
            if day.weekday() >= 5:
                weekends += 1
            elif closing_reason(day) in {ClosingReason.GOOD_FRIDAY, ClosingReason.EASTER_MONDAY}:
                moving += 1
        day += datetime.timedelta(days=1)

    assert moving == 54, f"27 Good Fridays and 27 Easter Mondays, got {moving} between them"
    assert weekends == 2884
    assert weekends > moving, "weekends are the most common absence, and always were"

    source = (REPO_SOURCE / "target_calendar.py").read_text()
    assert "27 absences each" in source
    assert "2,884" in source

    # The old figure is allowed to appear, but only in the sentence explaining that it was
    # wrong. Banning the words outright fails against the correction itself, which is the
    # vocabulary-versus-claim distinction this portfolio has already been caught by once.
    for sentence in source.split("."):
        if "fourteen each" in sentence:
            assert "wrong" in sentence.lower() or "said" in sentence.lower(), (
                f"the superseded figure is being stated as current: {sentence.strip()!r}"
            )


def test_the_series_start_is_the_first_day_that_carries_a_value() -> None:
    """SERIES_BEGINS is a fact about the data, so it is checked against the data."""
    assert min(days_with_a_value()) == SERIES_BEGINS
    assert closing_reason(SERIES_BEGINS) is None, "the first day is an open day"
