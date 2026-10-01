"""Fixed P4 service calls with distinct credentials and closed responses."""

import asyncio
from typing import Any

import httpx2
from pydantic import TypeAdapter, ValidationError

from app.adapters.analytics_dto import ExportView, ReportView
from app.adapters.supportops import DispatchState, DomainFailure
from app.demo.identity import Identity
from app.workflows.models import AnalyticsReport, Error, Payload, RefreshSnapshot


class AnalyticsAdapter:
    """Call only the configured local publisher and template report API."""

    def __init__(
        self, support_tokens: dict[str, str], report_tokens: dict[str, str], transport: Any = None
    ):
        """Keep per-service credentials and disable ambient proxies and redirects."""
        self.support_tokens = support_tokens
        self.report_tokens = report_tokens
        self.client = httpx2.AsyncClient(
            timeout=30, trust_env=False, follow_redirects=False, transport=transport
        )

    async def close(self) -> None:
        """Release the owned connection pool."""
        await self.client.aclose()

    async def invoke(
        self, payload: Payload, identity: Identity, *, dispatch: DispatchState | None = None
    ) -> Any:
        """Perform one publication or one read; never replay business writes."""
        if not identity.operator:
            raise DomainFailure(Error.AUTHORIZATION)
        model: type[ExportView] | type[ReportView]
        if isinstance(payload, RefreshSnapshot):
            url, tokens, body, model = (
                "http://127.0.0.1:8011/internal/demo/exports",
                self.support_tokens,
                {},
                ExportView,
            )
        elif isinstance(payload, AnalyticsReport):
            url, tokens, body, model = (
                "http://127.0.0.1:8012/internal/demo/reports",
                self.report_tokens,
                {"report_id": payload.report_id},
                ReportView,
            )
        else:
            raise DomainFailure(Error.REJECTED)
        dispatch = dispatch if dispatch is not None else DispatchState()
        try:
            # These calls do not mutate operational business state.
            response = await asyncio.wait_for(
                self.client.post(
                    url, json=body, headers={"Authorization": "Bearer " + tokens[identity.name]}
                ),
                timeout=30,
            )
        except (httpx2.TimeoutException, TimeoutError):
            raise DomainFailure(Error.TIMEOUT) from None
        except httpx2.RequestError:
            raise DomainFailure(Error.UNAVAILABLE) from None
        if response.status_code in {401, 403}:
            raise DomainFailure(Error.AUTHORIZATION)
        if response.status_code >= 500:
            raise DomainFailure(Error.UNAVAILABLE)
        if response.status_code >= 300:
            raise DomainFailure(Error.REJECTED)
        try:
            result = TypeAdapter(model).validate_python(response.json())
        except (ValueError, ValidationError):
            raise DomainFailure(Error.INVALID_RESPONSE) from None
        dispatch.outcome_known = True
        dispatch.result = result
        return result
