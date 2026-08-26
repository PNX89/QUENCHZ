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
from typing import Any

from quenchz.budget import FairBudget
from quenchz.coverage import reconstruct
from quenchz.target_calendar import closing_reason
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
        """Answer a window, and say what did not arrive in it."""
        reading = read(self._transport.fetch(cassette))
        if reading.outcome is Outcome.WRONG_FORMAT:
            raise ValueError(f"the vendor did not answer in the format asked for: {reading.detail}")

        delivered = {
            day: value
            for day, value in reading.observations.items()
            if requested_from <= day <= requested_to
        }
        coverage = reconstruct(requested_from, requested_to, set(delivered), now)
        return {
            "observations": [[day.isoformat(), value] for day, value in sorted(delivered.items())],
            "coverage": coverage.model_dump(mode="json"),
            "source": "ECB statistics.",
        }

    def closing_reason_for(self, day: datetime.date) -> str | None:
        reason = closing_reason(day)
        return str(reason) if reason is not None else None


__all__ += ["NO_SUCH_TOOL", "OverBudget", "ToolRefused"]
