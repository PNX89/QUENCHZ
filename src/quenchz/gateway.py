"""One call, in order: charge, dispatch, answer, certify.

THE ONE NAMED DECISION: THE BUDGET IS CHARGED BEFORE THE TOOL IS LOOKED UP. The obvious order
is the other way round. Look the tool up, find the caller may not have it, refuse, and charge
nothing, because why would you spend budget on a call you did not serve.

That is an oracle. `tools.py` goes to some trouble to make the refusal for an ungranted tool
byte-identical to the refusal for a tool that does not exist, so that a caller cannot map the
surface by guessing names. If an ungranted call is free and a served call is not, the caller
does not need to read the refusal at all: it asks for a hundred names, watches which ones move
its own admitted rate, and it has the same map, drawn by the side effect instead of the text.

So every call costs the same, whatever happens next. A refused call has already paid.
`tests/test_gateway.py` asserts that a caller's remaining budget after a hundred refused calls
is identical to its budget after a hundred served ones.

Source: ECB statistics.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, assert_never

from quenchz.budget import FairBudget
from quenchz.coverage import reconstruct
from quenchz.target_calendar import SERIES_BEGINS, closing_reason
from quenchz.tools import NO_SUCH_TOOL, ToolRefused, Toolset
from quenchz.upstream import Outcome, Transport, read

__all__ = ["OVER_BUDGET", "Caller", "Gateway"]

# Like NO_SUCH_TOOL, this never names a tool, a scope or another caller. It says only that
# this caller has run out, which is the one thing it already knows.
OVER_BUDGET = "this caller has spent its share of the upstream budget"


@dataclass(frozen=True, slots=True)
class Caller:
    client_id: str
    scopes: frozenset[str]


class OverBudget(Exception):
    def __init__(self) -> None:
        super().__init__(OVER_BUDGET)


class Gateway:
    def __init__(self, toolset: Toolset, budget: FairBudget, transport: Transport) -> None:
        self._tools = toolset
        self._budget = budget
        self._transport = transport

    @property
    def budget(self) -> FairBudget:
        """The one budget, so the transport boundary charges the same one the gateway does."""
        return self._budget

    def call(self, name: str, caller: Caller, arguments: dict[str, Any]) -> Any:
        # First, always, and before anything that could depend on `name`. See the module
        # docstring: making a refusal free is the same leak as making it descriptive.
        if not self._budget.request(caller.client_id).admitted:
            raise OverBudget

        return self._tools.dispatch(name, caller.scopes, arguments)

    def rates_window(
        self,
        cassette: str,
        requested_from: datetime.date,
        requested_to: datetime.date,
        now: datetime.datetime,
    ) -> dict[str, Any]:
        """Answer a window, and say what did not arrive in it.

        THE OUTCOME IS MATCHED EXHAUSTIVELY BY NAME. It used to test only for WRONG_FORMAT and
        let everything else fall through into the arithmetic below, so a vendor 404 for a
        series that does not exist reached the caller as a success, with a certificate
        byte-identical to a genuinely empty window. `upstream.read` had already classified it
        correctly as UNKNOWN_SERIES and this method threw the classification away, which is the
        worse half of it: the work was done and then discarded.
        """
        reading = read(self._transport.fetch(cassette))

        match reading.outcome:
            case Outcome.OBSERVATIONS | Outcome.EMPTY_WINDOW:
                pass
            case Outcome.WRONG_FORMAT:
                raise ValueError(
                    f"the vendor did not answer in the format asked for: {reading.detail}"
                )
            case Outcome.UNKNOWN_SERIES:
                raise ValueError(f"the vendor has no such series: {reading.detail}")
            case Outcome.REJECTED_PARAMETERS:
                raise ValueError(f"the vendor rejected the request: {reading.detail}")
            case Outcome.VENDOR_UNAVAILABLE:
                # Named separately from a rejected request on purpose. Whoever is on call needs
                # to know whether to read the request or the vendor's status page, and this
                # branch is the only place that distinction is still available.
                raise ValueError(f"the vendor could not answer: {reading.detail}")
            case unhandled:
                # assert_never RATHER THAN A CATCH-ALL, and the difference is when it fires. The
                # previous form raised at run time and gave mypy a total match, so adding
                # VENDOR_UNAVAILABLE to the enum type checked, passed the suite, and would have
                # reached a caller as a rejected request. This is a build error instead.
                assert_never(unhandled)

        delivered = {
            day: value
            for day, value in reading.observations.items()
            if requested_from <= day <= requested_to
        }
        # The first date this series ever carried a value, READ FROM THE CALENDAR AND NEVER FROM
        # THE BODY. It was `min(reading.observations)`, which is the first date of whichever
        # slice came back, and three of the four recordings are a narrow window. So a window
        # opening before that slice had its real published rates filed under BEFORE_THE_SERIES,
        # whose documented meaning is that nothing was ever due, and the certificate said
        # complete. That is the 1990 defect run backwards: instead of confidently reporting 261
        # gaps where none was owed, it confidently reported none where sixty had been published.
        # One series is served here, so its start is a constant. A second series would carry its
        # own start beside its key rather than have one inferred from a response.
        coverage = reconstruct(
            requested_from, requested_to, set(delivered), now, series_begins=SERIES_BEGINS
        )
        return {
            "observations": [[day.isoformat(), value] for day, value in sorted(delivered.items())],
            "coverage": coverage.model_dump(mode="json"),
            "source": "ECB statistics.",
        }

    def closing_reason_for(self, day: datetime.date) -> str | None:
        reason = closing_reason(day)
        return str(reason) if reason is not None else None


__all__ += ["NO_SUCH_TOOL", "OverBudget", "ToolRefused"]
