from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import jwt
from pydantic import SecretStr

from backend.app.application.identity.errors import (
    AuthenticationFailedError,
    IdentityProviderUnavailableError,
)
from backend.app.application.identity.models import AuthenticatedPrincipal


class PyJWTJwksIdentityProvider:
    """Verify bounded RS256 bearer tokens against one configured issuer and JWKS."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        clock_skew_seconds: int = 30,
        max_token_bytes: int = 8192,
        jwks_cache_seconds: int = 300,
        jwks_timeout_seconds: float = 2.0,
        allowed_algorithms: tuple[str, ...] = ("RS256",),
        client: jwt.PyJWKClient | None = None,
        clock: Callable[[], datetime] | None = None,
        require_https: bool = False,
    ) -> None:
        parsed_url = urlparse(jwks_url)
        if (
            parsed_url.scheme not in {"https", "http"}
            or not parsed_url.netloc
            or parsed_url.username
            or parsed_url.password
            or parsed_url.fragment
            or (require_https and parsed_url.scheme != "https")
        ):
            raise ValueError("JWKS URL must be a trusted HTTP(S) URL")
        if not issuer or not audience:
            raise ValueError("issuer and audience are required")
        if allowed_algorithms != ("RS256",):
            raise ValueError("the configured JWKS verifier supports RS256 only")
        if not 0 <= clock_skew_seconds <= 60:
            raise ValueError("clock skew must be between 0 and 60 seconds")
        if not 512 <= max_token_bytes <= 16_384:
            raise ValueError("maximum bearer token size must be between 512 and 16384 bytes")
        if not 1 <= jwks_cache_seconds <= 3600:
            raise ValueError("JWKS cache lifetime must be between 1 and 3600 seconds")
        if not 0.1 <= jwks_timeout_seconds <= 5:
            raise ValueError("JWKS timeout must be between 0.1 and 5 seconds")

        self._issuer = issuer
        self._audience = audience
        self._max_token_bytes = max_token_bytes
        self._clock_skew = timedelta(seconds=clock_skew_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._jwks_client = client or jwt.PyJWKClient(
            jwks_url,
            cache_keys=False,
            cache_jwk_set=True,
            lifespan=jwks_cache_seconds,
            timeout=jwks_timeout_seconds,
            cooldown_duration=min(30, jwks_cache_seconds),
        )

    def authenticate_bearer(self, token: SecretStr) -> AuthenticatedPrincipal:
        bearer = token.get_secret_value()
        try:
            if len(bearer.encode("utf-8")) > self._max_token_bytes:
                raise AuthenticationFailedError() from None
            header = jwt.get_unverified_header(bearer)
            if (
                header.get("alg") != "RS256"
                or not isinstance(header.get("kid"), str)
                or not header["kid"]
                or len(header["kid"]) > 128
                or header.get("crit")
                or header.get("b64") is False
            ):
                raise AuthenticationFailedError() from None
            signing_key = self._jwks_client.get_signing_key_from_jwt(bearer)
            claims = jwt.decode(
                bearer,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._clock_skew,
                options={
                    "require": ["exp", "iss", "sub", "aud"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.PyJWKClientConnectionError:
            raise IdentityProviderUnavailableError() from None
        except (jwt.PyJWKSetError, jwt.PyJWKError):
            raise IdentityProviderUnavailableError() from None
        except AuthenticationFailedError:
            raise
        except jwt.PyJWTError:
            raise AuthenticationFailedError() from None
        except (TypeError, ValueError, UnicodeError):
            raise AuthenticationFailedError() from None
        except OSError:
            raise IdentityProviderUnavailableError() from None

        subject = claims.get("sub")
        issuer = claims.get("iss")
        audience_claim = claims.get("aud")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationFailedError()
        if issuer != self._issuer:
            raise AuthenticationFailedError()
        if isinstance(audience_claim, str):
            audiences = (audience_claim,)
        elif isinstance(audience_claim, list) and all(
            isinstance(item, str) for item in audience_claim
        ):
            audiences = tuple(audience_claim)
        else:
            raise AuthenticationFailedError()
        try:
            return AuthenticatedPrincipal(
                issuer=issuer,
                subject=subject,
                audience=audiences,
                authenticated_at=self._clock(),
            )
        except ValueError:
            raise AuthenticationFailedError() from None
