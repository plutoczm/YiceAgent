from backend.app.application.business_analytics.errors import (
    BusinessAnalyticsError,
    BusinessCatalogMismatchError,
    BusinessCatalogResolutionError,
)


class BusinessContractLoadError(BusinessAnalyticsError):
    code = "business_contract_load_failed"
    safe_message = "Accepted business contract artifact could not be read"


class BusinessContractIntegrityError(BusinessAnalyticsError):
    code = "business_contract_integrity_failed"
    safe_message = "Accepted business contract artifact failed integrity verification"


class BusinessContractIdentityError(BusinessCatalogMismatchError):
    code = "business_contract_identity_mismatch"
    safe_message = "Accepted business contract identity does not match its pin"


class BusinessContractFormatError(BusinessCatalogResolutionError):
    code = "business_contract_format_invalid"
    safe_message = "Accepted business contract artifact has an invalid format"
