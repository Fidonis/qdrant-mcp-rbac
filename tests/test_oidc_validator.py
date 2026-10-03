"""JWKS-backed OIDC token validation."""
from __future__ import annotations

import time

import pytest
from fakes.oidc import ADMIN_ROLE, AUDIENCE, ISSUER, FakeIssuer

from auth.oidc import InvalidTokenError, OIDCValidator


@pytest.fixture
def issuer() -> FakeIssuer:
    return FakeIssuer()


def _validator(issuer: FakeIssuer) -> OIDCValidator:
    return OIDCValidator(ISSUER, AUDIENCE, transport=issuer.transport())


# --- accepted tokens ---------------------------------------------------------


async def test_valid_token_yields_claims(issuer: FakeIssuer) -> None:
    token = issuer.token(sub="alice", roles=[ADMIN_ROLE])
    claims = await _validator(issuer).validate(token)
    assert claims.sub == "alice"
    assert claims.all_roles == [ADMIN_ROLE]


async def test_client_roles_are_collected(issuer: FakeIssuer) -> None:
    token = issuer.token(
        roles=["realm-role"],
        client_roles={"librechat": ["client-role"], "other": ["second-role"]},
    )
    claims = await _validator(issuer).validate(token)
    assert claims.realm_roles == ["realm-role"]
    assert claims.client_roles == ["client-role", "second-role"]


async def test_optional_profile_claims_are_extracted(issuer: FakeIssuer) -> None:
    token = issuer.token(extra={"email": "a@example.test", "preferred_username": "alice"})
    claims = await _validator(issuer).validate(token)
    assert claims.email == "a@example.test"
    assert claims.preferred_username == "alice"


async def test_audience_list_containing_ours_is_accepted(issuer: FakeIssuer) -> None:
    token = issuer.token(extra={"aud": [AUDIENCE, "account"]})
    claims = await _validator(issuer).validate(token)
    assert claims.sub == "user-1"


async def test_issued_at_slightly_in_the_future_is_accepted(issuer: FakeIssuer) -> None:
    # Clock skew between the identity provider and this server must not turn a
    # freshly issued token away. `iat` is informational; `exp` is the bound.
    token = issuer.token(extra={"iat": int(time.time()) + 120})
    claims = await _validator(issuer).validate(token)
    assert claims.sub == "user-1"


async def test_jwk_without_alg_uses_the_key_type_fallback(issuer: FakeIssuer) -> None:
    # `alg` is optional in RFC 7517. The validator derives RS256 from the key
    # type rather than trusting the token header.
    issuer.omit_alg = True
    claims = await _validator(issuer).validate(issuer.token(sub="carol"))
    assert claims.sub == "carol"


# --- rejected tokens ---------------------------------------------------------


async def test_expired_token_rejected(issuer: FakeIssuer) -> None:
    with pytest.raises(InvalidTokenError, match="expired"):
        await _validator(issuer).validate(issuer.token(expires_in=-60))


async def test_token_without_expiry_rejected(issuer: FakeIssuer) -> None:
    # An unexpiring token is a standing key; `verify_exp` alone would let it
    # through, because it only checks an expiry that is actually present.
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(issuer.token(expires_in=None))


async def test_token_without_audience_rejected(issuer: FakeIssuer) -> None:
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(issuer.token(omit=["aud"]))


async def test_token_not_yet_valid_rejected(issuer: FakeIssuer) -> None:
    token = issuer.token(extra={"nbf": int(time.time()) + 300})
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(token)


async def test_wrong_audience_rejected(issuer: FakeIssuer) -> None:
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(issuer.token(audience="some-other-client"))


async def test_audience_list_without_ours_rejected(issuer: FakeIssuer) -> None:
    token = issuer.token(extra={"aud": ["account", "some-other-client"]})
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(token)


async def test_wrong_issuer_rejected(issuer: FakeIssuer) -> None:
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(issuer.token(issuer="https://evil.test"))


async def test_token_without_issuer_rejected(issuer: FakeIssuer) -> None:
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(issuer.token(omit=["iss"]))


async def test_token_signed_by_another_key_rejected(issuer: FakeIssuer) -> None:
    # Same `kid`, different key material: only the signature check can catch it.
    forged = FakeIssuer(kid=issuer.kid).token()
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(forged)


async def test_tampered_payload_rejected(issuer: FakeIssuer) -> None:
    header, _payload, signature = issuer.token(sub="alice").split(".")
    _, other_payload, _ = issuer.token(sub="mallory", roles=[ADMIN_ROLE]).split(".")
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(f"{header}.{other_payload}.{signature}")


async def test_malformed_token_rejected(issuer: FakeIssuer) -> None:
    with pytest.raises(InvalidTokenError, match="Malformed"):
        await _validator(issuer).validate("not-a-jwt")


