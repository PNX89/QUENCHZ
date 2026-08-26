"""The MCP server, once it has stopped being somebody's subprocess.

Everything a stdio server gets free from having exactly one caller who shares its context has
to be decided here, and the wiring below is where three of those decisions become visible.

THE TRANSPORT IS GIVEN NO REQUIRED SCOPES, DELIBERATELY. `AuthSettings.required_scopes`
defaults to `None`, and it is left there. Setting it would put `RequireAuthMiddleware` in
charge of scope denial, and that middleware answers 403 with `error_description` set to
`f"Required scope: {required_scope}"`. It names the scope, which confirms the tool exists and
tells the caller exactly which grant would have worked. Scope denial happens at dispatch
instead, where both refusals can be made identical. `tests/test_server.py` asserts the setting
is still `None`, so this cannot be undone by somebody tidying up.

THE VERIFIER IS OURS BECAUSE THE SDK'S IS NOT ENOUGH. `BearerAuthBackend` checks the bearer
prefix, truthiness and expiry, and hands `AccessToken.resource` through without ever comparing
it. `AudienceRestrictedVerifier` is what makes a token minted for another resource useless
here.

EVERY TOOL RETURN CARRIES A COVERAGE CERTIFICATE. It is a required field with no default, so a
tool cannot answer without saying what it did not deliver.

THE HOST ALLOWLIST IS WRITTEN OUT RATHER THAN SWITCHED OFF. The SDK turns DNS-rebinding
protection on by default with an EMPTY allowlist, so a server that configures nothing answers
421 to everything, including itself. That is fail-closed and it is the right default, and the
tempting response to meeting it is `enable_dns_rebinding_protection=False`. The hosts this
server expects to be reached on are named instead, because a browser on the same machine as an
agent is exactly the attacker this protection exists for.

Source: ECB statistics.
"""

from __future__ import annotations

import datetime
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette

from quenchz.budget import FairBudget, ManualClock
from quenchz.gateway import Caller, Gateway
from quenchz.issuer import ISSUER, RESOURCE, Issuer
from quenchz.tokens import AudienceRestrictedVerifier
from quenchz.tools import Tool, Toolset
from quenchz.upstream import CassetteTransport, Transport

__all__ = ["ALLOWED_HOSTS", "CALLERS", "build_app", "build_server", "current_caller"]

# Named rather than disabled. See the module docstring.
ALLOWED_HOSTS = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]

# The callers this gateway holds a reserve for. Naming them is the point: a budget can only be
# divided fairly between parties it knows about, and an unknown caller gets nothing rather than
# a share, so that nobody can manufacture capacity by inventing a name.
CALLERS = ("agent-alpha", "agent-beta")


def current_caller() -> Caller:
    """Who is asking, taken from the verified token rather than from anything they sent."""
    token = get_access_token()
    if token is None:  # pragma: no cover
        raise PermissionError("no verified token on this request")
    return Caller(client_id=token.client_id, scopes=frozenset(token.scopes))


def build_server(
    issuer: Issuer,
    *,
    transport: Transport | None = None,
    clock: ManualClock | None = None,
) -> MCPServer:
    """Wire the gateway to MCP. The interesting arguments are the ones NOT passed."""
    gateway = Gateway(
        toolset=_toolset(),
        budget=FairBudget(
            capacity=60,
            refill_per_second=60,
            callers=CALLERS,
            clock=clock or ManualClock(),
        ),
        transport=transport or CassetteTransport(),
    )

    server: MCPServer = MCPServer(
        name="quenchz",
        version="0.1.0",
        instructions=(
            "Euro foreign exchange reference rates from the ECB Data Portal. Every answer "
            "carries a coverage certificate saying what it did not deliver and why. "
            "Source: ECB statistics."
        ),
        token_verifier=AudienceRestrictedVerifier(issuer.public_key_pem),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(ISSUER),
            resource_server_url=AnyHttpUrl(RESOURCE),
            # required_scopes is NOT set. See the module docstring: the middleware that would
            # enforce it names the scope it denies, and naming the scope names the tool.
        ),
    )

    @server.tool(name="rates.window", description="Euro reference rates across a date window.")
    def rates_window(cassette: str, start: str, end: str) -> dict[str, Any]:
        caller = current_caller()
        gateway.call("rates.window", caller, {})
        return gateway.rates_window(
            cassette,
            datetime.date.fromisoformat(start),
            datetime.date.fromisoformat(end),
            datetime.datetime.now(datetime.UTC),
        )

    @server.tool(name="calendar.why", description="Why no rate was published on a given date.")
    def calendar_why(day: str) -> dict[str, Any]:
        caller = current_caller()
        gateway.call("calendar.why", caller, {})
        parsed = datetime.date.fromisoformat(day)
        return {
            "date": day,
            "closed_because": gateway.closing_reason_for(parsed),
            "source": "ECB statistics.",
        }

    return server


def _toolset() -> Toolset:
    """The dispatch surface. Scopes live here, never on the transport."""
    return Toolset(
        [
            Tool("rates.window", "rates:read", lambda **_: None, "rates across a window"),
            Tool("calendar.why", "rates:read", lambda **_: None, "why a date has no rate"),
            Tool(
                "series.catalogue",
                "series:list",
                lambda **_: None,
                "every series this server can reach, which most callers may not enumerate",
            ),
        ]
    )


def build_app(
    issuer: Issuer, *, allowed_hosts: list[str] | None = None, **kwargs: Any
) -> Starlette:
    hosts = ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts
    return build_server(issuer, **kwargs).streamable_http_app(
        transport_security=TransportSecuritySettings(
            allowed_hosts=hosts,
            allowed_origins=[f"http://{host}" for host in hosts],
        )
    )
