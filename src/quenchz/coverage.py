"""What arrived, what did not, and why not.

The ECB's response body is a list of observations. It does not say what you asked for, it
does not say how much of that you received, and when a day is missing it does not say
whether the market was closed, whether the rate is not published yet, or whether something
is genuinely wrong. Those three are one silent absence, and telling them apart is the whole
job of this module.

`Coverage` is a REQUIRED field on every tool return, with no default anywhere on it. That is
deliberate and it is the only mechanism here that actually works: a field with a default is
a field a caller forgets, and a coverage report nobody attached is indistinguishable from a
window with nothing missing.

Source: ECB statistics.
"""

from __future__ import annotations

import datetime
from enum import StrEnum
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
    """Why `day` is absent. Exhaustive by name, so a new closing reason breaks the build."""
    reason = closing_reason(day)
    match reason:
        case (
            ClosingReason.WEEKEND
            | ClosingReason.NEW_YEARS_DAY
            | ClosingReason.GOOD_FRIDAY
            | ClosingReason.EASTER_MONDAY
            | ClosingReason.LABOUR_DAY
            | ClosingReason.CHRISTMAS_DAY
            | ClosingReason.SAINT_STEPHENS_DAY
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
        case unreachable:  # pragma: no cover
            # Not a catch-all that quietly does something sensible. A closing reason added to
            # the enum without being taught to this match arrives here, and a guard that
            # silently passed it through would be dead code the day it was written.
            raise AssertionError(f"closing reason not handled by this match: {unreachable!r}")


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

    absent: dict[Absence, int] = dict.fromkeys(Absence, 0)
    expected = 0
    day = requested_from
    while day <= requested_to:
        if day in delivered:
            expected += 1
        elif series_begins is not None and day < series_begins:
            # Nothing was ever due here, so it is neither a gap nor a closure, and it does not
            # count towards what was expected.
            absent[Absence.BEFORE_THE_SERIES] += 1
        elif closing_reason(day) is None:
            expected += 1
            absent[_classify(day, now)] += 1
        else:
            absent[Absence.TARGET_CLOSED] += 1
        day += datetime.timedelta(days=1)

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
