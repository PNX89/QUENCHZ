"""Two callers, one budget neither can see.

The test that carries this file runs the same arrival pattern through both designs and
compares what the quiet caller got. That is the difference between a claim and a measurement,
and the naive bucket is kept in the source precisely so the comparison can be run rather than
asserted.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from quenchz.budget import FairBudget, ManualClock, NaiveSharedBucket

CASSETTES = pathlib.Path(__file__).resolve().parents[1] / "data" / "cassettes"


def test_a_burst_starves_the_quiet_caller_under_one_shared_bucket() -> None:
    """The behaviour being fixed, measured first so the fix has something to beat."""
    clock = ManualClock()
    bucket = NaiveSharedBucket(capacity=60, refill_per_second=60, clock=clock)

    greedy = sum(bucket.request("greedy").admitted for _ in range(1000))
    quiet = sum(bucket.request("quiet").admitted for _ in range(60))

    assert greedy == 60, "the burst takes the entire budget"
    assert quiet == 0, "and the quiet caller gets nothing at all"


def test_the_same_burst_cannot_touch_the_other_caller_s_reserve() -> None:
    """The claim, as the same numbers run through the other design."""
    clock = ManualClock()
    budget = FairBudget(capacity=60, refill_per_second=60, callers=("greedy", "quiet"), clock=clock)

    greedy = sum(budget.request("greedy").admitted for _ in range(1000))
    quiet = sum(budget.request("quiet").admitted for _ in range(60))

    # 60 tokens, half reserved and split between two callers, so each reserve is 15 and the
    # spare is 30. The burst may drain its own reserve and the whole spare, and stops there.
    assert greedy == 45
    assert quiet == 15, "exactly its reserve, and it was never available to the burst"


def test_the_reserve_is_what_a_caller_gets_back_after_being_starved() -> None:
    """Recovery, which is the property an operator actually asks about."""
    clock = ManualClock()
    budget = FairBudget(capacity=60, refill_per_second=60, callers=("greedy", "quiet"), clock=clock)
    for _ in range(1000):
        budget.request("greedy")
    assert budget.request("quiet").admitted is True

    for _ in range(1000):
        budget.request("quiet")
    assert budget.request("quiet").admitted is False

    # One second later the quiet caller's reserve has refilled, whatever the greedy one does.
    clock.advance(1.0)
    for _ in range(1000):
        budget.request("greedy")
    assert budget.request("quiet").admitted is True


def test_an_unknown_caller_cannot_manufacture_capacity_by_inventing_a_name() -> None:
    clock = ManualClock()
    budget = FairBudget(capacity=60, refill_per_second=60, callers=("known",), clock=clock)
    refused = budget.request("not-configured")
    assert refused.admitted is False
    assert "no reserve" in refused.reason


def test_fairness_costs_nothing_when_there_is_only_one_caller() -> None:
    """The case where the other term dominates.

    A fairness mechanism that reduced throughput for a lone caller would be paying for
    something nobody needed. With one caller the reserve plus the spare is the whole budget,
    so it admits exactly what the naive bucket admits.
    """
    naive_clock, fair_clock = ManualClock(), ManualClock()
    naive = NaiveSharedBucket(capacity=60, refill_per_second=60, clock=naive_clock)
    fair = FairBudget(capacity=60, refill_per_second=60, callers=("alone",), clock=fair_clock)
    assert sum(naive.request("alone").admitted for _ in range(1000)) == 60
    assert sum(fair.request("alone").admitted for _ in range(1000)) == 60


@pytest.mark.parametrize("seconds", [1, 5, 30, 120])
def test_the_admitted_rate_never_exceeds_the_configured_budget(seconds: int) -> None:
    """Conservative by test, over four window lengths.

    There is no published number to be conservative against, which is itself a finding. What
    can be asserted is that the limiter never admits more than it was configured for, under an
    arrival pattern that asks for far more than the budget every single second.
    """
    clock = ManualClock()
    budget = FairBudget(capacity=60, refill_per_second=60, callers=("a", "b", "c"), clock=clock)
    admitted = 0
    for _ in range(seconds):
        for caller in ("a", "b", "c"):
            admitted += sum(budget.request(caller).admitted for _ in range(100))
        clock.advance(1.0)

    ceiling = 60 + 60 * seconds  # a full bucket at the start, plus one second's refill each
    assert admitted <= ceiling, f"{admitted} admitted against a ceiling of {ceiling}"


def test_a_reserve_fraction_outside_the_unit_interval_is_refused() -> None:
    clock = ManualClock()
    for bad in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError, match="reserve_fraction"):
            FairBudget(
                capacity=60,
                refill_per_second=60,
                callers=("a",),
                clock=clock,
                reserve_fraction=bad,
            )


def test_a_budget_with_no_callers_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one caller"):
        FairBudget(capacity=60, refill_per_second=60, callers=(), clock=ManualClock())


def test_the_vendor_gives_a_limiter_nothing_to_react_to() -> None:
    """Why the budget is proactive, held against the recordings rather than asserted.

    A reactive limiter needs a signal. There is no rate-limit header on any response, and the
    vendor's documented status-code table does not include 429, so neither of the two
    conventional signals exists here.
    """
    index = json.loads((CASSETTES / "index.json").read_text())
    assert index, "there are recordings to check"
    for entry in index:
        headers = {header.lower() for header in entry["response_headers"]}
        assert not {h for h in headers if "ratelimit" in h.replace("-", "")}
        assert "retry-after" not in headers
        assert entry["status"] != 429


def test_a_reserve_refills_at_a_rate_rather_than_all_at_once() -> None:
    """Sub-second refill, which a whole-second test cannot see.

    This exists because a mutant survived. Replacing the incremental refill with "set the
    reserve back to full" passed every other test in this file, since they advance the clock
    by exactly one second and one second's refill happens to equal the full reserve. The
    difference only shows below a second, which is precisely where a burst lives.

    Three callers share 60 tokens with half reserved, so each reserve is 10 and refills at 10
    per second. A tenth of a second is worth one token, not ten.
    """
    clock = ManualClock()
    budget = FairBudget(capacity=60, refill_per_second=60, callers=("a", "b", "c"), clock=clock)

    # Drain everything this caller can reach: its own reserve, then the whole spare.
    drained = sum(budget.request("a").admitted for _ in range(1000))
    assert drained == 40, "reserve of 10 plus the entire spare of 30"
    assert budget.request("a").admitted is False

    clock.advance(0.1)
    granted = sum(budget.request("a").admitted for _ in range(100))
    assert granted == 4, (
        f"a tenth of a second is worth one reserve token and three of spare, got {granted}"
    )
