"""
db.py — Postgres connection, role lookup, and read-only SELECT execution.

Fully async (psycopg.AsyncConnection) so this drops straight into a
FastAPI route without blocking the event loop. The AI service does not
own the schema (the backend team does) and never writes to it — every
connection opened here is set read-only at the transaction level as a
second wall behind the SQL-shape checks in sql_check.py.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import psycopg
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# Without these, a slow/unreachable Postgres endpoint hangs the calling
# request indefinitely (observed: a 2-minute stall during testing with
# no timeout configured). CONNECT_TIMEOUT bounds the initial TCP/auth
# handshake; STATEMENT_TIMEOUT_MS bounds how long any single query can
# run once connected — both matter equally for a FastAPI route that
# must not tie up a worker forever on a DB hiccup.
CONNECT_TIMEOUT_SECONDS = 10
STATEMENT_TIMEOUT_MS = 15_000


class UnknownUserError(PermissionError):
    """Raised when a lookup email doesn't match any row in `user`."""


class QueryExecutionError(RuntimeError):
    """Wraps a psycopg error raised while executing a validated SELECT."""


class DatabaseConnectionError(RuntimeError):
    """Wraps a connection-level failure (unreachable, timed out, refused)."""


@asynccontextmanager
async def get_connection() -> AsyncIterator[psycopg.AsyncConnection]:
    """Open a read-only async connection to POSTGRES_URL.

    Read-only is enforced at the transaction level so any SELECT that
    somehow smuggled a write (a CTE with a DML clause, for example)
    fails at the database instead of silently succeeding. Both the
    connection attempt and every statement on it are time-bounded so a
    slow/unreachable DB can never hang the caller indefinitely.
    """
    url = os.getenv("POSTGRES_URL")
    if not url:
        raise RuntimeError("POSTGRES_URL is not set in .env")

    try:
        conn = await psycopg.AsyncConnection.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    except (psycopg.OperationalError, TimeoutError) as exc:
        logger.error(f"Could not connect to Postgres within {CONNECT_TIMEOUT_SECONDS}s: {exc}")
        raise DatabaseConnectionError(str(exc)) from exc

    try:
        await conn.execute("SET TRANSACTION READ ONLY")
        await conn.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
        yield conn
    finally:
        await conn.close()


async def lookup_user_role(
    email: str, *, conn: psycopg.AsyncConnection | None = None
) -> tuple[str, str]:
    """Look up a user's role and id by email.

    Returns (role, user_id). Raises UnknownUserError if no row matches.
    """

    async def _query(c: psycopg.AsyncConnection) -> tuple[str, str]:
        cur = await c.execute('SELECT id, role FROM "user" WHERE email = %s', (email,))
        row = await cur.fetchone()
        if row is None:
            raise UnknownUserError(f"No user found with email {email!r}")
        user_id, role = row
        return str(role), str(user_id)

    if conn is not None:
        return await _query(conn)

    async with get_connection() as c:
        return await _query(c)


async def execute_select(
    sql: str,
    *,
    conn: psycopg.AsyncConnection | None = None,
) -> list[dict[str, Any]]:
    """Run a validated SELECT and return rows as a list of dicts.

    Caller is responsible for having already passed `sql` through
    sql_check.check_shape() — this function trusts its input.
    """

    async def _run(c: psycopg.AsyncConnection) -> list[dict[str, Any]]:
        try:
            cur = await c.execute(sql)
            columns = [d.name for d in cur.description] if cur.description else []
            rows = await cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except psycopg.Error as exc:
            raise QueryExecutionError(str(exc)) from exc

    if conn is not None:
        return await _run(conn)

    async with get_connection() as c:
        return await _run(c)
