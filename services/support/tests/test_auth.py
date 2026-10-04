from time import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import AuthenticationError, AuthService
from app.config import Settings


def test_dev_auth_requires_explicit_principal():
    auth = AuthService(
        Settings(
            auth_mode="dev", app_env="development", mcp_auth_mode="disabled", seed_demo_data=True
        )
    )
    with pytest.raises(AuthenticationError, match="x-principal-id"):
        auth.authenticate(
            authorization=None,
            dev_principal_id=None,
            dev_customer_id="CUST-001",
            dev_roles="customer",
        )


def test_dev_auth_parses_customer_and_roles():
    auth = AuthService(
        Settings(
            auth_mode="dev", app_env="development", mcp_auth_mode="disabled", seed_demo_data=True
        )
    )
    principal = auth.authenticate(
        authorization=None,
        dev_principal_id="user-1",
        dev_customer_id="CUST-001",
        dev_roles="customer,support_agent",
    )
    assert principal.customer_id == "CUST-001"
    assert "support_agent" in principal.roles


def test_jwt_auth_verifies_signature_issuer_audience_and_subject():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    settings = Settings(
        auth_mode="jwt",
        auth_issuer="https://issuer.example",
        auth_audience="supportops-api",
        auth_jwks_url="https://issuer.example/jwks.json",
        auth_algorithm="RS256",
        app_env="development",
        mcp_auth_mode="disabled",
        seed_demo_data=True,
    )
    auth = AuthService(settings)
    auth._jwks_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=public_key)
    )
    now = int(time())
    token = jwt.encode(
        {
            "sub": "user-42",
            "tenant_id": "TENANT-DEMO",
            "customer_id": "CUST-001",
            "roles": ["customer"],
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "iat": now,
            "exp": now + 300,
        },
        private_key,
        algorithm="RS256",
    )
    principal = auth.authenticate(
        authorization=f"Bearer {token}",
        dev_principal_id=None,
        dev_customer_id=None,
        dev_roles=None,
    )
    assert principal.subject == "user-42"
    assert principal.customer_id == "CUST-001"


def test_jwt_auth_rejects_missing_bearer():
    auth = AuthService(
        Settings(
            auth_mode="jwt",
            auth_issuer="https://issuer.example",
            auth_audience="supportops-api",
            auth_jwks_url="https://issuer.example/jwks.json",
            app_env="development",
            mcp_auth_mode="disabled",
            seed_demo_data=True,
        )
    )
    with pytest.raises(AuthenticationError, match="bearer_token_required"):
        auth.authenticate(
            authorization=None,
            dev_principal_id=None,
            dev_customer_id=None,
            dev_roles=None,
        )
