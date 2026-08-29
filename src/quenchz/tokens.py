"""Audience validation, which is the gate. Signature validation is only the doorbell.

WHAT THE SDK DOES, READ FROM THE INSTALLED SOURCE RATHER THAN ASSUMED. `AccessToken` carries
a `resource` field for RFC 8707, and `BearerAuthBackend.authenticate` checks three things: the
`bearer ` prefix, that the verifier returned something truthy, and `expires_at`. It then hands
`resource` through untouched. Grep the server tree for a comparison against it and there is
none. The SDK does tell the ISSUER to "audience-restrict the issued access token to the
resource named in the ID-JAG's `resource` claim", and says nothing anywhere about the resource
server checking that it was. So the check has to live here or it does not exist.

THE ONE NAMED DECISION: A MULTI-AUDIENCE TOKEN IS REFUSED. PyJWT accepts a token whose `aud`
is a list, provided this resource appears somewhere in it. That is right by RFC 7519, where
audience is a membership test. It is wrong for this server. A token minted for both this
resource and another is simultaneously valid at the other one, so whoever holds that other
server can replay it here, and RFC 8707 exists precisely so a client can ask for a token
narrowed to one resource. This server therefore requires `aud` to be a single string equal to
its own identifier. The difference is one line and it is the difference between "somebody was
allowed to talk to something" and "somebody was allowed to talk to me".
"""

from __future__ import annotations

import jwt
from mcp.server.auth.provider import AccessToken

from quenchz.issuer import ISSUER, RESOURCE

__all__ = ["AudienceRestrictedVerifier", "Refusal"]


class Refusal(str):
    """Why a token was refused. Never returned to a caller; only ever logged and tested."""


class AudienceRestrictedVerifier:
    """An `mcp.server.auth.provider.TokenVerifier` that actually checks the audience."""

    def __init__(
        self, public_key: bytes, *, resource: str = RESOURCE, issuer: str = ISSUER
    ) -> None:
        self._public_key = public_key
        self._resource = resource
        self._issuer = issuer
        self.last_refusal: Refusal | None = None

    async def verify_token(self, token: str) -> AccessToken | None:
        self.last_refusal = None
        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=["RS256"],
                audience=self._resource,
                issuer=self._issuer,
                # WHICH OF THESE ARE LOAD-BEARING, measured by removing them one at a time
                # rather than reasoned about:
                #
                #   sub, exp, iat   removing any one turns a test red
                #   aud, iss        removing either changes nothing, because `audience=` and
                #                   `issuer=` above enforce them independently
                #
                # `sub` and `exp` are the sharp ones: without them PyJWT accepts a token that
                # omits the claim and the dictionary access below then raises an uncaught
                # KeyError, which is a crash rather than a refusal. `iat` is hygiene, an access
                # token that will not say when it was minted cannot be reasoned about.
                #
                # `aud` and `iss` stay because the redundancy is the point: they would become
                # the only enforcement the day somebody removes the two arguments above, and
                # `test_the_belt_and_the_braces_are_both_real` asserts that both paths refuse.
                options={"require": ["aud", "exp", "iat", "iss", "sub"]},
            )
        except (jwt.InvalidTokenError, TypeError, ValueError) as refused:
            # TypeError AND ValueError, NOT ONLY InvalidTokenError, and the two extras are a
            # measured defect rather than defensive padding. PyJWT compares `exp`, `iat` and
            # `nbf` numerically, so a token carrying one of them as a JSON list or object raises
            # TypeError from int() deep inside the library. That escaped this method, escaped
            # BearerAuthBackend.authenticate and escaped Starlette's AuthenticationMiddleware,
            # so a malformed claim reached the caller as a 500 with a traceback instead of a 401.
            #
            # The token still has to carry a valid RS256 signature from the trusted issuer to
            # get this far, so this is a robustness defect rather than an unauthenticated crash.
            # A verifier that can be made to raise is still a verifier that answers something
            # other than yes or no.
            self.last_refusal = Refusal(f"{type(refused).__name__}: {refused}")
            return None

        audience = claims["aud"]
        if not isinstance(audience, str):
            # Reached only for a token that is otherwise entirely valid and names this
            # resource. See the module docstring: a token good at two doors is one the other
            # door can present here.
            self.last_refusal = Refusal(f"audience is not a single resource: {audience!r}")
            return None

        return AccessToken(
            token=token,
            client_id=str(claims["sub"]),
            scopes=str(claims.get("scope", "")).split(),
            expires_at=int(claims["exp"]),
            resource=audience,
        )
