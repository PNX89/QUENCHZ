"""The certificate, tested against the case where each of its four causes is the answer.

The tests that matter here are the ones that would pass on a broken implementation if they
were written lazily. Requesting twelve days and receiving eight looks like a third of the
data missing, and reporting it that way would be wrong: four of those days are closures and
the response is complete. So there is a test for the complete case, a test for the genuinely
incomplete case, and a test proving that the number the payload itself offers is the wrong
one to use.
"""

from __future__ import annotations

import ast
import csv
import datetime
import inspect
import io
import pathlib
import textwrap
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from quenchz import coverage as coverage_module
from quenchz.coverage import FRANKFURT, Absence, Coverage, reconstruct

CASSETTES = pathlib.Path(__file__).resolve().parents[1] / "data" / "cassettes"
LONG_AFTER = datetime.datetime(2026, 8, 26, 12, 0, tzinfo=datetime.UTC)


def dates_in(cassette: str) -> set[datetime.date]:
    body = (CASSETTES / f"{cassette}.body").read_bytes()
    if not body:
        return set()
    rows = csv.DictReader(io.StringIO(body.decode("utf-8")))
    return {datetime.date.fromisoformat(row["TIME_PERIOD"]) for row in rows}


def names_matched_in_case_patterns(func: Callable[..., object]) -> set[str]:
    """The enum member names an exhaustive `match` actually tests a value against.

    `inspect.getsource(func)` used to be searched as text for `f"ClosingReason.{member.name}"`,
    which also matches the function's own docstring, an inline comment, or a `#:` note, none of
    which stop the interpreter falling through: naming a member only in a comment satisfied the
    old check while leaving it genuinely unhandled. This parses the source instead and reads only
    the `case` patterns of the one `match` statement, which is the part the interpreter tests a
    value against, walking `MatchOr` for a `case A | B | C:` union and reading the attribute off
    each `MatchValue`, which is what a dotted name like `ClosingReason.WEEKEND` compiles to.
    """
    source = textwrap.dedent(inspect.getsource(func))
    matches = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Match)]
    assert len(matches) == 1, f"{func.__qualname__} has {len(matches)} match statements, not 1"

    matched: set[str] = set()

    def collect(pattern: ast.pattern) -> None:
        if isinstance(pattern, ast.MatchOr):
            for alternative in pattern.patterns:
                collect(alternative)
        elif isinstance(pattern, ast.MatchValue) and isinstance(pattern.value, ast.Attribute):
            matched.add(pattern.value.attr)

    for case in matches[0].cases:
        collect(case.pattern)
    return matched


def test_four_days_missing_across_easter_is_a_complete_response() -> None:
    """The headline case, and the one a careless implementation gets wrong."""
    got = reconstruct(
        datetime.date(2026, 3, 30),
        datetime.date(2026, 4, 10),
        dates_in("usd-eur-daily-easter-2026"),
        LONG_AFTER,
    )
    assert got.requested_calendar_days == 12
    assert got.delivered_observations == 8
    assert got.expected_observations == 8
    assert got.absent[Absence.TARGET_CLOSED] == 4
    assert got.absent[Absence.NO_SUCH_OBSERVATION] == 0
    assert got.complete is True, "four closures are not four gaps"


def test_a_weekend_only_window_is_complete_despite_an_empty_body() -> None:
    """HTTP 200 and zero bytes, which is the response the client is built around."""
    got = reconstruct(
        datetime.date(2026, 8, 22),
        datetime.date(2026, 8, 23),
        dates_in("usd-eur-daily-one-weekend"),
        LONG_AFTER,
    )
    assert got.delivered_observations == 0
    assert got.delivered_from is None
    assert got.absent[Absence.TARGET_CLOSED] == 2
    assert got.complete is True


def test_a_real_gap_is_reported_as_one() -> None:
    """Take a day out of a delivered set and the certificate must notice."""
    delivered = dates_in("usd-eur-daily-easter-2026") - {datetime.date(2026, 4, 8)}
    got = reconstruct(datetime.date(2026, 3, 30), datetime.date(2026, 4, 10), delivered, LONG_AFTER)
    assert got.absent[Absence.NO_SUCH_OBSERVATION] == 1
    assert got.complete is False
    assert got.window_still_open is False, "a gap in the past is not an open window"


