from datetime import UTC, datetime, timedelta
import time
import urllib.request

import jwt
import pytest
from pydantic import SecretStr

from backend.app.application.identity.errors import (
    AuthenticationFailedError,
    IdentityProviderUnavailableError,
)
from backend.app.infrastructure.identity.pyjwt_jwks_provider import (
    PyJWTJwksIdentityProvider,
)
from tests.identity_test_support import (
    AUDIENCE,
    ISSUER,
    LocalJwksServer,
    create_rsa_key,
    public_jwk,
    private_pem,
    signed_token,
)


@pytest.fixture
def local_only_network(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})


@pytest.fixture
def jwks_server(local_only_network):
    key = create_rsa_key()
    server = LocalJwksServer([public_jwk(key, "key-1")])
    try:
        yield server, key
    finally:
        server.close()


def provider(server: LocalJwksServer, **kwargs) -> PyJWTJwksIdentityProvider:
    return PyJWTJwksIdentityProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=server.url,
        **kwargs,
    )


def test_valid_rsa_signature_returns_only_validated_identity_fields(jwks_server) -> None:
    server, key = jwks_server
    token = signed_token(key, claims={"tenant_id": "tenant-b", "roles": ["admin"]})

    principal = provider(server).authenticate_bearer(SecretStr(token))

    assert principal.issuer == ISSUER
    assert principal.subject == "user-a"
    assert principal.audience == (AUDIENCE,)
    assert principal.authenticated_at.tzinfo == UTC
    assert "tenant_id" not in principal.model_dump()
    assert "roles" not in principal.model_dump()
    assert "token" not in principal.model_dump()


def test_multi_audience_claim_retains_validated_audience_values(jwks_server) -> None:
    server, key = jwks_server
    token = signed_token(key, claims={"aud": [AUDIENCE, "another-client"]})

    principal = provider(server).authenticate_bearer(SecretStr(token))

    assert principal.audience == (AUDIENCE, "another-client")


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 1},
        {"nbf": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())},
        {"iss": "https://wrong-issuer.test/"},
        {"aud": "some-other-api"},
        {"sub": ""},
        {"exp": None},
    ],
)
def test_invalid_claims_fail_closed(jwks_server, claims) -> None:
    server, key = jwks_server
    token = signed_token(key, claims=claims)

    with pytest.raises(AuthenticationFailedError):
        provider(server).authenticate_bearer(SecretStr(token))


def test_invalid_signature_fails_closed(jwks_server) -> None:
    server, _ = jwks_server
    wrong_key = create_rsa_key()

    with pytest.raises(AuthenticationFailedError):
        provider(server).authenticate_bearer(SecretStr(signed_token(wrong_key)))


def test_unsupported_algorithm_is_rejected_before_jwks_lookup(jwks_server) -> None:
    server, key = jwks_server
    token = signed_token(key, algorithm="HS256")

    with pytest.raises(AuthenticationFailedError):
        provider(server).authenticate_bearer(SecretStr(token))
    assert server.request_count == 0


@pytest.mark.parametrize(
    "headers",
    [
        {"kid": ""},
        {"kid": "key-1", "crit": ["b64"]},
        {"kid": "key-1", "b64": False},
        {"kid": "x" * 129},
    ],
)
def test_unsupported_or_malformed_headers_fail_before_jwks_lookup(
    jwks_server,
    headers,
) -> None:
    server, key = jwks_server
    token = jwt.encode(
        {
            "iss": ISSUER,
            "sub": "user-a",
            "aud": AUDIENCE,
            "exp": int(datetime.now(UTC).timestamp()) + 300,
        },
        private_pem(key),
        algorithm="RS256",
        headers=headers,
    )

    with pytest.raises(AuthenticationFailedError):
        provider(server).authenticate_bearer(SecretStr(token))
    assert server.request_count == 0


@pytest.mark.parametrize("token", ["not-a-jwt", "", "a.b.c"])
def test_malformed_tokens_fail_closed(jwks_server, token: str) -> None:
    server, _ = jwks_server

    with pytest.raises(AuthenticationFailedError):
        provider(server).authenticate_bearer(SecretStr(token))


def test_invalid_utf8_token_is_rejected_without_exception_details(jwks_server) -> None:
    server, _key = jwks_server

    with pytest.raises(AuthenticationFailedError):
        provider(server).authenticate_bearer(SecretStr("\ud800"))


def test_oversized_token_is_rejected_before_header_parsing(jwks_server) -> None:
    server, key = jwks_server
    token = signed_token(key) + ("x" * 9000)

    with pytest.raises(AuthenticationFailedError):
        provider(server).authenticate_bearer(SecretStr(token))
    assert server.request_count == 0


