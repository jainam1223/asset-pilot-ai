"""Pagination metadata attached to `success_response()` via `meta.pagination`
for any endpoint that returns a page of a larger collection."""

from __future__ import annotations

from pydantic import BaseModel


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int

    @classmethod
    def create(cls, *, page: int, page_size: int, total_items: int) -> "PaginationMeta":
        total_pages = -(-total_items // page_size) if page_size else 0
        return cls(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
        )
