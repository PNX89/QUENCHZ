"""The claims, over real HTTP, through the actual SDK transport.

Everything in `test_audience.py` is proved against the verifier directly. This file proves the
same things through a running ASGI application, because a verifier that is correct and not
wired in is worth nothing, and the wiring is where the two decisions about the SDK live.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator

import httpx2
import pytest
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.applications import Starlette

from quenchz.issuer import RESOURCE, SOMEBODY_ELSE, Issuer
from quenchz.server import ALLOWED_HOSTS, build_app, build_server

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "the-tests", "version": "0"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def issuer() -> Issuer:
    return Issuer()


@contextlib.asynccontextmanager
async def serving(
    issuer: Issuer, *, base_url: str = "http://127.0.0.1"
) -> AsyncIterator[httpx2.AsyncClient]:
    """Run the application and hand back a client that talks to it over ASGI.

    This is a helper rather than a yield fixture on purpose. The session manager opens an anyio
    task group in the lifespan, and anyio requires a cancel scope to be exited in the task that
    entered it; a generator fixture is torn down in a different task and fails at teardown even
    when every assertion in the test passed. Entering the context inside the test body keeps
    both ends in one task.
    """
    app: Starlette = build_app(issuer)
    # The lifespan is what starts the session manager. Without it every authenticated request
    # fails with "Task group is not initialized", which is a real thing to get wrong once.
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=base_url) as client:
            yield client


async def _initialize(client: httpx2.AsyncClient, token: str | None) -> httpx2.Response:
    headers = dict(HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return await client.post("/mcp", json=INITIALIZE, headers=headers)


async def test_a_token_for_this_resource_gets_through_the_transport(issuer: Issuer) -> None:
    """The other direction first: a server that refused everything would pass the rest."""
    async with serving(issuer) as client:
        response = await _initialize(
            client, issuer.mint(client_id="agent-alpha", scopes=["rates:read"])
        )
    assert response.status_code == 200
    assert '"result"' in response.text


@pytest.mark.parametrize(
    ("label", "mint"),
    [
        ("no token at all", None),
        ("minted for another resource", {"audience": SOMEBODY_ELSE}),
        ("minted for this resource and another", {"audience": [RESOURCE, SOMEBODY_ELSE]}),
        ("minted with no audience claim", {"include_audience": False}),
    ],
)
async def test_the_audience_gate_holds_over_http(
    issuer: Issuer, label: str, mint: dict[str, object] | None
) -> None:
    token = (
        None
        if mint is None
        else issuer.mint(client_id="agent-alpha", scopes=["rates:read"], **mint)  # type: ignore[arg-type]
    )
    async with serving(issuer) as client:
        response = await _initialize(client, token)
    assert response.status_code == 401, f"{label} reached the server"
    assert response.headers["www-authenticate"].startswith("Bearer ")


def test_the_transport_is_given_no_required_scopes(issuer: Issuer) -> None:
    """The decision that stops the SDK naming a scope, asserted so tidying cannot undo it.

    RequireAuthMiddleware answers 403 with the scope it wanted. Setting required_scopes here
    would hand it scope denial and turn the server into a map of its own tool surface.
    """
    server = build_server(issuer)
    assert server.settings.auth is not None
    assert server.settings.auth.required_scopes is None


def test_dns_rebinding_protection_is_configured_rather_than_switched_off(
    issuer: Issuer,
) -> None:
    """The SDK fails closed with an empty allowlist. The fix must not be to disable it."""
    assert TransportSecuritySettings().enable_dns_rebinding_protection is True
    assert TransportSecuritySettings.model_fields["allowed_hosts"].default_factory is not None

    # The settings do not live on `app.user_middleware`. They are held by the session manager
    # inside the /mcp route, which is worth knowing before asserting against the wrong object.
    app = build_app(issuer)
    route = next(r for r in app.routes if getattr(r, "path", None) == "/mcp")
    settings = route.app.app.session_manager.security_settings  # type: ignore[attr-defined]

    assert settings is not None, "no transport security is configured at all"
    assert settings.enable_dns_rebinding_protection is True, "it must not be switched off"
    assert settings.allowed_hosts == ALLOWED_HOSTS
    assert TransportSecurityMiddleware is not None


async def test_a_host_outside_the_allowlist_is_refused(issuer: Issuer) -> None:
    """And the allowlist is not decoration: an unexpected Host gets 421."""
    async with serving(issuer, base_url="http://somewhere-else.invalid") as elsewhere:
        response = await elsewhere.post(
            "/mcp",
            json=INITIALIZE,
            headers={
                **HEADERS,
                "Authorization": (
                    f"Bearer {issuer.mint(client_id='agent-alpha', scopes=['rates:read'])}"
                ),
            },
        )
    assert response.status_code == 421


async def test_the_protected_resource_metadata_names_this_resource(issuer: Issuer) -> None:
    """RFC 9728, which is how a client discovers which audience to ask its issuer for."""
    async with serving(issuer) as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    metadata = json.loads(response.text)
    assert metadata["resource"] == RESOURCE
    assert metadata["authorization_servers"], "a client needs somewhere to go for a token"