def test_missing_required_claims_are_rejected(jwks_server) -> None:
    server, key = jwks_server
    token = signed_token(key, claims={"exp": None})

    with pytest.raises(AuthenticationFailedError):
        provider(server).authenticate_bearer(SecretStr(token))


@pytest.mark.parametrize("missing", ["iss", "sub", "aud", "exp"])
def test_each_required_claim_must_be_present(jwks_server, missing: str) -> None:
    server, key = jwks_server
    claims = {
        "iss": ISSUER,
        "sub": "user-a",
        "aud": AUDIENCE,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int(datetime.now(UTC).timestamp()) + 300,
    }
    del claims[missing]
    token = jwt.encode(
        claims,
        private_pem(key),
        algorithm="RS256",
        headers={"kid": "key-1"},
    )

    with pytest.raises(AuthenticationFailedError):
        provider(server).authenticate_bearer(SecretStr(token))


def test_alg_none_is_rejected_before_jwks_lookup(jwks_server) -> None:
    server, _key = jwks_server
    token = jwt.encode(
        {"iss": ISSUER, "sub": "user-a", "aud": AUDIENCE},
        key=None,
        algorithm="none",
        headers={"kid": "key-1"},
    )

    with pytest.raises(AuthenticationFailedError):
        provider(server).authenticate_bearer(SecretStr(token))
    assert server.request_count == 0


def test_jwks_unknown_kid_refreshes_for_key_rotation(jwks_server) -> None:
    server, first_key = jwks_server
    verifier = provider(server, jwks_cache_seconds=1)
    verifier.authenticate_bearer(SecretStr(signed_token(first_key, kid="key-1")))
    assert server.request_count == 1

    rotated_key = create_rsa_key()
    server.replace_keys([public_jwk(rotated_key, "key-2")])
    time.sleep(1.05)
    principal = verifier.authenticate_bearer(
        SecretStr(signed_token(rotated_key, kid="key-2"))
    )

    assert principal.subject == "user-a"
    assert server.request_count == 2


def test_unknown_kid_fails_closed_after_refresh(jwks_server) -> None:
    server, key = jwks_server
    verifier = provider(server, jwks_cache_seconds=1)
    verifier.authenticate_bearer(SecretStr(signed_token(key, kid="key-1")))
    time.sleep(1.05)
    token = signed_token(key, kid="unknown-kid")

    with pytest.raises(AuthenticationFailedError):
        verifier.authenticate_bearer(SecretStr(token))
    assert server.request_count == 2


def test_malformed_jwks_fails_closed_as_provider_unavailable(jwks_server) -> None:
    server, key = jwks_server
    server.replace_keys([])

    with pytest.raises(IdentityProviderUnavailableError):
        provider(server).authenticate_bearer(SecretStr(signed_token(key)))


def test_jwks_network_failure_is_unavailable_not_anonymous(local_only_network) -> None:
    key = create_rsa_key()
    verifier = PyJWTJwksIdentityProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url="http://127.0.0.1:1/.well-known/jwks.json",
        jwks_timeout_seconds=0.5,
    )

    with pytest.raises(IdentityProviderUnavailableError):
        verifier.authenticate_bearer(SecretStr(signed_token(key)))


@pytest.mark.parametrize(
    "overrides",
    [
        {"jwks_url": "ftp://issuer.test/jwks"},
        {"jwks_url": "https://user:pass@issuer.test/jwks"},
        {"jwks_url": "https://issuer.test/jwks#fragment"},
        {"issuer": ""},
        {"audience": ""},
        {"allowed_algorithms": ("HS256",)},
        {"clock_skew_seconds": 61},
        {"max_token_bytes": 16_385},
        {"jwks_cache_seconds": 3601},
        {"jwks_timeout_seconds": 5.1},
        {"jwks_url": "http://issuer.test/jwks", "require_https": True},
    ],
)
def test_invalid_verifier_configuration_is_rejected(jwks_server, overrides) -> None:
    server, _key = jwks_server
    values = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_url": server.url,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        PyJWTJwksIdentityProvider(**values)


def test_unexpected_jwks_os_error_is_provider_unavailable(jwks_server) -> None:
    _server, key = jwks_server

    class BrokenClient:
        def get_signing_key_from_jwt(self, token: str):
            raise OSError("private transport detail")

    verifier = PyJWTJwksIdentityProvider(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url="https://issuer.test/jwks",
        client=BrokenClient(),
    )
    with pytest.raises(IdentityProviderUnavailableError):
        verifier.authenticate_bearer(SecretStr(signed_token(key)))
