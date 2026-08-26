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

from quenchz.budget import ManualClock
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
    concealer = ConcealTheSurface(Toolset([Tool("a", "s", lambda **_: None, "a tool")]))

    with pytest.raises(RuntimeError, match="does not understand"):
        concealer._filter(object())

    with pytest.raises(RuntimeError, match="no 'tools' key"):
        concealer._filter({"something": "else"})