def test_the_afternoon_before_publication_is_lateness_and_not_a_gap() -> None:
    """15:00 in Frankfurt on a business day, with today's rate not yet out."""
    monday = datetime.date(2026, 8, 24)
    got = reconstruct(
        monday, monday, set(), datetime.datetime(2026, 8, 24, 15, 0, tzinfo=FRANKFURT)
    )
    assert got.absent[Absence.NOT_YET_PUBLISHED] == 1
    assert got.absent[Absence.NO_SUCH_OBSERVATION] == 0
    assert got.window_still_open is True


def test_the_time_the_payload_reports_is_the_wrong_one_to_use() -> None:
    """The trap, made into a number rather than a caution.

    Every observation's own metadata says the rate refers to 2.15 pm CET. Publication is
    around 16:00. At 14:30 Frankfurt time the rate is legitimately not out yet, so a client
    that trusted the payload's time would call it a genuine gap. This asserts the correct
    verdict at that moment, which is exactly the verdict the payload argues against.
    """
    monday = datetime.date(2026, 8, 24)
    at_1430 = datetime.datetime(2026, 8, 24, 14, 30, tzinfo=FRANKFURT)
    got = reconstruct(monday, monday, set(), at_1430)
    assert got.absent[Absence.NOT_YET_PUBLISHED] == 1
    assert got.absent[Absence.NO_SUCH_OBSERVATION] == 0

    # And after the grace has run out on the same day, the same absence is a real gap.
    at_1730 = datetime.datetime(2026, 8, 24, 17, 30, tzinfo=FRANKFURT)
    late = reconstruct(monday, monday, set(), at_1730)
    assert late.absent[Absence.NO_SUCH_OBSERVATION] == 1
    assert late.absent[Absence.NOT_YET_PUBLISHED] == 0


def test_the_certificate_cannot_be_built_with_anything_left_out() -> None:
    """No field carries a default, which is the only thing making it hard to forget."""
    with pytest.raises(ValidationError):
        Coverage()  # type: ignore[call-arg]
    for name, field in Coverage.model_fields.items():
        assert field.is_required(), f"{name} has a default, so a caller can omit it"


def test_the_certificate_refuses_a_naive_clock() -> None:
    with pytest.raises(ValueError, match="timezone aware"):
        reconstruct(
            datetime.date(2026, 8, 24),
            datetime.date(2026, 8, 24),
            set(),
            datetime.datetime(2026, 8, 24, 15, 0),
        )


def test_the_certificate_refuses_a_window_that_ends_before_it_starts() -> None:
    with pytest.raises(ValueError, match="ends before it starts"):
        reconstruct(datetime.date(2026, 8, 24), datetime.date(2026, 8, 1), set(), LONG_AFTER)


