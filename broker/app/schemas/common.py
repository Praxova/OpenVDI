"""Shared schema types: envelope, error shape, pagination."""
from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """Error body per API-3.

    `details` is populated only for role=admin callers; the API handler
    decides whether to pass through or drop it per API-3-b.
    """

    code: str
    message: str
    details: dict[str, Any] | None = None


class APIResponse(BaseModel, Generic[T]):
    """Standard response envelope. Exactly one of `data` / `error` is set."""

    data: T | None = None
    error: ErrorDetail | None = None


class ErrorResponse(BaseModel):
    """Pure-error response shape (for FastAPI response_model declarations)."""

    error: ErrorDetail


class PaginationParams(BaseModel):
    """Base for list-endpoint query params. Used via Depends(PaginationParams).

    `sort` is a free string in M2; the service layer decides which columns
    it accepts. `order` defaults to ascending.
    """

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)
    sort: str | None = None
    order: Literal["asc", "desc"] = "asc"
