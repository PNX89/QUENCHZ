"""A refused call has already paid.

The test that carries this file compares two callers who make the same number of calls, one
served and one refused every time, and requires that they have spent exactly the same budget.
If a refusal were free, a caller could map the tool surface by watching its own admitted rate
instead of by reading the refusals, and all the care taken over the refusal text would be
wasted on an oracle that does not use it.
"""

from __future__ import annotations

import datetime

import pytest

from quenchz.budget import FairBudget, ManualClock
from quenchz.gateway import OVER_BUDGET, Caller, Gateway, OverBudget
from quenchz.tools import Tool, ToolRefused, Toolset
from quenchz.upstream import CassetteTransport

WHEN = datetime.datetime(2026, 8, 26, 12, 0, tzinfo=datetime.UTC)


def _gateway(clock: ManualClock, callers: tuple[str, ...] = ("greedy", "quiet")) -> Gateway:
    return Gateway(
        Toolset(
            [
                Tool("rates.window", "rates:read", lambda **_: "served", "a window"),
                Tool("series.catalogue", "series:list", lambda **_: "served", "the catalogue"),
            ]
        ),
        FairBudget(capacity=60, refill_per_second=60, callers=callers, clock=clock),
        CassetteTransport(),
    )


# Far above any budget this file configures. The cap is not decoration: if refused calls ever
# stop costing anything, an uncapped loop here would spin for ever, and a test that hangs is
# worse than a test that fails because it blocks the pipeline instead of reporting.
CALL_CAP = 10_000


def _spend_until_refused(gateway: Gateway, caller: Caller, name: str) -> int:
    """How many calls this caller got through before the budget stopped it."""
    calls = 0
    while calls < CALL_CAP:
        try:
            gateway.call(name, caller, {})
        except OverBudget:
            return calls
        except ToolRefused:
            pass
        calls += 1
    raise AssertionError(
        f"{calls} calls to {name!r} and the budget never refused: this caller's calls are free"
    )


def test_a_refused_call_costs_the_same_budget_as_a_served_one() -> None:
    """The claim, as two numbers that have to be equal."""
    served_clock, refused_clock = ManualClock(), ManualClock()

    served = _spend_until_refused(
        _gateway(served_clock), Caller("greedy", frozenset({"rates:read"})), "rates.window"
    )
    refused = _spend_until_refused(
        _gateway(refused_clock), Caller("greedy", frozenset({"rates:read"})), "series.catalogue"
    )
    missing = _spend_until_refused(
        _gateway(ManualClock()), Caller("greedy", frozenset({"rates:read"})), "no.such.tool"
    )

    assert served == refused == missing, (
        f"served {served}, ungranted {refused}, nonexistent {missing}: a caller can tell "
        f"these apart by watching its own budget"
    )


def test_the_budget_refusal_names_nothing() -> None:
    """It says only what the caller already knows."""
    for forbidden in ("tool", "scope", "rates", "series", "quiet", "greedy"):
        assert forbidden not in OVER_BUDGET


def test_running_out_of_budget_is_a_different_answer_from_being_refused_a_tool() -> None:
    """The two refusals are distinct, which is correct and not a leak.

    A caller learns it has run out, which it could have worked out by counting. It learns
    nothing about which tools exist, because a call is charged before the name is looked at.
    """
    clock = ManualClock()
    gateway = _gateway(clock)
    caller = Caller("greedy", frozenset({"rates:read"}))

    with pytest.raises(ToolRefused):
        gateway.call("series.catalogue", caller, {})

    while True:
        try:
            gateway.call("rates.window", caller, {})
        except OverBudget:
            break

    # Now over budget, the ungranted tool no longer reports as ungranted, because the call
    # never gets that far. Both callers are told the same thing whatever they ask for.
    with pytest.raises(OverBudget):
        gateway.call("series.catalogue", caller, {})
    with pytest.raises(OverBudget):
        gateway.call("no.such.tool", caller, {})


def test_a_served_call_still_returns_its_answer() -> None:
    """The other direction. A gateway that refused everything would pass the tests above."""
    gateway = _gateway(ManualClock())
    assert gateway.call("rates.window", Caller("greedy", frozenset({"rates:read"})), {}) == "served"


