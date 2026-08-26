"""When the ECB publishes a euro reference rate, and when a missing day is not a gap.

Everything in this module was derived from the vendor's own series rather than from a
document, and then checked against the vendor's documentation. The derivation is in
`tests/test_target_calendar.py`: across 7,140 observations from 1999-01-04 to 2026-08-25,
**every** absent weekday is explained by the six rules below and none is left over.

Two facts here are not obvious and both cost a naive implementation real accuracy.

THE CALENDAR CHANGED. The ECB published reference rates on TARGET closing days until
December 2012. The last holiday it published on is 1 May 2012; the first it closed for is
25 December 2012. A rule set that explains every absence since 2013 explains none of them
before it, so applying today's calendar to 2010 data reports gaps that are not gaps.

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
    "CALENDAR_IN_FORCE_FROM",
    "PUBLICATION_HOUR_LOCAL",
    "ClosingReason",
    "closing_reason",
    "easter_sunday",
]


class ClosingReason(StrEnum):
    """Why the ECB published no reference rate on a given date.

    A StrEnum rather than a bare string so that a caller cannot invent a seventh reason by
    typo, and so the exhaustive match in `coverage` fails loudly if a member is added here
    without teaching that match what to do with it.
    """

    WEEKEND = "weekend"
    NEW_YEARS_DAY = "new-years-day"
    GOOD_FRIDAY = "good-friday"
    EASTER_MONDAY = "easter-monday"
    LABOUR_DAY = "labour-day"
    CHRISTMAS_DAY = "christmas-day"
    SAINT_STEPHENS_DAY = "26-december"


# The first TARGET closing day this series actually honours. Derived, not looked up: the
# series publishes a rate on every earlier holiday, including Good Friday 2012 and 1 May
# 2012, and stops at Christmas. A test pins both sides of this boundary, because it is the
# one constant here that a reader would otherwise assume has always been true.
CALENDAR_IN_FORCE_FROM = datetime.date(2012, 12, 25)

# "around 16:00 CET", from the ECB's own page, and deliberately not the 14:15 the payload
# reports. The hour is local Frankfurt time, which is CET in winter and CEST in summer; the
# ECB writes CET for both. `around` is the vendor's word, so anything derived from this is
# treated as a soft boundary and never as a guarantee.
PUBLICATION_HOUR_LOCAL = 16


def easter_sunday(year: int) -> datetime.date:
    """Easter Sunday in the Gregorian calendar, by the anonymous algorithm.

    Good Friday and Easter Monday are the only two closing days that move, and they are also
    the two most common absences in the series, at fourteen each. Everything else is a fixed
    date, so this function is the whole of the hard part of the calendar.
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

    Weekends apply for the whole series. The six holidays apply only from
    `CALENDAR_IN_FORCE_FROM`, because before that date the ECB published on them.
    """
    if day.weekday() >= 5:
        return ClosingReason.WEEKEND
    if day < CALENDAR_IN_FORCE_FROM:
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
