"""When the ECB publishes a euro reference rate, and when a missing day is not a gap.

Everything in this module was derived from the vendor's own series rather than from a
document, and then checked against the vendor's documentation. The derivation is in
`tests/test_target_calendar.py`: across 7,140 rows carrying 7,078 observations, from 1999-01-04
to 2026-08-25, **every** absent weekday is explained by the six annual rules below plus the
table of two special closures, and none is left over. The two numbers are different and this
file is where the difference is explained, so calling 7,140 observations here, as it did, was
wrong in the sentence introducing the point.

Two facts here are not obvious and both cost a naive implementation real accuracy.

THE ENCODING CHANGED, NOT THE CALENDAR. This one was found the hard way, by writing a client
that counted rows and getting a number 62 larger than the one that counted values. Until
1 May 2012 the ECB emitted a ROW for each closing day carrying an empty `OBS_VALUE` and
`OBS_STATUS` of `H`. After that date it emits no row at all. The calendar behind both is the
same and always has been. So a client that decides what arrived by counting ROWS is right
after 2012 and silently wrong before it, and the field that tells it so, `OBS_STATUS`, exists
in the old era and simply is not there in the new one.

TWO CLOSURES ARE NOT ANNUAL. 31 December 1999 and 31 December 2001 carry `OBS_STATUS` of `H`
and no value, and no annual rule explains either. They are the millennium changeover and the
euro cash changeover, each a one-off TARGET closure. Two special days in twenty-seven years
is exactly the kind of thing a rule set derived from three years of data would miss.

AND 1999 IS ITS OWN ERA, WHICH THE OTHER DIRECTION OF THE TEST FOUND. Good Friday and Easter
Monday 1999, the 2nd and 5th of April, carry real rates: TARGET's first year ran on national
calendars and had no harmonised closing days, and the six-day calendar applies from 2000. It
is two days out of seven thousand, it would never have been noticed by sampling, and it was
caught only because the suite also requires that every day carrying a rate is called open.

THE PAYLOAD CARRIES THE WRONG TIME. Each observation's `TITLE_COMPL` reads "ECB reference
exchange rate, US dollar/Euro, 2.15 pm (C.E.T.)". That is the moment the rate REFERS TO. It
is not the moment the rate becomes available: the ECB's own page says the rates "are usually
updated at around 16:00 CET every working day, except on TARGET closing days". A client that
takes 14:15 from the payload will call every not-yet-published afternoon a real gap.

Source: ECB statistics.
"""

from __future__ import annotations

import datetime
from enum import StrEnum

__all__ = [
    "ANNUAL_CLOSURES_FROM",
    "PLACEHOLDER_ROWS_END",
    "PUBLICATION_HOUR_LOCAL",
    "SERIES_BEGINS",
    "SPECIAL_CLOSURES",
    "ClosingReason",
    "closing_reason",
    "easter_sunday",
]


class ClosingReason(StrEnum):
    """Why the ECB published no reference rate on a given date.

    A StrEnum rather than a bare string so that a caller cannot invent a reason by typo, and so
    the exhaustive match in `coverage` fails loudly if a member is added here without teaching
    that match what to do with it.
    """

    WEEKEND = "weekend"
    NEW_YEARS_DAY = "new-years-day"
    GOOD_FRIDAY = "good-friday"
    EASTER_MONDAY = "easter-monday"
    LABOUR_DAY = "labour-day"
    CHRISTMAS_DAY = "christmas-day"
    SAINT_STEPHENS_DAY = "26-december"
    SPECIAL_CLOSURE = "special-closure"


# The last day the vendor emitted a placeholder row for a closure. This is NOT a calendar
# boundary: the calendar is the same on both sides of it. It is the boundary between two ways
# of saying the same thing, and it exists here only so that a reader who finds two different
# row counts for one series knows why.
PLACEHOLDER_ROWS_END = datetime.date(2012, 5, 1)

# The first date this series carries a value. Before it nothing was ever due, which is a
# different answer from "the market was shut" and is reported as such.
SERIES_BEGINS = datetime.date(1999, 1, 4)

# Two closures no annual rule produces, both confirmed by the vendor's own OBS_STATUS of H.
# TARGET ran on national calendars in its first year, so the annual rules start here. The
# evidence is two real rates on Good Friday and Easter Monday 1999 and none in any later year.
ANNUAL_CLOSURES_FROM = datetime.date(2000, 1, 1)

SPECIAL_CLOSURES: dict[datetime.date, str] = {
    datetime.date(1999, 12, 31): "millennium changeover",
    datetime.date(2001, 12, 31): "euro cash changeover",
}

# "around 16:00 CET", from the ECB's own page, and deliberately not the 14:15 the payload
# reports. The hour is local Frankfurt time, which is CET in winter and CEST in summer; the
# ECB writes CET for both. `around` is the vendor's word, so anything derived from this is
# treated as a soft boundary and never as a guarantee.
PUBLICATION_HOUR_LOCAL = 16


def easter_sunday(year: int) -> datetime.date:
    """Easter Sunday in the Gregorian calendar, by the anonymous algorithm.

    Good Friday and Easter Monday are the only two closing days that move, at 27 absences each
    across this series. Everything else is a fixed date, so this function is the whole of the
    hard part of the calendar.

    The first version of this paragraph said "the two most common absences, at fourteen each",
    and both halves were wrong. Fourteen was left over from counting missing ROWS before the
    2012 encoding change, and survived the correction to counting missing VALUES that doubled
    it. And they were never the most common absence by any measure: weekends are, at 2,884.
    `test_target_calendar.py` now recomputes both numbers, because a number in a docstring that
    nothing recomputes is a number that goes stale the first time the method under it changes.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    length = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * length) // 451
    month, day = divmod(h + length - 7 * m + 114, 31)
    return datetime.date(year, month, day + 1)


def closing_reason(day: datetime.date) -> ClosingReason | None:
    """Why no rate is expected on `day`, or None if one is.

    Weekends and the two special closures apply across the whole series. The six annual
    holidays apply from 2000, because TARGET's first year had no harmonised calendar.
    """
    if day.weekday() >= 5:
        return ClosingReason.WEEKEND
    if day in SPECIAL_CLOSURES:
        return ClosingReason.SPECIAL_CLOSURE
    if day < ANNUAL_CLOSURES_FROM:
        return None

    easter = easter_sunday(day.year)
    fixed: dict[tuple[int, int], ClosingReason] = {
        (1, 1): ClosingReason.NEW_YEARS_DAY,
        (5, 1): ClosingReason.LABOUR_DAY,
        (12, 25): ClosingReason.CHRISTMAS_DAY,
        (12, 26): ClosingReason.SAINT_STEPHENS_DAY,
    }
    if day == easter - datetime.timedelta(days=2):
        return ClosingReason.GOOD_FRIDAY
    if day == easter + datetime.timedelta(days=1):
        return ClosingReason.EASTER_MONDAY
    return fixed.get((day.month, day.day))