def test_a_window_carries_the_certificate_that_describes_it() -> None:
    gateway = _gateway(ManualClock())
    answer = gateway.rates_window(
        "usd-eur-daily-easter-2026", datetime.date(2026, 3, 30), datetime.date(2026, 4, 10), WHEN
    )
    assert len(answer["observations"]) == 8
    assert answer["coverage"]["requested_calendar_days"] == 12
    assert answer["coverage"]["expected_observations"] == 8
    assert answer["coverage"]["absent"]["target_closed"] == 4
    assert answer["source"] == "ECB statistics."


def test_a_window_never_returns_an_observation_outside_what_was_asked_for() -> None:
    """The cassette holds far more than the window; the answer must not."""
    gateway = _gateway(ManualClock())
    answer = gateway.rates_window(
        "usd-eur-daily-full-history", datetime.date(2026, 4, 1), datetime.date(2026, 4, 3), WHEN
    )
    days = [datetime.date.fromisoformat(day) for day, _ in answer["observations"]]
    assert days == [datetime.date(2026, 4, 1), datetime.date(2026, 4, 2)]
    assert answer["coverage"]["absent"]["target_closed"] == 1, "Good Friday"


def test_a_body_in_the_wrong_format_is_raised_and_never_answered_from() -> None:
    """A 200 carrying XML must not become an empty but confident answer."""
    gateway = _gateway(ManualClock())
    with pytest.raises(ValueError, match="did not answer in the format"):
        gateway.rates_window(
            "format-that-does-not-exist",
            datetime.date(2026, 7, 1),
            datetime.date(2026, 7, 31),
            WHEN,
        )


def test_a_vendor_404_is_not_served_as_an_empty_window() -> None:
    """The classifier did its job and the gateway used to throw the answer away.

    `upstream.read` classifies the recorded 404 correctly as UNKNOWN_SERIES. This method used
    to test only for WRONG_FORMAT, so every other non-observation outcome fell through into
    the arithmetic and the caller received a success whose certificate was byte-identical to a
    genuinely empty window.
    """
    gateway = _gateway(ManualClock())
    with pytest.raises(ValueError, match="no such series"):
        gateway.rates_window(
            "unknown-series-key", datetime.date(2026, 1, 1), datetime.date(2026, 1, 31), WHEN
        )


def test_every_outcome_is_either_answered_or_raised_and_none_falls_through() -> None:
    """Exhaustive by name, so a new Outcome breaks the build rather than the caller."""
    gateway = _gateway(ManualClock())
    answered, raised = [], []
    for name in CassetteTransport().names():
        try:
            gateway.rates_window(name, datetime.date(2026, 7, 1), datetime.date(2026, 7, 31), WHEN)
            answered.append(name)
        except ValueError:
            raised.append(name)

    assert "unknown-series-key" in raised
    assert "format-that-does-not-exist" in raised
    assert "usd-eur-daily-one-month" in answered
    assert sorted(answered + raised) == CassetteTransport().names(), "one fell through"


def test_a_window_before_the_series_is_not_reported_as_gaps_through_the_gateway() -> None:
    """The first observed date is in hand here, so it must reach the certificate."""
    gateway = _gateway(ManualClock())
    answer = gateway.rates_window(
        "usd-eur-daily-full-history", datetime.date(1990, 1, 1), datetime.date(1990, 12, 31), WHEN
    )
    coverage = answer["coverage"]
    assert coverage["expected_observations"] == 0
    assert coverage["absent"]["no_such_observation"] == 0
    assert coverage["absent"]["before_the_series"] == 365


def test_an_outcome_this_match_has_not_been_taught_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The last arm is reachable and does work, rather than being decoration.

    A catch-all here would answer the caller anyway, from a response nobody classified, which
    is the exact defect the match was written to fix. This forces an unknown outcome through
    and requires a raise. Found because a mutation turning the match into `case _` survived
    every other test in this file.
    """
    from quenchz import gateway as gateway_module
    from quenchz.upstream import Reading

    monkeypatch.setattr(
        gateway_module,
        "read",
        lambda _response: Reading(
            outcome="something-nobody-declared",  # type: ignore[arg-type]
            observations={},
            placeholders=frozenset(),
            detail="a classification this gateway has never seen",
        ),
    )
    with pytest.raises(AssertionError, match="not handled by this match"):
        _gateway(ManualClock()).rates_window(
            "usd-eur-daily-one-month", datetime.date(2026, 7, 1), datetime.date(2026, 7, 31), WHEN
        )
