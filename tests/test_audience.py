"""Audience validation is the gate. Signature validation is only the doorbell.

The token in `test_a_perfectly_valid_token_for_another_resource_reaches_nothing` is correctly
signed by the trusted issuer, unexpired, and carries the right scope. Every check a careless
resource server performs, it passes. It is refused on the one check that matters.
"""

from __future__ import annotations

import datetime
import inspect

import jwt
import pytest
from mcp.server.auth.middleware import bearer_auth

from quenchz.issuer import ISSUER, RESOURCE, SOMEBODY_ELSE, Issuer
from quenchz.tokens import AudienceRestrictedVerifier


@pytest.fixture
def issuer() -> Issuer:
    return Issuer()


@pytest.fixture
def verifier(issuer: Issuer) -> AudienceRestrictedVerifier:
    return AudienceRestrictedVerifier(issuer.public_key_pem)


async def test_a_token_minted_for_this_resource_is_accepted(
    issuer: Issuer, verifier: AudienceRestrictedVerifier
) -> None:
    token = issuer.mint(client_id="agent-a", scopes=["rates:read"])
    accepted = await verifier.verify_token(token)
    assert accepted is not None
    assert accepted.client_id == "agent-a"
    assert accepted.scopes == ["rates:read"]
    assert accepted.resource == RESOURCE


async def test_a_perfectly_valid_token_for_another_resource_reaches_nothing(
    issuer: Issuer, verifier: AudienceRestrictedVerifier
) -> None:
    """The headline. Right issuer, right signature, right scope, unexpired, wrong door."""
    token = issuer.mint(client_id="agent-a", scopes=["rates:read"], audience=SOMEBODY_ELSE)

    # Everything a careless server checks, this token passes.
    unverified = jwt.decode(token, options={"verify_signature": False})
    assert unverified["iss"] == ISSUER
    assert unverified["scope"] == "rates:read"
    assert unverified["exp"] > datetime.datetime.now(datetime.UTC).timestamp()
    jwt.decode(token, issuer.public_key_pem, algorithms=["RS256"], audience=SOMEBODY_ELSE)

    assert await verifier.verify_token(token) is None
    assert "Audience doesn't match" in str(verifier.last_refusal)


async def test_a_token_good_at_two_doors_is_refused_at_this_one(
    issuer: Issuer, verifier: AudienceRestrictedVerifier
) -> None:
    """The named decision, and the one the library would let through.

    PyJWT treats `aud` as a membership test, so a list containing this resource is accepted by
    the library. It is refused here: a token also valid elsewhere is one that elsewhere can
    replay, which is the situation RFC 8707 exists to avoid.
    """
    token = issuer.mint(
        client_id="agent-a", scopes=["rates:read"], audience=[RESOURCE, SOMEBODY_ELSE]
    )

    # The library accepts it. That is the behaviour being overridden, asserted rather than
    # described, so this test fails loudly if PyJWT ever changes its mind.
    claims = jwt.decode(token, issuer.public_key_pem, algorithms=["RS256"], audience=RESOURCE)
    assert claims["aud"] == [RESOURCE, SOMEBODY_ELSE]

    assert await verifier.verify_token(token) is None
    assert "not a single resource" in str(verifier.last_refusal)


async def test_a_token_with_no_audience_is_a_token_for_nobody(
    issuer: Issuer, verifier: AudienceRestrictedVerifier
) -> None:
    token = issuer.mint(client_id="agent-a", scopes=["rates:read"], include_audience=False)
    assert await verifier.verify_token(token) is None
    assert "aud" in str(verifier.last_refusal)


async def test_an_expired_token_is_refused(
    issuer: Issuer, verifier: AudienceRestrictedVerifier
) -> None:
    token = issuer.mint(
        client_id="agent-a", scopes=["rates:read"], lifetime=-datetime.timedelta(seconds=1)
    )
    assert await verifier.verify_token(token) is None
    assert "Expired" in str(verifier.last_refusal)


async def test_a_token_from_an_untrusted_issuer_is_refused(
    issuer: Issuer, verifier: AudienceRestrictedVerifier
) -> None:
    token = issuer.mint(client_id="agent-a", scopes=["rates:read"], issuer="https://evil.invalid")
    assert await verifier.verify_token(token) is None


async def test_a_token_signed_by_a_different_key_is_refused(
    verifier: AudienceRestrictedVerifier,
) -> None:
    """A second issuer with a real, valid keypair. The claims are perfect; the key is not."""
    impostor = Issuer()
    token = impostor.mint(client_id="agent-a", scopes=["rates:read"])
    assert await verifier.verify_token(token) is None


async def test_an_unsigned_token_is_refused(
    issuer: Issuer, verifier: AudienceRestrictedVerifier
) -> None:
    """The classic algorithm-confusion attempt, refused because RS256 is pinned."""
    forged = jwt.encode(
        {
            "iss": ISSUER,
            "aud": RESOURCE,
            "sub": "agent-a",
            "scope": "rates:read series:list",
            "iat": int(datetime.datetime.now(datetime.UTC).timestamp()),
            "exp": int(datetime.datetime.now(datetime.UTC).timestamp()) + 3600,
        },
        key="",
        algorithm="none",
    )
    assert await verifier.verify_token(forged) is None


def test_the_sdk_carries_the_resource_field_and_never_checks_it() -> None:
    """The reason this module has to exist, asserted against the installed SDK.

    If a future release starts enforcing the audience itself, this test fails and the
    repository's central claim has to be rewritten rather than quietly becoming untrue.
    """
    from mcp.server.auth.provider import AccessToken

    assert "resource" in AccessToken.model_fields, "RFC 8707 field is carried"

    source = inspect.getsource(bearer_auth.BearerAuthBackend)
    assert "resource" not in source, "the SDK now inspects resource; the claim needs rewriting"
    assert "expires_at" in source, "it does check expiry, which is the point of the contrast"


def test_the_sdk_names_the_scope_it_denies() -> None:
    """The information leak the dispatch design exists to avoid, quoted from the SDK."""
    source = inspect.getsource(bearer_auth.RequireAuthMiddleware)
    assert 'f"Required scope: {required_scope}"' in source


@pytest.mark.parametrize("omitted", ["sub", "exp", "iat", "aud", "iss"])
async def test_a_token_missing_any_required_claim_is_refused_and_does_not_crash(
    issuer: Issuer, verifier: AudienceRestrictedVerifier, omitted: str
) -> None:
    """Every entry of the require list, one test each, so none of them is decoration.

    Two of these are not belt and braces. Drop `sub` or `exp` from the list and PyJWT accepts
    the token, after which this module's own dictionary access raises an uncaught KeyError.
    That is a crash inside a verifier, which is a worse outcome than a refusal, so the
    assertion is specifically that a refusal comes back rather than an exception escaping.
    """
    now = int(datetime.datetime.now(datetime.UTC).timestamp())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": "agent-a",
        "iat": now,
        "exp": now + 3600,
        "scope": "rates:read",
    }
    del claims[omitted]
    token = jwt.encode(claims, issuer._private_key_pem, algorithm="RS256")

    assert await verifier.verify_token(token) is None
    assert omitted in str(verifier.last_refusal) or "Audience" in str(verifier.last_refusal)
