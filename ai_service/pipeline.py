"""
pipeline.py — orchestrates the full NL question -> Markdown answer flow.

    generate SQL -> check_shape (SELECT-only + role scope) -> execute
    -> synthesize a Markdown answer from the rows

This is the single entry point other services (a FastAPI route, the
CLI in nlsql.py) should call. It never surfaces raw SQL, table names,
or exception internals to the end user — `AskResult.answer` is always
the thing to show them, and it is always drawn from the fixed,
human-facing messages in ai_service.errors. `sql` and `refusal_reason`
are carried on the result for logging/debugging only.

Fully async: every step (LLM calls, DB query) awaits, so this is safe
to call directly from a FastAPI route handler without blocking the
event loop.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from ai_service.db import execute_select
from ai_service.errors import EMPTY_QUERY_MESSAGE, user_facing_message
from ai_service.formatting import format_rows
from ai_service.models import SQLResponse
from ai_service.providers import Provider, generate_sql as generate_sql_via_chain
from ai_service.sql_check import check_shape
from ai_service.synthesis import synthesize_answer


def _extract_caveat(explanation: str) -> str | None:
    """Pull a caveat out of the SQL-gen explanation, if present.

    The SQL-gen prompt is instructed to prefix explanation with the
    literal word "Note:" only when part of a mixed request was
    declined (see sql_generation.py's MIXED requests rule) — a routine
    explanation never gets this prefix, so this is a precise check,
    not a fuzzy keyword search.
    """
    if explanation.strip().lower().startswith("note:"):
        return explanation.strip()
    return None


@dataclass
class AskResult:
    answer: str  # Markdown, always safe to show the end user
    sql: str | None  # for logs/debugging only — never render to the end user
    row_count: int | None
    refused: bool
    refusal_reason: str | None  # dev-facing detail, for logs only
    sql_provider: str | None
    answer_provider: str | None


def _refused(answer: str, *, sql: str | None, reason: str, sql_provider: str | None) -> AskResult:
    """Build a refused AskResult. Small helper so every early-return
    below stays a one-liner instead of repeating all seven fields."""
    return AskResult(
        answer=answer,
        sql=sql,
        row_count=None,
        refused=True,
        refusal_reason=reason,
        sql_provider=sql_provider,
        answer_provider=None,
    )


async def ask(
    question: str,
    *,
    chain: list[Provider],
    schema_text: str,
    role: str,
) -> AskResult:
    """Run the full pipeline for one question. Never raises — every
    failure mode (refusal, bad scope, execution error, LLM outage)
    becomes a refused=True result with a human-readable answer.

    `schema_text` must already be sliced for `role` (see
    ai_service.schema.build_role_schemas, called once at app startup) —
    this function does not re-slice it, since the sliced output is the
    same for every call with this role and shouldn't be recomputed
    per request.
    """
    if not question.strip():
        # Deterministic, no LLM call spent on a question that isn't one —
        # every role gets the exact same fixed wording instead of the
        # model free-writing its own (and inconsistent) refusal.
        return _refused(EMPTY_QUERY_MESSAGE, sql=None, reason="empty question", sql_provider=None)

    try:
        data, sql_provider = await generate_sql_via_chain(question, schema_text, role, chain=chain)
        result = SQLResponse.model_validate(data)
    except Exception as exc:
        logger.error(f"SQL generation failed: {exc}")
        return _refused(user_facing_message(exc), sql=None, reason=str(exc), sql_provider=None)

    if result.sql is None:
        # The model itself declined (destructive ask, out of scope, off-topic,
        # a document/file request, an instruction-override attempt) — its
        # own explanation is already written for a human, in plain English,
        # so it's used as-is rather than routed through user_facing_message().
        return _refused(result.explanation, sql=None, reason=result.explanation, sql_provider=sql_provider)

    try:
        check_shape(result.sql, role=role)
    except PermissionError as exc:
        logger.warning(f"Scope violation for role={role}: {exc}")
        return _refused(user_facing_message(exc), sql=result.sql, reason=str(exc), sql_provider=sql_provider)

    try:
        rows = await execute_select(result.sql)
    except Exception as exc:
        logger.error(f"Query execution failed: {exc}")
        return _refused(user_facing_message(exc), sql=result.sql, reason=str(exc), sql_provider=sql_provider)

    caveat = _extract_caveat(result.explanation)

    try:
        answer, answer_provider = await synthesize_answer(question, rows, chain=chain, caveat=caveat)
    except Exception as exc:
        # Synthesis failed but we do have real data — fall back to a
        # plain table rather than surfacing raw SQL or an error to the user.
        logger.warning(f"Answer synthesis failed, falling back to raw table: {exc}")
        answer = format_rows(rows)
        answer_provider = None

    return AskResult(
        answer=answer,
        sql=result.sql,
        row_count=len(rows),
        refused=False,
        refusal_reason=None,
        sql_provider=sql_provider,
        answer_provider=answer_provider,
    )
