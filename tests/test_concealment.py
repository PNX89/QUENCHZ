"""What a caller can learn about tools it may not use, asked over MCP rather than in Python.

`test_reach.py` proves the two refusals identical at the dispatch layer. That was true and it
was not enough: over MCP the SDK answered an unregistered name with `Unknown tool: <name>`,
which differs from this repository's refusal and echoes the name back. A caller could tell
`series.catalogue` from `series.cataloguz` and had its map. This file holds the fix at the
layer a caller actually talks to.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest

from quenchz import concealment as concealment_module
from quenchz.budget import FairBudget, ManualClock
from quenchz.concealment import ConcealTheSurface
from quenchz.issuer import Issuer
from quenchz.server import build_app
from quenchz.tools import NO_SUCH_TOOL, Tool, Toolset

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _payload(text: str) -> dict[str, Any]:
    """The SDK answers over SSE, so the JSON lives on a `data:` line."""
    for line in text.splitlines():
        if line.startswith("data: "):
            return dict(json.loads(line[6:]))
    return dict(json.loads(text))


class Session:
    """One initialised MCP session, held open for the length of a test."""

    def __init__(self, client: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        self._client = client
        self._headers = headers
        self._id = 1

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        response = await self._client.post("/mcp", json=body, headers=self._headers)
        return _payload(response.text)

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        answer = await self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return dict(answer["result"])

    async def tool_names(self) -> list[str]:
        answer = await self.request("tools/list")
        return [tool["name"] for tool in answer["result"]["tools"]]


@contextlib.asynccontextmanager
async def session(issuer: Issuer, scopes: list[str]) -> AsyncIterator[Session]:
    """Serve, authenticate and initialise. See test_server.py for why this is not a fixture."""
    app = build_app(issuer, clock=ManualClock())
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            headers = dict(HEADERS)
            headers["Authorization"] = (
                f"Bearer {issuer.mint(client_id='agent-alpha', scopes=scopes)}"
            )
            opened = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "the-tests", "version": "0"},
                    },
                },
                headers=headers,
            )
            headers["mcp-session-id"] = opened.headers["mcp-session-id"]
            await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=headers,
            )
            yield Session(client, headers)


@pytest.fixture
def issuer() -> Issuer:
    return Issuer()


async def test_an_ungranted_tool_and_a_missing_tool_refuse_identically_over_mcp(
    issuer: Issuer,
) -> None:
    """The claim at the layer a caller actually talks to, as a byte comparison."""
    async with session(issuer, ["rates:read"]) as caller:
        ungranted = await caller.call("series.catalogue")
        missing = await caller.call("series.cataloguz")
        invented = await caller.call("totally.invented.name")

    texts = [result["content"][0]["text"] for result in (ungranted, missing, invented)]
    assert all(result["isError"] for result in (ungranted, missing, invented))
    assert texts[0].encode() == texts[1].encode() == texts[2].encode()
    assert texts[0] == NO_SUCH_TOOL


async def test_the_refusal_does_not_echo_the_name_it_was_asked_for(issuer: Issuer) -> None:
    """The SDK's own answer is `Unknown tool: <name>`. This must not be that."""
    async with session(issuer, ["rates:read"]) as caller:
        for name in ("series.catalogue", "a.name.nobody.chose", "rates.window.v2"):
            refused = await caller.call(name)
            text = refused["content"][0]["text"]
            assert name not in text
            assert "Unknown tool" not in text


@pytest.mark.parametrize(
    ("scopes", "expected"),
    [
        (["rates:read"], ["calendar.why", "rates.window"]),
        (["rates:read", "series:list"], ["calendar.why", "rates.window", "series.catalogue"]),
        (["series:list"], ["series.catalogue"]),
        ([], []),
    ],
)
async def test_the_listing_shows_only_what_the_caller_may_call(
    issuer: Issuer, scopes: list[str], expected: list[str]
) -> None:
    """A tool a caller cannot use is a tool it is never told about."""
    async with session(issuer, scopes) as caller:
        assert sorted(await caller.tool_names()) == expected


async def test_a_granted_tool_still_answers(issuer: Issuer) -> None:
    """The other direction. A server that refused everything would pass all of the above."""
    async with session(issuer, ["rates:read"]) as caller:
        answer = await caller.call("calendar.why", {"day": "2026-04-03"})
    assert answer["isError"] is False, answer["content"][0]["text"]
    assert "good-friday" in json.dumps(answer)


def test_the_filter_refuses_a_shape_it_does_not_understand() -> None:
    """The guard that silently did nothing, now made loud.

    The first version of the filter read `result.tools` and passed the result through
    untouched when that attribute was missing, which is exactly what the SDK returns: a plain
    dict. It looked like a working filter in the source and in the diff, and it leaked the
    whole tool list. Anything unrecognised now raises.
    """
    concealer = ConcealTheSurface(
        Toolset([Tool("a", "s", lambda **_: None, "a tool")]),
        FairBudget(capacity=60, refill_per_second=60, callers=("a",), clock=ManualClock()),
    )

    with pytest.raises(RuntimeError, match="does not understand"):
        concealer._filter(object())

    with pytest.raises(RuntimeError, match="no 'tools' key"):
        concealer._filter({"something": "else"})


