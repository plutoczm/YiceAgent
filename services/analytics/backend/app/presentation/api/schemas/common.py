from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict] | dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str | None = None


class DeleteResponse(BaseModel):
    deleted: bool
    document_id: str
