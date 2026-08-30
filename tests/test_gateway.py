"""A refused call has already paid.

The test that carries this file compares two callers who make the same number of calls, one
served and one refused every time, and requires that they have spent exactly the same budget.
If a refusal were free, a caller could map the tool surface by watching its own admitted rate
instead of by reading the refusals, and all the care taken over the refusal text would be
wasted on an oracle that does not use it.
"""

from __future__ import annotations

import ast
import datetime
import inspect
import textwrap
from collections.abc import Callable

import pytest

from quenchz.budget import FairBudget, ManualClock
from quenchz.gateway import OVER_BUDGET, Caller, Gateway, OverBudget
from quenchz.tools import Tool, ToolRefused, Toolset
from quenchz.upstream import CassetteTransport, Outcome, RawResponse, Transport, read


def names_matched_in_case_patterns(func: Callable[..., object]) -> set[str]:
    """The enum member names an exhaustive `match` actually tests a value against.

    `inspect.getsource(func)` used to be searched as text for `f"Outcome.{member.name}"`, which
    also matches the function's own docstring, an inline comment, or a `#:` note, none of which
    stop the interpreter falling through: naming a member only in a comment satisfied the old
    check while leaving it genuinely unhandled. This parses the source instead and reads only the
    `case` patterns of the one `match` statement, which is the part the interpreter tests a value
    against, walking `MatchOr` for a `case A | B:` union and reading the attribute off each
    `MatchValue`, which is what a dotted name like `Outcome.WRONG_FORMAT` compiles to.
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


WHEN = datetime.datetime(2026, 8, 26, 12, 0, tzinfo=datetime.UTC)


def _gateway(
    clock: ManualClock,
    callers: tuple[str, ...] = ("greedy", "quiet"),
    transport: Transport | None = None,
) -> Gateway:
    return Gateway(
        Toolset(
            [
                Tool("rates.window", "rates:read", lambda **_: "served", "a window"),
                Tool("series.catalogue", "series:list", lambda **_: "served", "the catalogue"),
            ]
        ),
        FairBudget(capacity=60, refill_per_second=60, callers=callers, clock=clock),
        transport or CassetteTransport(),
    )


class _OneResponse:
    """A transport that hands back one chosen response, whatever it is asked for.

    `Transport` is a Protocol, so this is the whole of it. The committed corpus carries a 200
    with rows, an empty 200, a 200 in the wrong format and a 404, and nothing that is a 400 or a
    5xx. That is exactly why two arms of the gateway's match had no behavioural test.
    """

    def __init__(self, response: RawResponse) -> None:
        self._response = response

    def fetch(self, name: str) -> RawResponse:
        return self._response


# One response per Outcome, each chosen so that `upstream.read` classifies it as that member,
# paired with what the gateway then owes a caller: None to answer it, or the words its refusal
# has to carry. Written out here rather than read from the enum, and pinned by name and size
# below, because a parametrisation taken straight from the code under test covers one case
# fewer the moment a member is deleted, and reads exactly like a pass.
RESPONSES: dict[Outcome, tuple[RawResponse, str | None]] = {
    Outcome.OBSERVATIONS: (
        RawResponse(200, "text/csv", b"TIME_PERIOD,OBS_VALUE\n2026-07-01,1.1646\n"),
        None,
    ),
    Outcome.EMPTY_WINDOW: (RawResponse(200, "text/csv", b""), None),
    Outcome.WRONG_FORMAT: (
        RawResponse(200, "application/vnd.sdmx.genericdata+xml", b"<?xml version='1.0'?><d/>"),
        "did not answer in the format asked for",
    ),
    Outcome.UNKNOWN_SERIES: (
        RawResponse(404, "application/problem+json", b'{"detail": "no series named that"}'),
        "has no such series",
    ),
    Outcome.REJECTED_PARAMETERS: (
        RawResponse(400, "text/html", b"<html>the parameters were rejected</html>"),
        "rejected the request",
    ),
    Outcome.VENDOR_UNAVAILABLE: (
        RawResponse(503, "text/html", b"<html>service unavailable</html>"),
        "could not answer",
    ),
    Outcome.NOT_MODIFIED: (
        RawResponse(304, "text/html", b""),
        "did not answer what was asked",
    ),
}


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

    # Bounded for the same reason as CALL_CAP above, and this loop was missed when that cap
    # was added. A mutation that makes refused calls free turns an unbounded loop here into a
    # hang, and a test that hangs blocks the pipeline instead of reporting.
    for _ in range(CALL_CAP):
        try:
            gateway.call("rates.window", caller, {})
        except OverBudget:
            break
    else:
        raise AssertionError(f"{CALL_CAP} calls and the budget never refused")

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
    """Exhaustive by name, so a new Outcome breaks the build rather than the caller.

    `assert sorted(answered + raised) == CassetteTransport().names()` used to close this out,
    and it cannot fail: every name in the corpus is appended to exactly one of the two lists by
    construction, so their sorted union always equals the list they were both built from,
    whatever the gateway does with any of them. Checked directly: making every non-observation
    outcome answer instead of raise, and deleting the three assertions above this one, still
    left it green. What can fail, and is worth asserting, is that the corpus still exercises
    both branches: a corpus that lost every failing cassette would make this "exhaustive" test
    exhaustive over one outcome.
    """
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
    assert answered, "the corpus holds no cassette this gateway answers; the split is vacuous"
    assert raised, "the corpus holds no cassette this gateway raises on; the split is vacuous"


def test_a_window_before_the_series_is_not_reported_as_gaps_through_the_gateway() -> None:
    """The series start is in hand here, so it must reach the certificate."""
    gateway = _gateway(ManualClock())
    answer = gateway.rates_window(
        "usd-eur-daily-full-history", datetime.date(1990, 1, 1), datetime.date(1990, 12, 31), WHEN
    )
    coverage = answer["coverage"]
    assert coverage["expected_observations"] == 0
    assert coverage["absent"]["no_such_observation"] == 0
    assert coverage["absent"]["before_the_series"] == 365


def test_a_window_opening_before_the_recording_is_not_called_before_the_series() -> None:
    """The series start is a fact about the SERIES, never about the slice that came back.

    This is the case the test above cannot express. It asks the full history, the one recording
    whose first row happens to be the first row of the series, so taking the start from the
    response agrees with taking it from the calendar and the two are indistinguishable.

    `usd-eur-daily-one-month` carries July 2026 and nothing else. Asking it for June as well
    used to read the series as beginning on 1 July, so every rate the ECB really published in
    June was filed under `before_the_series`, whose documented meaning is that nothing was ever
    due, and the certificate came back complete on a two month window a third of which arrived.
    """
    gateway = _gateway(ManualClock())
    answer = gateway.rates_window(
        "usd-eur-daily-one-month", datetime.date(2026, 6, 1), datetime.date(2026, 7, 31), WHEN
    )
    coverage = answer["coverage"]

    # Counted out of the vendor's own full history rather than typed here. The ECB published on
    # every TARGET open day that month, so this is both what was owed and what is missing from
    # the narrow recording, and every one of them has to be reported as missing.
    published_in_june = [
        day
        for day in read(CassetteTransport().fetch("usd-eur-daily-full-history")).observations
        if datetime.date(2026, 6, 1) <= day <= datetime.date(2026, 6, 30)
    ]
    assert published_in_june, "the full history no longer covers June 2026, so this proves nothing"

    assert coverage["absent"]["before_the_series"] == 0, (
        "days the vendor published rates on are being certified as days nothing was ever due"
    )
    assert coverage["absent"]["no_such_observation"] == len(published_in_june)
    assert coverage["delivered_observations"] < coverage["expected_observations"], (
        "a window missing a month of published rates is being certified as complete"
    )


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
    with pytest.raises(AssertionError, match="Expected code to be unreachable"):
        _gateway(ManualClock()).rates_window(
            "usd-eur-daily-one-month", datetime.date(2026, 7, 1), datetime.date(2026, 7, 31), WHEN
        )


def test_every_outcome_is_named_in_the_gateway_match() -> None:
    """`Outcome.VENDOR_UNAVAILABLE` did not exist, and adding it was a silent change.

    Before this, every status that was not 200, 400 or 404 fell through to
    `REJECTED_PARAMETERS`, so the vendor's own documented 500, 501 and 503 told a caller its
    parameters were wrong. Adding the member type checked and the suite passed, because the
    match ended in a catch-all that gave mypy a total match.

    Read from the `case` patterns of the match, not from the function's text: a substring search
    over the whole source is satisfied by a member named in a comment or in this docstring, which
    is not an arm and does not stop a real one falling through. Reading only the patterns is what
    a member added without an arm is caught by here as well as by mypy, and a reader of the tests
    can see the rule without running the type checker.

    THIS IS NOT THE SECOND DEFENCE IT LOOKS LIKE. Reading the patterns cannot tell an arm that
    raises from an arm whose body is `pass`, and neither can `assert_never`, which sees a named
    arm either way. Emptying the REJECTED_PARAMETERS or VENDOR_UNAVAILABLE arm left pytest,
    mypy and ruff all green, and served a vendor outage to a caller as five genuine gaps under
    the ECB's attribution. The behavioural half is
    `test_the_gateway_answers_or_refuses_every_outcome_by_name`.
    """
    from quenchz.upstream import Outcome

    matched = names_matched_in_case_patterns(Gateway.rates_window)
    unnamed = [member.name for member in Outcome if member.name not in matched]
    assert unnamed == [], (
        f"these outcomes have no arm in the gateway match: {unnamed}. Falling through means "
        f"answering a caller about a response nobody classified"
    )


def test_every_outcome_has_a_response_here_and_the_set_is_the_one_upstream_declares() -> None:
    """Pinned by name AND by size, so the parametrisation below cannot quietly shrink.

    A case list read out of `Outcome` covers one fewer outcome the day a member is deleted and
    stays green, which is indistinguishable from a pass. Naming the members and counting them
    turns that into a failure that says what happened.
    """
    assert set(RESPONSES) == set(Outcome)
    assert len(RESPONSES) == 7, (
        f"{len(RESPONSES)} outcomes are covered here; an Outcome was added or removed and this "
        f"file has not been taught what the gateway owes a caller for it"
    )


@pytest.mark.parametrize("outcome", sorted(RESPONSES, key=str))
def test_the_gateway_answers_or_refuses_every_outcome_by_name(outcome: Outcome) -> None:
    """Every outcome driven through the real gateway, including the two no recording carries.

    `test_every_outcome_is_either_answered_or_raised_and_none_falls_through` enumerates the
    committed cassettes, and none of them is a 400 or a 5xx, so REJECTED_PARAMETERS and
    VENDOR_UNAVAILABLE never reached the gateway at all. Replacing either arm's body with
    `pass` left pytest, mypy and ruff green while a vendor outage came back as five genuine
    gaps filed under `no_such_observation`, whose documented meaning is that it was due, it is
    late and somebody should know, carried under "Source: ECB statistics.".
    """
    response, refusal = RESPONSES[outcome]
    assert read(response).outcome is outcome, (
        f"this response is no longer read as {outcome}, so this case is not the one it names"
    )

    gateway = _gateway(ManualClock(), transport=_OneResponse(response))
    window = (datetime.date(2026, 7, 1), datetime.date(2026, 7, 31))
    if refusal is None:
        answer = gateway.rates_window("whatever", *window, WHEN)
        assert answer["coverage"]["source"] == "ECB statistics."
    else:
        with pytest.raises(ValueError, match=refusal):
            gateway.rates_window("whatever", *window, WHEN)
