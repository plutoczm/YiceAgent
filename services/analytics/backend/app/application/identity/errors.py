class IdentityError(Exception):
    """Base class for safe identity-boundary failures."""


class AuthenticationFailedError(IdentityError):
    """The supplied credential could not be verified."""


class IdentityProviderUnavailableError(IdentityError):
    """The configured identity provider could not be reached safely."""


class TenantAccessDeniedError(IdentityError):
    """The authenticated principal has no configured analytics grant."""


class IdentityConfigurationError(IdentityError):
    """Trusted identity or tenant grant configuration is invalid."""
