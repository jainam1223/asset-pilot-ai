"""models.py — response shapes shared across the NL→SQL pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SQLResponse(BaseModel):
    sql: str | None = Field(
        default=None,
        description="A single PostgreSQL SELECT statement, or null if disallowed.",
    )
    explanation: str = Field(
        ...,
        description="1-2 sentence description of what the query returns.",
    )