async def test_token_without_kid_rejected(issuer: FakeIssuer) -> None:
    with pytest.raises(InvalidTokenError, match="kid"):
        await _validator(issuer).validate(issuer.token(include_kid=False))


async def test_token_without_subject_rejected(issuer: FakeIssuer) -> None:
    with pytest.raises(InvalidTokenError, match="sub"):
        await _validator(issuer).validate(issuer.token(omit=["sub"]))


async def test_malformed_role_claims_rejected(issuer: FakeIssuer) -> None:
    token = issuer.token(extra={"realm_access": "not-an-object"})
    with pytest.raises(InvalidTokenError, match="role claims"):
        await _validator(issuer).validate(token)


# --- key selection and algorithm handling ------------------------------------


async def test_unknown_kid_rejected_after_one_refresh(issuer: FakeIssuer) -> None:
    validator = _validator(issuer)
    await validator.validate(issuer.token())  # warm the caches
    calls_before = issuer.jwks_calls
    with pytest.raises(InvalidTokenError, match="Signing key"):
        await validator.validate(issuer.token(kid="rotated-away"))
    # Exactly one forced refresh, not an unbounded retry loop.
    assert issuer.jwks_calls == calls_before + 1


async def test_rotated_key_is_picked_up_by_the_refresh(issuer: FakeIssuer) -> None:
    validator = _validator(issuer)
    await validator.validate(issuer.token())
    # The provider rotates: the same material is now served under a new kid.
    issuer.extra_jwks = [issuer.public_jwk(kid="key-2")]
    claims = await validator.validate(issuer.token(kid="key-2"))
    assert claims.sub == "user-1"


async def test_encryption_only_key_is_skipped(issuer: FakeIssuer) -> None:
    issuer.extra_jwks = [issuer.public_jwk(kid="enc-key", use="enc")]
    with pytest.raises(InvalidTokenError, match="Signing key"):
        await _validator(issuer).validate(issuer.token(kid="enc-key"))


async def test_key_without_use_is_usable(issuer: FakeIssuer) -> None:
    # `use` is optional in RFC 7517; only an explicit non-"sig" value excludes a key.
    issuer.extra_jwks = [issuer.public_jwk(kid="no-use", use=None)]
    claims = await _validator(issuer).validate(issuer.token(kid="no-use"))
    assert claims.sub == "user-1"


async def test_hs256_confusion_is_refused(issuer: FakeIssuer) -> None:
    # The classic confusion attack: sign with HMAC using the published public
    # key as the shared secret. Deriving the algorithm from the JWK (RS256)
    # rather than from the token header makes it fail.
    public_key_material = issuer.public_jwk()["n"]
    forged = issuer.token(algorithm="HS256", key=public_key_material)
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(forged)


async def test_unsigned_token_is_refused(issuer: FakeIssuer) -> None:
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(issuer.token(algorithm="none"))


async def test_jwk_advertising_a_symmetric_algorithm_is_refused(issuer: FakeIssuer) -> None:
    issuer.extra_jwks = [{"kty": "oct", "kid": "hmac-key", "alg": "HS256", "k": "c2VjcmV0"}]
    with pytest.raises(InvalidTokenError, match="not permitted"):
        await _validator(issuer).validate(issuer.token(kid="hmac-key"))


@pytest.mark.parametrize(
    "jwk",
    [
        {"kty": "RSA", "alg": "RS256", "n": "AAAA", "e": "AQAB"},
        {"kty": "RSA", "alg": "RS256", "e": "AQAB"},
        {"kty": "EC", "alg": "ES256", "crv": "P-384", "x": "AAAA", "y": "AAAA"},
        {"kty": "EC", "alg": "ES256", "crv": "P-256", "x": "AAAA", "y": "AAAA"},
    ],
    ids=["tiny-modulus", "missing-modulus", "wrong-curve", "off-curve-point"],
)
async def test_unusable_key_material_is_a_rejection(
    issuer: FakeIssuer, jwk: dict[str, str]
) -> None:
    # A JWK that cannot be turned into a key must end as InvalidTokenError,
    # not as an unhandled error out of the key library.
    issuer.extra_jwks = [{**jwk, "kid": "broken"}]
    with pytest.raises(InvalidTokenError):
        await _validator(issuer).validate(issuer.token(kid="broken"))


async def test_jwk_of_unsupported_key_type_is_refused(issuer: FakeIssuer) -> None:
    issuer.extra_jwks = [{"kty": "OKP", "kid": "okp-key", "crv": "Ed25519", "x": "AAAA"}]
    with pytest.raises(InvalidTokenError, match="key type"):
        await _validator(issuer).validate(issuer.token(kid="okp-key"))


# --- caching -----------------------------------------------------------------


async def test_caches_avoid_refetching(issuer: FakeIssuer) -> None:
    validator = _validator(issuer)
    for _ in range(3):
        await validator.validate(issuer.token())
    assert issuer.discovery_calls == 1
    assert issuer.jwks_calls == 1