def test_an_unhandled_closing_reason_raises_rather_than_passing_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is live, not decoration.

    A catch-all that quietly returned something sensible would make the exhaustive match dead
    code the day it was written. This forces an unknown reason through it and requires a
    raise, which is what proves the last arm is reachable and doing work.

    The arm is `assert_never` now rather than a hand-written raise, so the message is the one
    the standard library writes. That swap is the point: the old form gave mypy a total match,
    which is why adding a member to `ClosingReason` used to type check and pass.
    """
    monkeypatch.setattr(coverage_module, "closing_reason", lambda _day: "bank-holiday-monday")
    with pytest.raises(AssertionError, match="Expected code to be unreachable"):
        coverage_module._classify(datetime.date(2026, 8, 24), LONG_AFTER)


def test_every_closing_reason_is_named_in_the_match_that_claims_to_be_exhaustive() -> None:
    """The half the run-time test cannot reach, and the half that was actually broken.

    `ClosingReason.SPECIAL_CLOSURE` was a member `closing_reason` genuinely returns and the
    match had no arm for it. Nothing failed, because `reconstruct` only ever called `_classify`
    when the reason was None, so the seven member arm was unreachable and a special closure
    never arrived. That is not a test passing, it is a test that could not run.

    Read from the source rather than by calling, because a member added and not handled is a
    static fact. mypy now refuses it too, via `assert_never`, and this says so in the suite so
    that a reader of the tests can see the rule without running the type checker.

    Read from the `case` patterns specifically, not from the function's text: a substring search
    over the whole source is satisfied by a member named in a comment or in this very docstring,
    which is not an arm and does not stop a real one falling through.
    """
    from quenchz.target_calendar import ClosingReason

    matched = names_matched_in_case_patterns(coverage_module._classify)
    unnamed = [member.name for member in ClosingReason if member.name not in matched]
    assert unnamed == [], (
        f"these closing reasons have no arm in the match: {unnamed}. The match is what decides "
        f"whether a day counts towards what was expected, so a missing one is a miscount rather "
        f"than a crash"
    )


def test_every_absence_reason_is_always_present_in_the_report() -> None:
    """A missing key and a zero are different things to a caller, so all four are always there.

    `set(got.absent) == set(Absence)` used to be the assertion, and it cannot fail: `reconstruct`
    builds `absent` as `dict.fromkeys(Absence, 0)`, so its keys are `Absence`'s members for any
    `Absence` at all, including one with a single member or none. Checked directly: the identity
    held with the real four-member enum, with a one-member stand-in, and with an empty one.
    Asserted here against the certificate's own serialised names instead, which is a literal
    that a renamed or deleted member actually disagrees with.
    """
    got = reconstruct(
        datetime.date(2026, 8, 24),
        datetime.date(2026, 8, 24),
        {datetime.date(2026, 8, 24)},
        LONG_AFTER,
    )
    assert set(got.model_dump(mode="json")["absent"]) == {
        "target_closed",
        "not_yet_published",
        "no_such_observation",
        "before_the_series",
    }


def test_the_grace_window_exists_because_the_vendor_said_around() -> None:
    """16:30, past the nominal hour and inside the grace.

    Without this the grace could be deleted and every other test would still pass, which
    would make it decoration. The vendor's word is "around 16:00", so a rate that has not
    appeared at half past is late rather than missing, and only becomes a gap at 17:00.
    """
    monday = datetime.date(2026, 8, 24)
    inside = reconstruct(
        monday, monday, set(), datetime.datetime(2026, 8, 24, 16, 30, tzinfo=FRANKFURT)
    )
    assert inside.absent[Absence.NOT_YET_PUBLISHED] == 1, "inside the grace, this is lateness"

    outside = reconstruct(
        monday, monday, set(), datetime.datetime(2026, 8, 24, 17, 1, tzinfo=FRANKFURT)
    )
    assert outside.absent[Absence.NO_SUCH_OBSERVATION] == 1, "past the grace, this is a gap"


def test_a_window_before_the_series_began_is_not_hundreds_of_gaps() -> None:
    """Nothing was due in 1990, so nothing is missing.

    Found by a review that asked for a window nine years before the euro existed. The
    certificate answered `expected_observations: 261` with all 261 filed under
    NO_SUCH_OBSERVATION, whose documented meaning is that somebody should know. That is the
    worst available answer: a confident report of 261 absent rates on days when none was owed.
    """
    got = reconstruct(
        datetime.date(1990, 1, 1),
        datetime.date(1990, 12, 31),
        set(),
        LONG_AFTER,
        series_begins=datetime.date(1999, 1, 4),
    )
    assert got.expected_observations == 0, "nothing was ever due here"
    assert got.absent[Absence.NO_SUCH_OBSERVATION] == 0
    assert got.absent[Absence.TARGET_CLOSED] == 0, "a closure implies something to close"
    assert got.absent[Absence.BEFORE_THE_SERIES] == 365
    assert got.complete is True


def test_a_window_straddling_the_start_reports_both_sides() -> None:
    """The harder case, because the spurious gaps used to hide among real ones.

    A window from before the series into it must not lump the two together: the days before
    the start were never owed, and the days after are subject to the usual three causes.
    """
    begins = datetime.date(1999, 1, 4)
    got = reconstruct(
        datetime.date(1998, 12, 1),
        datetime.date(1999, 2, 1),
        set(),
        LONG_AFTER,
        series_begins=begins,
    )
    assert got.absent[Absence.BEFORE_THE_SERIES] == 34
    assert got.expected_observations == 21, "only the days from the start onwards were due"
    assert got.absent[Absence.NO_SUCH_OBSERVATION] == 21, "and those really are missing here"


def test_an_unknown_lower_bound_is_left_unknown_rather_than_guessed() -> None:
    """Omitting series_begins must not silently invent one.

    The parameter is optional because a caller may genuinely not know the first date. What it
    must never do is default to something plausible, which would make the certificate wrong in
    a way nobody could see.
    """
    got = reconstruct(datetime.date(1990, 1, 1), datetime.date(1990, 12, 31), set(), LONG_AFTER)
    assert got.absent[Absence.BEFORE_THE_SERIES] == 0
    assert got.expected_observations == 261, "with no lower bound, every weekday was expected"
