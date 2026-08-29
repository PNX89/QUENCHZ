"""What arrived, what did not, and why not.

The ECB's response body is a list of observations. It does not say what you asked for, it
does not say how much of that you received, and when a day is missing it does not say
whether the market was closed, whether the rate is not published yet, or whether something
is genuinely wrong. Those three are one silent absence, and telling them apart is the whole
job of this module.

`Coverage` is a REQUIRED field on every tool return THAT CARRIES OBSERVATIONS, with no default
anywhere on it. The qualifier matters and was missing here after the rest of the repository had
been narrowed: `calendar.why` classifies one date and `series.catalogue` lists what exists, and
a certificate on either would be a field with nothing to say, which is worse than no field
because a reader would believe it meant something. That is
deliberate and it is the only mechanism here that actually works: a field with a default is
a field a caller forgets, and a coverage report nobody attached is indistinguishable from a
window with nothing missing.

Source: ECB statistics.
"""

from __future__ import annotations

import datetime
from enum import StrEnum
from typing import assert_never
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from quenchz.target_calendar import PUBLICATION_HOUR_LOCAL, ClosingReason, closing_reason

__all__ = ["PUBLICATION_GRACE", "Absence", "Coverage", "reconstruct"]

# The ECB publishes from Frankfurt. There is no Europe/Frankfurt zone, and Europe/Berlin is
# the same offset with the same transitions, which is what the reference rate's "CET" means
# in summer as well as in winter.
FRANKFURT = ZoneInfo("Europe/Berlin")

# The vendor's own word is "around 16:00 CET", so 16:00 is not a guarantee and must not be
# treated as one. Nothing is called a genuine gap until an hour after the nominal time. The
# cost of the grace is that a real outage is reported as lateness for one hour; the cost of
# omitting it would be a false gap reported every single afternoon that publication ran late.
PUBLICATION_GRACE = datetime.timedelta(hours=1)


class Absence(StrEnum):
    """Why an expected observation is not in the body.

    Four named causes. A fifth this module has not thought of would be counted under
    NO_SUCH_OBSERVATION, and the README says so rather than claiming the list is complete.

    BEFORE_THE_SERIES was the third of the four and it was added late, after a review asked for
    a window in 1990. The euro did not exist in 1990, so nothing was ever due, and the
    certificate answered `expected_observations: 261` with all 261 filed under
    NO_SUCH_OBSERVATION, whose documented meaning is that somebody should know. It is the worst
    available answer: a confident report of 261 missing rates on days when none was owed. It is
    also reachable straight from a tool argument, since `rates.window` parses a caller's dates
    with no window validation.

    "The market was shut" and "this series had not started" are different answers, so this is a
    member of its own rather than being folded into TARGET_CLOSED.
    """

    TARGET_CLOSED = "target_closed"
    NOT_YET_PUBLISHED = "not_yet_published"
    NO_SUCH_OBSERVATION = "no_such_observation"
    BEFORE_THE_SERIES = "before_the_series"


