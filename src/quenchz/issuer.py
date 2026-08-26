"""A local authorization server, so the audience proof has something to prove against.

This exists only so the resource server has real tokens to reject. It is not an identity
provider and this repository claims no integration with one.

THE SIGNING KEY IS GENERATED IN MEMORY AT STARTUP AND NEVER TOUCHES DISK. That is not
laziness about key management, it is the only defensible option: a private key committed to
a public repository is a private key on the internet, and labelling it a demo does not change
what a scanner finds or what a reader thinks of the author.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

__all__ = ["ISSUER", "RESOURCE", "SOMEBODY_ELSE", "Issuer"]

ISSUER = "https://issuer.quenchz.invalid"

# This resource server's own identifier, and what a token must be minted FOR. The `.invalid`
# TLD is reserved by RFC 2606 and can never resolve, which is the point: nothing here is a
# real endpoint and nobody can be tricked into thinking it is.
RESOURCE = "https://quenchz.invalid/mcp"

# A second resource served by the same trusted issuer. Every audience test needs somewhere
# else for a perfectly valid token to have been minted for.
SOMEBODY_ELSE = "https://elsewhere.invalid/mcp"


@dataclass(slots=True)
class Issuer:
    """Mints RS256 access tokens. One instance, one keypair, both in memory."""

    _key: rsa.RSAPrivateKey = field(
        default_factory=lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )

    @property
    def public_key_pem(self) -> bytes:
        return self._key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    @property
    def _private_key_pem(self) -> bytes:
        return self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def mint(
        self,
        *,
        client_id: str,
        scopes: list[str],
        audience: str | list[str] = RESOURCE,
        lifetime: datetime.timedelta = datetime.timedelta(minutes=15),
        issuer: str = ISSUER,
        include_audience: bool = True,
    ) -> str:
        """Mint a token. Every argument that can be made wrong on purpose is a parameter.

        `audience` accepts a list and `include_audience` can drop the claim entirely, because
        a resource server is only as good as the malformed tokens it has actually been shown.
        """
        now = datetime.datetime.now(datetime.UTC)
        claims: dict[str, object] = {
            "iss": issuer,
            "sub": client_id,
            "iat": int(now.timestamp()),
            "exp": int((now + lifetime).timestamp()),
            "jti": uuid.uuid4().hex,
            "scope": " ".join(scopes),
        }
        if include_audience:
            claims["aud"] = audience
        return jwt.encode(claims, self._private_key_pem, algorithm="RS256")
