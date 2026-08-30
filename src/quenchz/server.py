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

EVERY TOOL THAT RETURNS OBSERVATIONS CARRIES A COVERAGE CERTIFICATE, and the other two say
plainly that they do not. The first draft of this line claimed every tool return carried one,
and a review pointed out that two of the three do not, which made a headline claim false in the
module that wires it up.

The claim was wrong rather than the code. A coverage certificate answers "how much of the
window you asked for actually arrived", and neither of the other tools is answering about a
window: `calendar.why` classifies one date, and `series.catalogue` lists what exists. Attaching
a certificate to either would be a field with nothing to say, which is worse than no field,
because a reader would believe it meant something. `test_server.py` enforces the narrowed rule
structurally rather than trusting this paragraph: any tool return containing `observations`
must also contain `coverage`.

RUNNING OUT OF BUDGET IS AN EXPECTED ANSWER, NOT A CRASH. This was found by running the
TypeScript proofs and watching the server's own output: the SDK logs a full stack trace for any
exception that is not a `ToolError`, so every routine rate-limit refusal printed a traceback
through four frames of anyio. A server that stack-traces its own normal behaviour buries the
one real failure in a thousand expected ones. The budget is now charged in `ConcealTheSurface`,
which returns a refusal result rather than raising at all, so nothing reaches that logger.

THE TOOLS DO NOT CHARGE. There is exactly one charge point and it is the middleware, because a
tool that charged as well would bill a granted call twice, and because a charge that happens
only when a tool actually runs makes every refusal free. That was measured: two hundred refused
calls at no cost while a granted tool stopped at forty-five.

THE HOST ALLOWLIST IS WRITTEN OUT RATHER THAN LEFT TO THE DEFAULT, and this paragraph used to
give the opposite reason. It said the SDK turns DNS-rebinding protection on with an empty
allowlist, so an unconfigured server fails closed, and called that the right default. Measured
against the installed SDK, both halves are wrong, and the SDK says so in its own source:

    TransportSecurityMiddleware.__init__:
        # If not specified, disable DNS rebinding protection by default for backwards
        # compatibility
        self.settings = settings or TransportSecuritySettings(
            enable_dns_rebinding_protection=False)

    Server.streamable_http_app:
        # Auto-enable DNS rebinding protection for localhost (IPv4 and IPv6)

So an unconfigured server FAILS OPEN on any bind address that is not loopback, and gets a
populated loopback allowlist when it is. The bare `TransportSecuritySettings()` the old
paragraph described is real as a class default and is never what the SDK constructs.

That makes naming the hosts more important than the old reasoning suggested, not less: it is
the difference between protection and none, rather than a way of relaxing something already
strict. A browser on the same machine as an agent is exactly the attacker this exists for.

Source: ECB statistics.
"""

from __future__ import annotations

import datetime
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette

from quenchz.budget import Clock, FairBudget, WallClock
from quenchz.concealment import ConcealTheSurface
from quenchz.gateway import Caller, Gateway
from quenchz.issuer import ISSUER, RESOURCE, Issuer
from quenchz.target_calendar import SERIES_BEGINS
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
    clock: Clock | None = None,
) -> MCPServer:
    """Wire the gateway to MCP. The interesting arguments are the ones NOT passed."""
    toolset = _toolset()
    gateway = Gateway(
        toolset=toolset,
        budget=FairBudget(
            capacity=60,
            refill_per_second=60,
            callers=CALLERS,
            # A WALL CLOCK BY DEFAULT, and it used to be a manual one. Defaulting to the
            # clock that only a test moves gave every server built outside this suite a
            # frozen one: 60 calls served, then refusal for the life of the process,
            # silently, while refill_per_second sat in the constructor doing nothing.
            # ManualClock is still what the interop proof passes, deliberately.
            clock=clock or WallClock(),
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
        # Runs before the SDK's own tool lookup, which would otherwise answer an unregistered
        # name with "Unknown tool: <name>" and undo the whole point of one shared refusal.
        middleware=[ConcealTheSurface(toolset, gateway.budget)],
    )

    @server.tool(name="rates.window", description="Euro reference rates across a date window.")
    def rates_window(cassette: str, start: str, end: str) -> dict[str, Any]:
        """Everything a caller can get wrong, turned into an answer rather than a traceback.

        THE SAME ARGUMENT AS THE BUDGET REFUSAL ABOVE, applied to the other three things a
        caller controls. `ToolError` is the SDK's word for "expected"; anything else is logged
        with a four frame anyio traceback and reaches the caller as
        `Error executing tool rates.window`, which names the tool and nothing else. Measured
        before this: a start of `0000-00-00` produced `ValueError: year 0 is out of range`, an
        unrecorded cassette name produced a `KeyError` that ENUMERATED EVERY RECORDING INTO THE
        LOG, and a window ending at 9999-12-31 produced `OverflowError`. All three arrived as
        HTTP 200 with `isError: true` and the text `Error executing tool rates.window`.

        What is quoted back is chosen rather than convenient. The caller's own dates are quoted,
        because it sent them. The list of recordings is not, because knowing which names exist
        is exactly what the concealment layer withholds from a caller with no scope for the
        catalogue, and a KeyError is a poor place to give it away.
        """
        try:
            first = datetime.date.fromisoformat(start)
            last = datetime.date.fromisoformat(end)
        except ValueError as broken:
            raise ToolError(
                f"start and end must be dates as YYYY-MM-DD; got {start!r} and {end!r}"
            ) from broken
        try:
            return gateway.rates_window(
                cassette,
                first,
                last,
                datetime.datetime.now(datetime.UTC),
            )
        except KeyError as unknown:
            raise ToolError(f"no recording named {cassette!r}") from unknown
        except ValueError as refused:
            raise ToolError(str(refused)) from refused

    @server.tool(name="calendar.why", description="Why no rate was published on a given date.")
    def calendar_why(day: str) -> dict[str, Any]:
        try:
            parsed = datetime.date.fromisoformat(day)
        except ValueError as broken:
            raise ToolError(f"day must be a date as YYYY-MM-DD; got {day!r}") from broken
        if parsed < SERIES_BEGINS:
            # Otherwise this answers "no reason, a rate was published" for every date before
            # the series existed, including 1 January 1999, three days before its first row.
            return {
                "date": day,
                "closed_because": "before_the_series",
                "source": "ECB statistics.",
            }
        return {
            "date": day,
            "closed_because": gateway.closing_reason_for(parsed),
            "source": "ECB statistics.",
        }

    @server.tool(
        name="series.catalogue",
        description="Every series this server can reach. Most callers are not granted this.",
    )
    def series_catalogue() -> dict[str, Any]:
        return {
            "series": ["EXR.D.USD.EUR.SP00.A"],
            "note": "One series. The catalogue exists to be a tool most callers cannot see.",
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
