"""One shared upstream budget, and callers who cannot see each other.

WHAT THE VENDOR TELLS YOU ABOUT ITS LIMITS, MEASURED RATHER THAN ASSUMED. Nothing. Three
things, each checked and each a test in this repository:

  1. No page of the API documentation states a rate limit, a throttle, a fair-use number or a
     concurrency cap.
  2. No response carries a rate-limit header. Every header of every recorded response is
     inspected by a test, and there is no `X-RateLimit-*` and no `Retry-After`.
  3. The vendor's own documented status-code table lists 200, 304, 400, 404, 406, 500, 501 and
     503. **429 is not in it.**

So a reactive limiter here is not merely imperfect, it is impossible: there is no signal to
react to, and the status that conventionally announces exhaustion is not one the vendor says
it will send. You cannot back off from something you cannot observe.

The only defensible design is therefore a budget chosen in advance and never exceeded, and the
honest way to describe it is as SELF-IMPOSED. This module does not claim to match the vendor's
enforcement. It claims to keep the server on the polite side of the only constraint the vendor
does state, which is in the reuse policy: access "may be restricted in exceptional
circumstances, for example if a user is acting in a manner contrary to the interests of other
users".

THE ONE NAMED DECISION: EACH CALLER HAS A RESERVE THAT NOBODY ELSE CAN SPEND. A single shared
bucket is the obvious design and it has a property nobody wants: a caller that bursts hard
takes capacity that a quiet caller would have been admitted for, and neither of them can see
the other to negotiate. Here the shared budget is divided into a reserve per caller plus a
common spare. A burst may drain the entire spare, and it can never touch another caller's
reserve. `tests/test_budget.py` measures both designs on the same arrival pattern and prints
what the naive one costs.

Source: ECB statistics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Admission", "FairBudget", "ManualClock", "NaiveSharedBucket"]


@dataclass(slots=True)
class ManualClock:
    """Time as an argument. A limiter that reads the wall clock cannot be tested at all."""

    now: float = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass(frozen=True, slots=True)
class Admission:
    admitted: bool
    reason: str


@dataclass(slots=True)
class NaiveSharedBucket:
    """One bucket for everybody. Present so the comparison is a measurement, not a claim."""

    capacity: float
    refill_per_second: float
    clock: ManualClock
    _tokens: float = field(init=False)
    _last: float = field(init=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last = self.clock.now

    def _refill(self) -> None:
        elapsed = self.clock.now - self._last
        self._last = self.clock.now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)

    def request(self, caller: str) -> Admission:
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return Admission(True, "admitted from the shared bucket")
        return Admission(False, "the shared bucket is empty")


@dataclass(slots=True)
class FairBudget:
    """A reserve per caller, plus a spare that anyone may drain.

    The invariant, and the only thing worth claiming: a caller can always spend its own
    reserve, however hard anybody else is bursting.
    """

    capacity: float
    refill_per_second: float
    callers: tuple[str, ...]
    clock: ManualClock
    reserve_fraction: float = 0.5
    _reserve: dict[str, float] = field(init=False)
    _spare: float = field(init=False)
    _last: float = field(init=False)

    def __post_init__(self) -> None:
        if not self.callers:
            raise ValueError("a fair budget needs at least one caller to be fair between")
        if not 0.0 < self.reserve_fraction <= 1.0:
            raise ValueError(f"reserve_fraction must be in (0, 1]: {self.reserve_fraction}")
        self._reserve = dict.fromkeys(self.callers, self._reserve_size)
        self._spare = self.capacity * (1.0 - self.reserve_fraction)
        self._last = self.clock.now

    @property
    def _reserve_size(self) -> float:
        return self.capacity * self.reserve_fraction / len(self.callers)

    @property
    def _reserve_refill(self) -> float:
        return self.refill_per_second * self.reserve_fraction / len(self.callers)

    def _refill(self) -> None:
        elapsed = self.clock.now - self._last
        self._last = self.clock.now
        if elapsed <= 0:
            return
        for caller in self._reserve:
            self._reserve[caller] = min(
                self._reserve_size, self._reserve[caller] + elapsed * self._reserve_refill
            )
        self._spare = min(
            self.capacity * (1.0 - self.reserve_fraction),
            self._spare + elapsed * self.refill_per_second * (1.0 - self.reserve_fraction),
        )

    def request(self, caller: str) -> Admission:
        if caller not in self._reserve:
            # An unknown caller has no reserve, and giving it the spare would let anybody
            # manufacture capacity by inventing a name.
            return Admission(False, "no reserve is held for this caller")
        self._refill()
        if self._reserve[caller] >= 1.0:
            self._reserve[caller] -= 1.0
            return Admission(True, "admitted from this caller's own reserve")
        if self._spare >= 1.0:
            self._spare -= 1.0
            return Admission(True, "admitted from the shared spare")
        return Admission(False, "this caller's reserve is spent and the spare is empty")