async def _calls_before_the_budget_stops(
    caller: Session, name: str, arguments: dict[str, Any]
) -> int:
    """How many calls got through before the budget refused, capped so a leak cannot hang."""
    for attempt in range(500):
        answer = await caller.call(name, arguments)
        text = answer["content"][0]["text"] if answer.get("content") else ""
        if "budget" in text:
            return attempt
    raise AssertionError(f"500 calls to {name!r} and the budget never refused: they are free")


async def test_a_refused_call_costs_the_same_as_a_served_one_over_mcp(issuer: Issuer) -> None:
    """The gateway's invariant, held at the layer a caller actually talks to.

    `gateway.py` charges before it looks a tool up, so that a refusal is never cheaper than an
    answer. This middleware then began refusing before the gateway was reached, and over MCP a
    refused call cost nothing: measured at two hundred refusals for free while a granted tool
    stopped at forty-five. The charge moved here, to the one boundary every tools/call crosses.

    Being exact about what that defect was, since the first report of it overstated the case:
    an ungranted name and a nonexistent one were equally free, so it was never an oracle for
    which tools exist. It was a stated invariant that had quietly become false, and an
    unmetered probe of the whole namespace.
    """
    async with session(issuer, ["rates:read"]) as caller:
        granted = await _calls_before_the_budget_stops(
            caller, "calendar.why", {"day": "2026-04-03"}
        )
    async with session(issuer, ["rates:read"]) as caller:
        ungranted = await _calls_before_the_budget_stops(caller, "series.catalogue", {})
    async with session(issuer, ["rates:read"]) as caller:
        missing = await _calls_before_the_budget_stops(caller, "no.such.tool", {})

    assert granted == ungranted == missing, (
        f"served {granted}, ungranted {ungranted}, nonexistent {missing}: a caller can tell "
        f"these apart by watching its own budget"
    )
    assert granted == 45, "reserve of 15 plus the whole spare of 30, with the clock frozen"


async def test_the_budget_refusal_over_mcp_names_nothing(issuer: Issuer) -> None:
    """It says only what the caller already knows, exactly as the dispatch refusal does."""
    async with session(issuer, ["rates:read"]) as caller:
        await _calls_before_the_budget_stops(caller, "calendar.why", {"day": "2026-04-03"})
        spent = await caller.call("series.catalogue", {})
    text = spent["content"][0]["text"]
    for forbidden in ("series", "catalogue", "scope", "rates", "Unknown tool"):
        assert forbidden not in text, f"the budget refusal leaks {forbidden!r}"


def test_the_object_shaped_result_is_filtered_and_not_only_the_dict_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch the module was written for, which no test had ever reached.

    The filter handles two shapes. Every test and every proof drives the dict one, because that
    is what this SDK version returns. The object arm existed for the day the SDK returns a model
    instead, which is the whole reason the module's docstring says a shape it does not recognise
    must raise rather than pass through.

    That arm could be DELETED and the suite stayed green. Removing the list comprehension so the
    method fell straight to `return result` left an object-shaped response completely unfiltered,
    which is the leak this module exists to prevent, and nothing objected. `object()` and
    `{"something": "else"}` both raise before reaching it, so the refusal test could not cover it
    either.

    Driven directly rather than over HTTP, since HTTP cannot produce the shape at all here.
    """

    class Named:
        def __init__(self, name: str) -> None:
            self.name = name

    class ListResult:
        def __init__(self, tools: list[Named]) -> None:
            self.tools = tools

    concealer = ConcealTheSurface(
        Toolset(
            [
                Tool("visible", "rates:read", lambda **_: None, "a tool this caller may see"),
                Tool("concealed", "admin", lambda **_: None, "a tool it may not"),
            ]
        ),
        FairBudget(capacity=60, refill_per_second=60, callers=("a",), clock=ManualClock()),
    )
    # The scopes a request would have carried. Patched rather than faked through HTTP because
    # the object shape cannot be produced over HTTP by this SDK version at all, which is the
    # reason the branch had no coverage in the first place.
    monkeypatch.setattr(concealment_module, "_granted_scopes", lambda: frozenset({"rates:read"}))
    result = ListResult([Named("visible"), Named("concealed")])
    filtered = concealer._filter(result)

    assert [tool.name for tool in filtered.tools] == ["visible"], (
        "an object shaped tools/list came back carrying a tool this caller has no scope for"
    )
    assert filtered is result, "the object was replaced rather than filtered in place"
