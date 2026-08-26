"""Two callers, one budget neither can see, and four claims in one run.

Everything printed below is computed here and now. No number is quoted from a document.

    uv run python examples/two_callers.py

Source: ECB statistics.
"""

from __future__ import annotations

import asyncio
import datetime

from quenchz.budget import FairBudget, ManualClock, NaiveSharedBucket
from quenchz.coverage import Absence
from quenchz.gateway import Gateway
from quenchz.issuer import RESOURCE, SOMEBODY_ELSE, Issuer
from quenchz.server import _toolset
from quenchz.tokens import AudienceRestrictedVerifier
from quenchz.tools import ToolRefused
from quenchz.upstream import CassetteTransport

WHEN = datetime.datetime(2026, 8, 26, 12, 0, tzinfo=datetime.UTC)
RULE = "-" * 78


def heading(text: str) -> None:
    print(f"\n{text}\n{RULE}")


async def audience() -> None:
    heading("AUDIENCE  a token is only good at the door it was minted for")
    issuer = Issuer()
    verifier = AudienceRestrictedVerifier(issuer.public_key_pem)

    cases = [
        ("minted for this resource", {}),
        ("minted for another resource", {"audience": SOMEBODY_ELSE}),
        ("minted for this resource AND another", {"audience": [RESOURCE, SOMEBODY_ELSE]}),
        ("minted with no audience at all", {"include_audience": False}),
        ("signed by an issuer we do not trust", {"issuer": "https://evil.invalid"}),
    ]
    for label, options in cases:
        token = issuer.mint(client_id="agent-alpha", scopes=["rates:read"], **options)  # type: ignore[arg-type]
        accepted = await verifier.verify_token(token)
        verdict = "ACCEPTED" if accepted else "refused"
        print(f"  {label:<38} {verdict}")
    print("\n  Every one of these is correctly signed, unexpired and carries the right scope.")


def reach() -> None:
    heading("REACH  a caller cannot learn what it may not use")
    tools = _toolset()
    granted = frozenset({"rates:read"})

    print(f"  tools this caller can see:  {tools.visible_to(granted)}")
    print(f"  tools that actually exist:  {tools.names()}")

    refusals = {}
    for name in ("series.catalogue", "series.cataloguz"):
        try:
            tools.dispatch(name, granted, {})
        except ToolRefused as refused:
            refusals[name] = str(refused)
    left, right = refusals["series.catalogue"], refusals["series.cataloguz"]
    print(f"\n  asking for one it may not use:  {left!r}")
    print(f"  asking for one that never was:  {right!r}")
    print(f"  identical bytes:                {left.encode() == right.encode()}")


def budget() -> None:
    heading("BUDGET  one burst cannot touch another caller's reserve")
    naive = NaiveSharedBucket(capacity=60, refill_per_second=60, clock=ManualClock())
    greedy_naive = sum(naive.request("greedy").admitted for _ in range(1000))
    quiet_naive = sum(naive.request("quiet").admitted for _ in range(60))

    fair = FairBudget(
        capacity=60, refill_per_second=60, callers=("greedy", "quiet"), clock=ManualClock()
    )
    greedy_fair = sum(fair.request("greedy").admitted for _ in range(1000))
    quiet_fair = sum(fair.request("quiet").admitted for _ in range(60))

    print(f"  {'design':<34}{'greedy':>8}{'quiet':>8}")
    print(f"  {'one shared bucket':<34}{greedy_naive:>8}{quiet_naive:>8}")
    print(f"  {'a reserve each, plus a spare':<34}{greedy_fair:>8}{quiet_fair:>8}")
    print("\n  The vendor publishes no rate limit, no rate-limit header, and does not list 429")
    print("  among its status codes, so this budget is self-imposed and reacts to nothing.")


def coverage() -> None:
    heading("COVERAGE  every answer says what it did not deliver")
    gateway = Gateway(
        _toolset(),
        FairBudget(
            capacity=60, refill_per_second=60, callers=("agent-alpha",), clock=ManualClock()
        ),
        CassetteTransport(),
    )
    answer = gateway.rates_window(
        "usd-eur-daily-easter-2026", datetime.date(2026, 3, 30), datetime.date(2026, 4, 10), WHEN
    )
    block = answer["coverage"]
    print(
        f"  requested   {block['requested_calendar_days']} calendar days, "
        f"{block['requested_from']} to {block['requested_to']}"
    )
    print(f"  expected    {block['expected_observations']}")
    print(f"  delivered   {block['delivered_observations']}")
    for reason in Absence:
        print(f"  absent, {reason.value:<20} {block['absent'][reason.value]}")
    print(f"\n  complete: {block['delivered_observations'] == block['expected_observations']}")
    print("  Four days are missing and none of them is a gap. Good Friday, Easter Monday and")
    print("  the weekend are closures, and the vendor's payload says nothing about any of them.")


async def main() -> None:
    print("QUENCHZ  what a tool server has to decide once it stops being a subprocess")
    await audience()
    reach()
    budget()
    coverage()
    print(f"\n{RULE}\nSource: ECB statistics.")


if __name__ == "__main__":
    asyncio.run(main())