class Coverage(BaseModel):
    """The certificate. Every field is required; not one carries a default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_from: datetime.date
    requested_to: datetime.date
    requested_calendar_days: int
    expected_observations: int
    delivered_observations: int
    delivered_from: datetime.date | None
    delivered_to: datetime.date | None
    absent: dict[Absence, int]
    window_still_open: bool
    body_completed_at: datetime.datetime
    source: str

    @property
    def complete(self) -> bool:
        """True only when every observation the calendar expects actually arrived.

        A window entirely before the series began is complete, because nothing was expected.
        That reads oddly until you consider the alternative, which is calling a window
        incomplete for failing to deliver rates that never existed.
        """
        return self.delivered_observations == self.expected_observations


def _published_yet(day: datetime.date, now: datetime.datetime) -> bool:
    """Whether `day`'s rate should have appeared by `now`.

    The rate for a given day is published at around 16:00 Frankfurt time ON that day. The
    payload's own metadata says 2.15 pm, which is the moment the rate REFERS to and is the
    wrong number to use here; taking it would call every afternoon between 14:15 and 16:00 a
    real gap.
    """
    due = datetime.datetime.combine(
        day, datetime.time(hour=PUBLICATION_HOUR_LOCAL), tzinfo=FRANKFURT
    )
    return now >= due + PUBLICATION_GRACE


def _classify(day: datetime.date, now: datetime.datetime) -> Absence:
    """Why `day` is absent, for every reason the calendar has.

    TWO THINGS WERE WRONG HERE AND THE DOCSTRING WAS THE THIRD. It said "exhaustive by name, so
    a new closing reason breaks the build", and adding a member to `ClosingReason` type checked
    and passed, because `case unreachable` gave mypy a total match and moved the check to
    run time. It also had no arm for `SPECIAL_CLOSURE`, which is a reason `closing_reason`
    genuinely returns, on the dates in `SPECIAL_CLOSURES`.

    That combination was harmless only by luck. `reconstruct` called this exclusively inside
    `elif closing_reason(day) is None`, so the seven member arm was unreachable, the function
    re-read a reason its caller had already read, and a special closure never got here. Anybody
    tidying away the dead arm by calling this unconditionally, which is the obvious tidy, would
    have turned a real ECB closure into an AssertionError.

    So it is called unconditionally now, `SPECIAL_CLOSURE` is named, and `assert_never` replaces
    the catch-all: adding a member is a mypy error at build time, which is what the docstring
    claimed all along.
    """
    match closing_reason(day):
        case (
            ClosingReason.WEEKEND
            | ClosingReason.NEW_YEARS_DAY
            | ClosingReason.GOOD_FRIDAY
            | ClosingReason.EASTER_MONDAY
            | ClosingReason.LABOUR_DAY
            | ClosingReason.CHRISTMAS_DAY
            | ClosingReason.SAINT_STEPHENS_DAY
            | ClosingReason.SPECIAL_CLOSURE
        ):
            return Absence.TARGET_CLOSED
        case None:
            # The calendar says the ECB should have published. Either it has not got round
            # to it yet, or the observation is genuinely missing and somebody should know.
            return (
                Absence.NO_SUCH_OBSERVATION
                if _published_yet(day, now)
                else Absence.NOT_YET_PUBLISHED
            )
        case unhandled:
            assert_never(unhandled)


#: The widest window worth answering, and the number is chosen rather than round.
#:
#: The series starts on 1999-01-04, so its first day to the end of 2040 is 15,338 days: every
#: question anybody can have about it plus fifteen years of headroom.
#:
#: THE FIRST VALUE HERE WAS 13,500 AND IT WAS ELEVEN DAYS TOO SMALL. It was reasoned from
#: "1999 to 2035, about 13,500" without the subtraction being done, and it refused
#: 1999-01-04 to 2035-12-31, which is 13,511 days and a question somebody would actually ask.
#: The test asserting the bound admits every real window is what found it, which is why that
#: test exists beside the one asserting the bound refuses an absurd one: a limit that blocks a
#: genuine question is a worse defect than the walk it was added to prevent.
#:
#: A constant rather than a computation from today, because a limit that moves with the clock
#: is a limit whose failures cannot be reproduced.
MAX_WINDOW_DAYS = 15_338


def reconstruct(
    requested_from: datetime.date,
    requested_to: datetime.date,
    delivered: set[datetime.date],
    now: datetime.datetime,
    series_begins: datetime.date | None = None,
) -> Coverage:
    """Build the certificate for one response.

    `delivered` is the set of dates actually present in the body. `now` is passed in rather
    than read from the clock, because a certificate that consults a global clock cannot be
    tested against the afternoon it is describing.

    `series_begins` is the first date this series ever carried a value. It is passed in for the
    same reason as the clock: the caller knows it and this function cannot. Leaving it None is
    honest rather than convenient, and says only that the lower bound is unknown here.
    """
    if requested_to < requested_from:
        raise ValueError(f"window ends before it starts: {requested_from} to {requested_to}")
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware; a naive clock silently assumes a zone")
    # A THIRD GUARD, AND THE TWO ABOVE IT ARE WHY IT WAS MISSING. They bound the DIRECTION of
    # the window and the awareness of the clock, which reads like the window has been validated.
    # Its SPAN was not bounded at all. Measured before this: 0001-01-01 to 9999-12-30 walked
    # 3,652,058 days in six seconds, from a 185 byte request, on a worker thread holding the
    # GIL, and returned 200. A window ending at date.max raised OverflowError from the final
    # increment instead, which is a crash rather than a refusal.
    stray = sorted(day for day in delivered if not requested_from <= day <= requested_to)
    if stray:
        # RAISE RATHER THAN CLIP, which is the opposite of what looks helpful here. Clipping
        # would make the caller's own clip in `gateway.rates_window` redundant, and a function
        # that silently ignores part of its input is how the certificate below came to be able
        # to say `complete: True` while reporting a gap: `len(delivered)` counted dates the walk
        # never visited. It was not reachable through `rates.window`, whose one caller clips
        # first, so this is a contract nobody was holding rather than a live wire.
        raise ValueError(
            f"{len(stray)} delivered dates fall outside the window this certificate is about, "
            f"the first being {stray[0]}. Clip before asking, because a certificate counting "
            f"observations it did not examine describes a different window from the one it names"
        )
    span = (requested_to - requested_from).days + 1
    if span > MAX_WINDOW_DAYS:
        raise ValueError(
            f"a window of {span:,} days was asked for and {MAX_WINDOW_DAYS:,} is the most this "
            f"answers, which is the series from its first day to well past today. This is walked "
            f"a day at a time, so the cost of answering is the width of the question"
        )

    absent: dict[Absence, int] = dict.fromkeys(Absence, 0)
    expected = 0
    # COUNTED FORWARD FROM THE START RATHER THAN INCREMENTED PAST THE END, and the difference is
    # a real defect rather than a style. `day += one_day` at the top of the last iteration
    # constructs the day AFTER `requested_to`, so a window ending on 9999-12-31 raised
    # OverflowError from the increment itself: a window inside the span limit above, refused by
    # a crash rather than by a check. Building each date from an offset never constructs one
    # outside the window.
    for offset in range(span):
        day = requested_from + datetime.timedelta(days=offset)
        if day in delivered:
            expected += 1
            continue
        if series_begins is not None and day < series_begins:
            # Nothing was ever due here, so it is neither a gap nor a closure, and it does not
            # count towards what was expected.
            absent[Absence.BEFORE_THE_SERIES] += 1
            continue
        # ONE CALL, and this used to ask the calendar twice: once here as `is None` and once
        # again inside `_classify`. That is what made the classifier's seven member arm
        # unreachable, and left a real closing reason missing from it without anything noticing.
        absence = _classify(day, now)
        if absence is not Absence.TARGET_CLOSED:
            expected += 1
        absent[absence] += 1

    return Coverage(
        requested_from=requested_from,
        requested_to=requested_to,
        requested_calendar_days=(requested_to - requested_from).days + 1,
        expected_observations=expected,
        delivered_observations=len(delivered),
        delivered_from=min(delivered) if delivered else None,
        delivered_to=max(delivered) if delivered else None,
        absent=absent,
        window_still_open=absent[Absence.NOT_YET_PUBLISHED] > 0,
        body_completed_at=now,
        source="ECB statistics.",
    )
