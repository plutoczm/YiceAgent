"""SupportOps-owned tenant identifier; no authentication or implicit tenant fallback."""

import re

_TENANT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class TenantId(str):
    def __new__(cls, value: object):
        if not isinstance(value, str) or _TENANT_ID.fullmatch(value) is None:
            raise ValueError("invalid_tenant_id")
        return str.__new__(cls, value)


DEMO_TENANT = TenantId("TENANT-DEMO")
