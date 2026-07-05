"""
db.py — Postgres connection, role lookup, and read-only SELECT execution.

Fully async (psycopg.AsyncConnection) so this drops straight into a
FastAPI route without blocking the event loop. The AI service does not
own the schema (the backend team does) and never writes to it — every
connection opened here is set read-only at the transaction level as a
second wall behind the SQL-shape checks in sql_check.py.
"""

from __future__ import annotations

import asyncio
import os
import time
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
# must not tie up a worker forever on a DB hiccup. Kept short: if the
# DB is actually down, every second here is a second every concurrent
# request is stuck waiting — the circuit breaker below is what protects
# against a sustained outage, not a longer timeout on each attempt.
CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 15_000

# Circuit breaker: once a connection attempt fails, stop trying fresh
# connections for this long and fail fast with the transient message
# instead. Without this, a real Postgres outage means every single
# concurrent request independently pays the full CONNECT_TIMEOUT_SECONDS
# — this mirrors the cooldown pattern in ai_service/providers.py, just
# for the one DB target instead of a chain of LLM providers.
DB_COOLDOWN_SECONDS = 30.0

# Module-level, not per-request: this state is shared across every
# caller in the process, which is the point — one failed connection
# attempt should stop the *next* caller from immediately re-trying too.
_cooldown_until = 0.0

# Guards the actual connection attempt below (not the whole request —
# each caller still gets and uses its own connection object once one
# succeeds). Without this, N truly-concurrent requests arriving before
# the cooldown is set would each independently start a slow attempt and
# each pay the full CONNECT_TIMEOUT_SECONDS before any of them has had
# a chance to set the cooldown for the others. With the lock, only the
# first caller actually dials out; everyone else queued behind the lock
# re-checks cooldown state immediately after — since the first caller
# just set it — and fails fast instead of dialing out themselves.
_connect_lock = asyncio.Lock()


def _db_cooling_down() -> bool:
    return time.monotonic() < _cooldown_until


def _db_cooldown_remaining() -> float:
    return max(0.0, _cooldown_until - time.monotonic())


def _start_db_cooldown() -> None:
    global _cooldown_until
    _cooldown_until = time.monotonic() + DB_COOLDOWN_SECONDS


def _clear_db_cooldown() -> None:
    global _cooldown_until
    _cooldown_until = 0.0


class UnknownUserError(PermissionError):
    """Raised when a lookup email doesn't match any row in `user`."""


class QueryExecutionError(RuntimeError):
    """Wraps a psycopg error raised while executing a validated SELECT."""


class DatabaseConnectionError(RuntimeError):
    """Wraps a connection-level failure (unreachable, timed out, refused),
    including a fast-fail while the circuit breaker is cooling down."""


def _raise_cooling_down() -> None:
    remaining = _db_cooldown_remaining()
    logger.debug(f"DB connection skipped — cooling down for {remaining:.0f}s more")
    raise DatabaseConnectionError(
        f"Skipping connection attempt — database was unreachable "
        f"{DB_COOLDOWN_SECONDS - remaining:.0f}s ago, retrying in {remaining:.0f}s"
    )


async def _connect() -> psycopg.AsyncConnection:
    """Make (or skip) exactly one real connection attempt, serialized
    across concurrent callers via _connect_lock.

    Concurrent requests that arrive while one attempt is already in
    flight queue on the lock rather than each starting their own dial-out.
    By the time a queued caller acquires the lock, either the first
    caller succeeded (cooldown still clear — this caller dials out too,
    now against a confirmed-live DB) or failed (cooldown now set — this
    caller fails fast without touching the network at all).
    """
    if _db_cooling_down():
        _raise_cooling_down()

    url = os.getenv("POSTGRES_URL")
    if not url:
        raise RuntimeError("POSTGRES_URL is not set in .env")

    async with _connect_lock:
        # Re-check: another caller may have just set the cooldown while
        # we were waiting for the lock.
        if _db_cooling_down():
            _raise_cooling_down()

        try:
            conn = await psycopg.AsyncConnection.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS)
        except (psycopg.OperationalError, TimeoutError) as exc:
            logger.error(f"Could not connect to Postgres within {CONNECT_TIMEOUT_SECONDS}s: {exc}")
            _start_db_cooldown()
            raise DatabaseConnectionError(str(exc)) from exc

        _clear_db_cooldown()  # a successful connection means the DB is back
        return conn


@asynccontextmanager
async def get_connection() -> AsyncIterator[psycopg.AsyncConnection]:
    """Open a read-only async connection to POSTGRES_URL.

    Read-only is enforced at the transaction level so any SELECT that
    somehow smuggled a write (a CTE with a DML clause, for example)
    fails at the database instead of silently succeeding. Both the
    connection attempt and every statement on it are time-bounded so a
    slow/unreachable DB can never hang the caller indefinitely.

    If a recent connection attempt failed, this fails immediately
    (no network round-trip at all) until DB_COOLDOWN_SECONDS has
    passed — see the circuit breaker constants above. Concurrent
    callers during an outage coalesce onto one real attempt instead of
    each independently paying the connect timeout — see _connect().
    """
    conn = await _connect()

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
